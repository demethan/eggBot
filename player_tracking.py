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


@dataclass(frozen=True)
class ServerStatistics:
    server_name: str
    total_hours: float
    sessions: int
    unique_players: int
    current_pack: Optional[str]
    current_version: Optional[str]
    installed_at: Optional[datetime]


@dataclass(frozen=True)
class PackStatistics:
    server_name: str
    pack_name: str
    version: str
    installed_at: datetime
    removed_at: Optional[datetime]
    installed_hours: float
    played_hours: float
    sessions: int
    unique_players: int
    baseline: bool


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
                installation = self.connection.execute(
                    """
                    SELECT id FROM pack_installations
                    WHERE server_id = ? AND ended_at IS NULL
                    """,
                    (server["id"],),
                ).fetchone()
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO player_sessions(
                        player_id, server_id, pack_installation_id, started_at,
                        start_source, confidence, start_event_id
                    ) VALUES (?, ?, ?, ?, 'discord', 'exact', ?)
                    """,
                    (
                        player_id,
                        server["id"],
                        installation["id"] if installation else None,
                        timestamp,
                        event_id,
                    ),
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
        pack_name: Optional[str] = None,
        pack_version: Optional[str] = None,
        pack_metadata: Optional[dict] = None,
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
        normalized_pack_name = str(pack_name or "").strip()
        normalized_pack_version = str(pack_version or "").strip()
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
            pack_version_id = None
            pack_installation_id = None
            pack_changed = False
            if normalized_pack_name and normalized_pack_version:
                self.connection.execute(
                    "INSERT OR IGNORE INTO packs(name) VALUES (?)",
                    (normalized_pack_name,),
                )
                pack = self.connection.execute(
                    "SELECT id FROM packs WHERE name = ? COLLATE NOCASE",
                    (normalized_pack_name,),
                ).fetchone()
                metadata_json = json.dumps(
                    pack_metadata or {}, sort_keys=True, default=str
                )
                self.connection.execute(
                    """
                    INSERT INTO pack_versions(pack_id, version, metadata_json)
                    VALUES (?, ?, ?)
                    ON CONFLICT(pack_id, version) DO UPDATE
                    SET metadata_json = excluded.metadata_json
                    """,
                    (pack["id"], normalized_pack_version, metadata_json),
                )
                version = self.connection.execute(
                    """
                    SELECT id FROM pack_versions
                    WHERE pack_id = ? AND version = ? COLLATE NOCASE
                    """,
                    (pack["id"], normalized_pack_version),
                ).fetchone()
                pack_version_id = version["id"]
                installation = self.connection.execute(
                    """
                    SELECT id, pack_version_id FROM pack_installations
                    WHERE server_id = ? AND ended_at IS NULL
                    """,
                    (server["id"],),
                ).fetchone()
                if installation is None or installation["pack_version_id"] != pack_version_id:
                    pack_changed = installation is not None
                    if installation is not None:
                        self.connection.execute(
                            """
                            UPDATE pack_installations
                            SET ended_at = ?, end_source = 'api'
                            WHERE id = ?
                            """,
                            (timestamp, installation["id"]),
                        )
                    pack_installation_id = self.connection.execute(
                        """
                        INSERT INTO pack_installations(
                            server_id, pack_version_id, started_at, start_source
                        ) VALUES (?, ?, ?, 'api')
                        """,
                        (server["id"], pack_version_id, timestamp),
                    ).lastrowid
                else:
                    pack_installation_id = installation["id"]

                if pack_changed:
                    cursor = self.connection.execute(
                        """
                        UPDATE player_sessions
                        SET ended_at = ?, end_source = 'api',
                            confidence = 'api_derived'
                        WHERE server_id = ? AND ended_at IS NULL
                        """,
                        (timestamp, server["id"]),
                    )
                    closed += cursor.rowcount
                else:
                    self.connection.execute(
                        """
                        UPDATE player_sessions SET pack_installation_id = ?
                        WHERE server_id = ? AND ended_at IS NULL
                          AND pack_installation_id IS NULL
                        """,
                        (pack_installation_id, server["id"]),
                    )

            self.connection.execute(
                """
                INSERT OR REPLACE INTO server_observations(
                    server_id, observed_at, status, pack_version_id,
                    online_count, raw_json
                ) VALUES (?, ?, 'success', ?, ?, ?)
                """,
                (
                    server["id"],
                    timestamp,
                    pack_version_id,
                    len(names),
                    json.dumps(
                        {
                            "players_online": sorted(names),
                            "pack_name": normalized_pack_name or None,
                            "pack_version": normalized_pack_version or None,
                        },
                        sort_keys=True,
                    ),
                ),
            )
            for name in names:
                login_at = login_times.get(name.casefold())
                if pack_changed or (login_at is not None and login_at > timestamp):
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
                        player_id, server_id, pack_installation_id, started_at,
                        start_source, confidence
                    ) VALUES (?, ?, ?, ?, 'api', 'api_derived')
                    """,
                    (player_id, server["id"], pack_installation_id, started_at),
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

    def server_statistics(
        self,
        server_name: Optional[str] = None,
        *,
        now: Optional[datetime] = None,
    ) -> list[ServerStatistics]:
        current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        current_timestamp = current_time.isoformat(timespec="seconds")
        name_filter = "" if server_name is None else "AND s.name = ? COLLATE NOCASE"
        parameters = [current_timestamp]
        if server_name is not None:
            parameters.append(server_name.strip())
        rows = self.connection.execute(
            f"""
            SELECT s.name,
                   COALESCE(SUM(
                       (julianday(COALESCE(ps.ended_at, ?))
                        - julianday(ps.started_at)) * 24.0
                   ), 0.0) AS total_hours,
                   COUNT(ps.id) AS sessions,
                   COUNT(DISTINCT ps.player_id) AS unique_players,
                   p.name AS pack_name,
                   pv.version AS pack_version,
                   pi.started_at AS installed_at
            FROM servers AS s
            LEFT JOIN player_sessions AS ps ON ps.server_id = s.id
            LEFT JOIN pack_installations AS pi
                   ON pi.server_id = s.id AND pi.ended_at IS NULL
            LEFT JOIN pack_versions AS pv ON pv.id = pi.pack_version_id
            LEFT JOIN packs AS p ON p.id = pv.pack_id
            WHERE s.enabled = 1 {name_filter}
            GROUP BY s.id
            ORDER BY s.name COLLATE NOCASE
            """,
            parameters,
        ).fetchall()
        return [
            ServerStatistics(
                server_name=row["name"],
                total_hours=round(max(0.0, float(row["total_hours"])), 6),
                sessions=int(row["sessions"]),
                unique_players=int(row["unique_players"]),
                current_pack=row["pack_name"],
                current_version=row["pack_version"],
                installed_at=self._stored_datetime(row["installed_at"]),
            )
            for row in rows
        ]

    def pack_statistics(
        self,
        server_name: Optional[str] = None,
        *,
        now: Optional[datetime] = None,
    ) -> list[PackStatistics]:
        current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        current_timestamp = current_time.isoformat(timespec="seconds")
        name_filter = "" if server_name is None else "AND s.name = ? COLLATE NOCASE"
        rows = self.connection.execute(
            f"""
            SELECT s.name AS server_name, p.name AS pack_name, pv.version,
                   pi.started_at, pi.ended_at,
                   CASE WHEN pi.id = (
                       SELECT first_pi.id FROM pack_installations AS first_pi
                       WHERE first_pi.server_id = pi.server_id
                       ORDER BY first_pi.started_at, first_pi.id LIMIT 1
                   ) THEN 1 ELSE 0 END AS baseline,
                   (julianday(COALESCE(pi.ended_at, ?))
                    - julianday(pi.started_at)) * 24.0 AS installed_hours,
                   COALESCE(SUM(
                       (julianday(MIN(COALESCE(ps.ended_at, ?),
                                      COALESCE(pi.ended_at, ?)))
                        - julianday(MAX(ps.started_at, pi.started_at))) * 24.0
                   ), 0.0) AS played_hours,
                   COUNT(ps.id) AS sessions,
                   COUNT(DISTINCT ps.player_id) AS unique_players
            FROM pack_installations AS pi
            JOIN servers AS s ON s.id = pi.server_id
            JOIN pack_versions AS pv ON pv.id = pi.pack_version_id
            JOIN packs AS p ON p.id = pv.pack_id
            LEFT JOIN player_sessions AS ps ON ps.pack_installation_id = pi.id
            WHERE 1 = 1 {name_filter}
            GROUP BY pi.id
            ORDER BY pi.started_at DESC, s.name COLLATE NOCASE
            """,
            [current_timestamp, current_timestamp, current_timestamp]
            + ([] if server_name is None else [server_name.strip()]),
        ).fetchall()
        return [
            PackStatistics(
                server_name=row["server_name"],
                pack_name=row["pack_name"],
                version=row["version"],
                installed_at=self._stored_datetime(row["started_at"]),
                removed_at=self._stored_datetime(row["ended_at"]),
                installed_hours=round(max(0.0, float(row["installed_hours"])), 6),
                played_hours=round(max(0.0, float(row["played_hours"])), 6),
                sessions=int(row["sessions"]),
                unique_players=int(row["unique_players"]),
                baseline=bool(row["baseline"]),
            )
            for row in rows
        ]

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

    @staticmethod
    def _stored_datetime(value: Optional[str]) -> Optional[datetime]:
        return datetime.fromisoformat(value) if value else None
