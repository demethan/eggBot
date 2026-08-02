"""Parse authenticated server notifications into durable player sessions."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo


PARSER_VERSION = 1
PLAYER_EVENT_PATTERN = re.compile(
    r"^(?P<player>[A-Za-z0-9_]{1,16}) has (?P<action>joined|left) "
    r"(?:the server|the game)\.?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedPlayerEvent:
    event_type: str
    player_name: str


@dataclass(frozen=True)
class TrackingResult:
    status: str
    event_type: Optional[str] = None
    player_name: Optional[str] = None
    server_name: Optional[str] = None


@dataclass(frozen=True)
class WeeklyPlayerHours:
    player_name: str
    total_hours: float
    server_hours: dict[str, float]
    open_sessions: int


@dataclass(frozen=True)
class ReconciliationResult:
    started_sessions: int
    closed_sessions: int
    online_players: int


def parse_player_event(content: str) -> Optional[ParsedPlayerEvent]:
    match = PLAYER_EVENT_PATTERN.fullmatch(content.strip())
    if match is None:
        return None
    action = match.group("action").casefold()
    return ParsedPlayerEvent(
        event_type="join" if action == "joined" else "leave",
        player_name=match.group("player"),
    )


class PlayerTrackingService:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def ingest_discord_message(
        self,
        *,
        discord_message_id: int,
        discord_channel_id: int,
        source_discord_id: int,
        source_name: str,
        occurred_at: datetime,
        raw_content: str,
    ) -> TrackingResult:
        parsed = parse_player_event(raw_content)
        if parsed is None:
            return TrackingResult("ignored")

        server = self.connection.execute(
            """
            SELECT id, name FROM servers
            WHERE enabled = 1 AND name = ? COLLATE NOCASE
            """,
            (source_name.strip(),),
        ).fetchone()
        if server is None:
            return TrackingResult("unknown_server")

        timestamp = occurred_at.astimezone(timezone.utc).isoformat(timespec="seconds")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            cursor = self.connection.execute(
                """
                INSERT OR IGNORE INTO discord_events(
                    discord_message_id, discord_channel_id, source_discord_id,
                    server_id, event_type, player_name, occurred_at,
                    raw_content, parsed_at, parser_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    discord_message_id,
                    discord_channel_id,
                    source_discord_id,
                    server["id"],
                    parsed.event_type,
                    parsed.player_name,
                    timestamp,
                    raw_content,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    PARSER_VERSION,
                ),
            )
            if cursor.rowcount == 0:
                self.connection.rollback()
                return TrackingResult("duplicate")
            event_id = cursor.lastrowid

            player = self.connection.execute(
                "SELECT id FROM players WHERE current_name = ? COLLATE NOCASE",
                (parsed.player_name,),
            ).fetchone()
            if player is None:
                player_id = self.connection.execute(
                    """
                    INSERT INTO players(current_name, first_seen_at, last_seen_at)
                    VALUES (?, ?, ?)
                    """,
                    (parsed.player_name, timestamp, timestamp),
                ).lastrowid
                self.connection.execute(
                    """
                    INSERT INTO player_names(
                        player_id, name, first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (player_id, parsed.player_name, timestamp, timestamp),
                )
            else:
                player_id = player["id"]
                self.connection.execute(
                    "UPDATE players SET last_seen_at = ? WHERE id = ?",
                    (timestamp, player_id),
                )
                self.connection.execute(
                    """
                    UPDATE player_names SET last_seen_at = ?
                    WHERE player_id = ? AND name = ? COLLATE NOCASE
                    """,
                    (timestamp, player_id, parsed.player_name),
                )

            if parsed.event_type == "join":
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO player_sessions(
                        player_id, server_id, started_at, start_source,
                        confidence, start_event_id
                    ) VALUES (?, ?, ?, 'discord', 'exact', ?)
                    """,
                    (player_id, server["id"], timestamp, event_id),
                )
            else:
                self.connection.execute(
                    """
                    UPDATE player_sessions
                    SET ended_at = ?, end_source = 'discord', end_event_id = ?
                    WHERE player_id = ? AND server_id = ? AND ended_at IS NULL
                    """,
                    (timestamp, event_id, player_id, server["id"]),
                )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

        return TrackingResult(
            "recorded",
            parsed.event_type,
            parsed.player_name,
            server["name"],
        )

    def weekly_hours(
        self,
        minecraft_name: Optional[str] = None,
        *,
        now: Optional[datetime] = None,
        timezone_name: str = "America/Montreal",
    ) -> tuple[datetime, datetime, list[WeeklyPlayerHours]]:
        zone = ZoneInfo(timezone_name)
        local_now = (now or datetime.now(timezone.utc)).astimezone(zone)
        week_start_local = local_now.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        week_start_local -= timedelta(days=week_start_local.weekday())
        start_utc = week_start_local.astimezone(timezone.utc)
        end_utc = local_now.astimezone(timezone.utc)
        name_filter = "" if minecraft_name is None else "AND p.current_name = ? COLLATE NOCASE"
        parameters = [
            end_utc.isoformat(timespec="seconds"),
            end_utc.isoformat(timespec="seconds"),
            start_utc.isoformat(timespec="seconds"),
            end_utc.isoformat(timespec="seconds"),
            end_utc.isoformat(timespec="seconds"),
            start_utc.isoformat(timespec="seconds"),
        ]
        if minecraft_name is not None:
            parameters.append(minecraft_name.strip())
        rows = self.connection.execute(
            f"""
            SELECT p.current_name, s.name AS server_name,
                   SUM(
                       (julianday(MIN(COALESCE(ps.ended_at, ?), ?))
                        - julianday(MAX(ps.started_at, ?))) * 24.0
                   ) AS hours,
                   SUM(CASE WHEN ps.ended_at IS NULL THEN 1 ELSE 0 END) AS open_sessions
            FROM player_sessions AS ps
            JOIN players AS p ON p.id = ps.player_id
            JOIN servers AS s ON s.id = ps.server_id
            WHERE ps.started_at < ?
              AND COALESCE(ps.ended_at, ?) > ?
              {name_filter}
            GROUP BY p.id, s.id
            ORDER BY p.current_name COLLATE NOCASE, s.name COLLATE NOCASE
            """,
            parameters,
        ).fetchall()
        players = {}
        for row in rows:
            player = players.setdefault(
                row["current_name"],
                {"total": 0.0, "servers": {}, "open": 0},
            )
            hours = round(max(0.0, float(row["hours"] or 0.0)), 6)
            player["total"] += hours
            player["servers"][row["server_name"]] = hours
            player["open"] += int(row["open_sessions"] or 0)
        result = [
            WeeklyPlayerHours(name, values["total"], values["servers"], values["open"])
            for name, values in players.items()
        ]
        result.sort(key=lambda item: (-item.total_hours, item.player_name.casefold()))
        return week_start_local, local_now, result

    def reconcile_api_snapshot(
        self,
        *,
        server_name: str,
        players_online,
        observed_at: Optional[datetime] = None,
    ) -> ReconciliationResult:
        server = self.connection.execute(
            """
            SELECT id, name FROM servers
            WHERE enabled = 1 AND name = ? COLLATE NOCASE
            """,
            (server_name.strip(),),
        ).fetchone()
        if server is None:
            return ReconciliationResult(0, 0, 0)
        if isinstance(players_online, dict):
            names = {str(name).strip() for name in players_online if str(name).strip()}
            login_times = {
                str(name).strip().casefold(): self._api_login_time(details)
                for name, details in players_online.items()
                if str(name).strip()
            }
        else:
            names = {str(name).strip() for name in (players_online or []) if str(name).strip()}
            login_times = {}
        normalized_names = {name.casefold() for name in names}
        timestamp = (observed_at or datetime.now(timezone.utc)).astimezone(
            timezone.utc
        ).isoformat(timespec="seconds")
        previous = self.connection.execute(
            """
            SELECT raw_json FROM server_observations
            WHERE server_id = ? AND status = 'success'
            ORDER BY observed_at DESC LIMIT 1
            """,
            (server["id"],),
        ).fetchone()
        previous_names = None
        if previous is not None:
            try:
                previous_names = {
                    str(name).casefold()
                    for name in json.loads(previous["raw_json"])["players_online"]
                }
            except (KeyError, TypeError, ValueError):
                previous_names = None

        started = 0
        closed = 0
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                """
                INSERT OR REPLACE INTO server_observations(
                    server_id, observed_at, status, online_count, raw_json
                ) VALUES (?, ?, 'success', ?, ?)
                """,
                (
                    server["id"],
                    timestamp,
                    len(names),
                    json.dumps({"players_online": sorted(names)}),
                ),
            )
            for name in names:
                login_at = login_times.get(name.casefold())
                if login_at is not None and login_at > timestamp:
                    login_at = None
                player = self.connection.execute(
                    "SELECT id FROM players WHERE current_name = ? COLLATE NOCASE",
                    (name,),
                ).fetchone()
                if player is None:
                    player_id = self.connection.execute(
                        """
                        INSERT INTO players(current_name, first_seen_at, last_seen_at)
                        VALUES (?, ?, ?)
                        """,
                        (name, timestamp, timestamp),
                    ).lastrowid
                    self.connection.execute(
                        """
                        INSERT INTO player_names(
                            player_id, name, first_seen_at, last_seen_at
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (player_id, name, timestamp, timestamp),
                    )
                else:
                    player_id = player["id"]
                    self.connection.execute(
                        "UPDATE players SET last_seen_at = ? WHERE id = ?",
                        (timestamp, player_id),
                    )

                open_session = self.connection.execute(
                    """
                    SELECT id, started_at, start_source
                    FROM player_sessions
                    WHERE player_id = ? AND server_id = ? AND ended_at IS NULL
                    """,
                    (player_id, server["id"]),
                ).fetchone()
                if open_session is not None and login_at is not None:
                    if login_at > open_session["started_at"]:
                        self.connection.execute(
                            """
                            UPDATE player_sessions
                            SET ended_at = ?, end_source = 'api',
                                confidence = 'api_derived'
                            WHERE id = ?
                            """,
                            (login_at, open_session["id"]),
                        )
                        closed += 1
                        open_session = None
                    elif (
                        login_at < open_session["started_at"]
                        and open_session["start_source"] == "api"
                    ):
                        self.connection.execute(
                            "UPDATE player_sessions SET started_at = ? WHERE id = ?",
                            (login_at, open_session["id"]),
                        )

                started_at = login_at or timestamp
                cursor = self.connection.execute(
                    """
                    INSERT OR IGNORE INTO player_sessions(
                        player_id, server_id, started_at, start_source, confidence
                    ) VALUES (?, ?, ?, 'api', 'api_derived')
                    """,
                    (player_id, server["id"], started_at),
                )
                started += cursor.rowcount

            if previous_names is not None:
                open_sessions = self.connection.execute(
                    """
                    SELECT ps.id, p.current_name
                    FROM player_sessions AS ps
                    JOIN players AS p ON p.id = ps.player_id
                    WHERE ps.server_id = ? AND ps.ended_at IS NULL
                    """,
                    (server["id"],),
                ).fetchall()
                for session in open_sessions:
                    player_key = session["current_name"].casefold()
                    if (
                        player_key not in normalized_names
                        and player_key not in previous_names
                    ):
                        self.connection.execute(
                            """
                            UPDATE player_sessions
                            SET ended_at = ?, end_source = 'api',
                                confidence = 'api_derived'
                            WHERE id = ?
                            """,
                            (timestamp, session["id"]),
                        )
                        closed += 1
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return ReconciliationResult(started, closed, len(names))

    @staticmethod
    def _api_login_time(details) -> Optional[str]:
        if not isinstance(details, dict):
            return None
        value = details.get("login_time")
        if not isinstance(value, str) or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
