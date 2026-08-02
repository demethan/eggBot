"""Validated, idempotent import of EggBot's legacy data.json."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .database import utc_now
from .repositories import ServerRepository, SettingsRepository
from .secrets import SecretBox


STRUCTURAL_KEYS = {"server_list", "players", "reboot_schedule"}


class ImportValidationError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("legacy JSON validation failed")
        self.errors = errors


@dataclass
class ImportReport:
    source_sha256: str
    source_size: int
    settings: int = 0
    servers: int = 0
    players: int = 0
    schedules: int = 0
    unresolved_schedules: int = 0
    legacy_server_players: int = 0
    already_imported: bool = False
    warnings: list[str] = field(default_factory=list)

    def sanitized(self) -> Dict[str, Any]:
        return asdict(self)


def read_source(path: Path) -> tuple[Dict[str, Any], str, int]:
    raw = Path(path).read_bytes()
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImportValidationError(["source is not valid UTF-8 JSON"]) from exc
    if not isinstance(payload, dict):
        raise ImportValidationError(["top-level JSON value must be an object"])
    return payload, hashlib.sha256(raw).hexdigest(), len(raw)


def validate_payload(payload: Dict[str, Any]) -> list[str]:
    errors = []
    servers = payload.get("server_list")
    players = payload.get("players", {})
    schedules = payload.get("reboot_schedule", {})

    if not isinstance(servers, dict):
        errors.append("server_list must be an object")
        servers = {}
    if not isinstance(players, dict):
        errors.append("players must be an object")
        players = {}
    if not isinstance(schedules, dict):
        errors.append("reboot_schedule must be an object")
        schedules = {}

    for name, server in servers.items():
        prefix = f"server_list[{name!r}]"
        if not isinstance(name, str) or not name.strip():
            errors.append("server names must be non-empty strings")
        if not isinstance(server, dict):
            errors.append(f"{prefix} must be an object")
            continue
        if not isinstance(server.get("endpoint"), str) or not server["endpoint"].strip():
            errors.append(f"{prefix}.endpoint must be a non-empty string")
        for secret_field in ("id", "password", "token"):
            if server.get(secret_field) is not None and not isinstance(server[secret_field], str):
                errors.append(f"{prefix}.{secret_field} must be a string or null")
        server_players = server.get("players", {})
        if server_players is not None and not isinstance(server_players, dict):
            errors.append(f"{prefix}.players must be an object or null")
        elif isinstance(server_players, dict):
            for player_name, state in server_players.items():
                if not isinstance(player_name, str) or not player_name.strip():
                    errors.append(f"{prefix}.players names must be non-empty strings")
                if not isinstance(state, dict):
                    errors.append(f"{prefix}.players[{player_name!r}] must be an object")
                    continue
                if state.get("login_time") is not None and not isinstance(
                    state["login_time"], str
                ):
                    errors.append(
                        f"{prefix}.players[{player_name!r}].login_time must be a string or null"
                    )

    for name, player in players.items():
        if not isinstance(name, str) or not name.strip():
            errors.append("player names must be non-empty strings")
        if not isinstance(player, dict):
            errors.append(f"players[{name!r}] must be an object")
        elif player.get("last_seen") is not None and not isinstance(player["last_seen"], str):
            errors.append(f"players[{name!r}].last_seen must be a string or null")

    for server_name, schedule in schedules.items():
        prefix = f"reboot_schedule[{server_name!r}]"
        if not isinstance(schedule, dict):
            errors.append(f"{prefix} must be an object")
            continue
        for key in ("time", "frequency", "timezone"):
            if not isinstance(schedule.get(key), str) or not schedule[key].strip():
                errors.append(f"{prefix}.{key} must be a non-empty string")
    return errors


def parse_legacy_timestamp(value: Optional[str], source_timezone: ZoneInfo) -> Optional[str]:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        timestamp = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=source_timezone)
    return timestamp.astimezone(timezone.utc).isoformat(timespec="seconds")


class LegacyJsonImporter:
    def __init__(self, connection: sqlite3.Connection, secrets: SecretBox, source_timezone: str):
        self.connection = connection
        self.secrets = secrets
        try:
            self.source_timezone = ZoneInfo(source_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ImportValidationError([f"unknown source timezone: {source_timezone}"]) from exc

    def prepare(self, source_path: Path) -> tuple[Dict[str, Any], ImportReport]:
        payload, source_hash, source_size = read_source(source_path)
        errors = validate_payload(payload)
        if errors:
            raise ImportValidationError(errors)

        legacy_count = sum(
            len(server.get("players") or {})
            for server in payload["server_list"].values()
        )
        report = ImportReport(
            source_sha256=source_hash,
            source_size=source_size,
            settings=len(set(payload) - STRUCTURAL_KEYS),
            servers=len(payload["server_list"]),
            players=len(payload.get("players", {})),
            schedules=len(payload.get("reboot_schedule", {})),
            unresolved_schedules=sum(
                1
                for key in payload.get("reboot_schedule", {})
                if key.casefold()
                not in {server_name.casefold() for server_name in payload["server_list"]}
            ),
            legacy_server_players=legacy_count,
        )
        if report.unresolved_schedules:
            report.warnings.append(
                f"{report.unresolved_schedules} schedules require server mapping"
            )
        return payload, report

    def import_file(self, source_path: Path) -> ImportReport:
        payload, report = self.prepare(source_path)
        existing = self.connection.execute(
            "SELECT 1 FROM import_runs WHERE source_sha256 = ?", (report.source_sha256,)
        ).fetchone()
        if existing:
            report.already_imported = True
            return report

        imported_at = utc_now()
        servers = ServerRepository(self.connection, self.secrets)
        settings = SettingsRepository(self.connection)

        self.connection.execute("BEGIN IMMEDIATE")
        try:
            for key in sorted(set(payload) - STRUCTURAL_KEYS):
                settings.set(key, payload[key])

            server_ids = {}
            for name, server in sorted(payload["server_list"].items()):
                server_ids[name] = servers.upsert(
                    name=name,
                    endpoint=server["endpoint"],
                    api_user=server.get("id"),
                    api_password=server.get("password"),
                    api_token=server.get("token"),
                )
            server_ids_casefold = {
                name.casefold(): server_id for name, server_id in server_ids.items()
            }

            player_records = dict(payload.get("players", {}))
            for server in payload["server_list"].values():
                for name in (server.get("players") or {}):
                    player_records.setdefault(name, {})

            player_ids = {}
            for name, record in sorted(player_records.items(), key=lambda item: item[0].casefold()):
                last_seen_raw = record.get("last_seen") if isinstance(record, dict) else None
                last_seen = parse_legacy_timestamp(last_seen_raw, self.source_timezone)
                if last_seen_raw and last_seen is None:
                    report.warnings.append(f"player {name!r} has an unparsed last_seen value")
                first_seen = last_seen or imported_at
                self.connection.execute(
                    """
                    INSERT INTO players(current_name, first_seen_at, last_seen_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(current_name) DO UPDATE SET
                        last_seen_at = COALESCE(excluded.last_seen_at, players.last_seen_at)
                    """,
                    (name, first_seen, last_seen),
                )
                player_id = self.connection.execute(
                    "SELECT id FROM players WHERE current_name = ? COLLATE NOCASE", (name,)
                ).fetchone()["id"]
                player_ids[name.casefold()] = player_id
                self.connection.execute(
                    """
                    INSERT INTO player_names(player_id, name, first_seen_at, last_seen_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(player_id, name) DO UPDATE SET
                        last_seen_at = COALESCE(excluded.last_seen_at, player_names.last_seen_at)
                    """,
                    (player_id, name, first_seen, last_seen),
                )

            for server_name, schedule in sorted(payload.get("reboot_schedule", {}).items()):
                server_id = server_ids_casefold.get(server_name.casefold())
                if server_id is not None:
                    self.connection.execute(
                        """
                        INSERT INTO reboot_schedules(server_id, time_value, frequency, timezone)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(server_id) DO UPDATE SET
                            time_value = excluded.time_value,
                            frequency = excluded.frequency,
                            timezone = excluded.timezone,
                            updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                        """,
                        (
                            server_id,
                            schedule["time"],
                            schedule["frequency"],
                            schedule["timezone"],
                        ),
                    )

            self.connection.execute(
                """
                INSERT INTO import_runs(
                    source_sha256, source_size, imported_at, settings_count,
                    server_count, player_count, schedule_count, unresolved_schedule_count,
                    legacy_server_player_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.source_sha256,
                    report.source_size,
                    imported_at,
                    report.settings,
                    report.servers,
                    report.players,
                    report.schedules,
                    report.unresolved_schedules,
                    report.legacy_server_players,
                ),
            )

            for schedule_key, schedule in sorted(payload.get("reboot_schedule", {}).items()):
                if schedule_key.casefold() in server_ids_casefold:
                    continue
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO legacy_reboot_schedules(
                        schedule_key, time_value, frequency, timezone,
                        imported_at, source_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        schedule_key,
                        schedule["time"],
                        schedule["frequency"],
                        schedule["timezone"],
                        imported_at,
                        report.source_sha256,
                    ),
                )

            for server_name, server in sorted(payload["server_list"].items()):
                for player_name, state in sorted((server.get("players") or {}).items()):
                    state = state if isinstance(state, dict) else {}
                    duration = state.get("duration")
                    try:
                        duration = float(duration) if duration is not None else None
                    except (TypeError, ValueError):
                        duration = None
                        report.warnings.append(
                            f"server {server_name!r} player {player_name!r} has invalid duration"
                        )
                    self.connection.execute(
                        """
                        INSERT OR IGNORE INTO legacy_server_player_state(
                            server_id, player_id, login_time_raw, duration_minutes,
                            imported_at, source_sha256
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            server_ids[server_name],
                            player_ids[player_name.casefold()],
                            state.get("login_time"),
                            duration,
                            imported_at,
                            report.source_sha256,
                        ),
                    )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        return report
