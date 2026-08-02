"""Repository interfaces keep SQL out of Discord commands and API clients."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Optional

from .secrets import SecretBox


@dataclass(frozen=True)
class Server:
    id: int
    name: str
    endpoint: str
    api_user: Optional[str]
    api_password: Optional[str]
    api_token: Optional[str]
    enabled: bool


class ServerRepository:
    def __init__(self, connection: sqlite3.Connection, secrets: SecretBox):
        self.connection = connection
        self.secrets = secrets

    def upsert(
        self,
        *,
        name: str,
        endpoint: str,
        api_user: Optional[str] = None,
        api_password: Optional[str] = None,
        api_token: Optional[str] = None,
        enabled: bool = True,
    ) -> int:
        self.connection.execute(
            """
            INSERT INTO servers(
                name, endpoint, api_user, api_password_encrypted,
                api_token_encrypted, enabled
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                endpoint = excluded.endpoint,
                api_user = excluded.api_user,
                api_password_encrypted = excluded.api_password_encrypted,
                api_token_encrypted = excluded.api_token_encrypted,
                enabled = excluded.enabled,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (
                name,
                endpoint.rstrip("/"),
                api_user,
                self.secrets.encrypt(api_password),
                self.secrets.encrypt(api_token),
                int(enabled),
            ),
        )
        row = self.connection.execute(
            "SELECT id FROM servers WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        return int(row["id"])

    def get_by_name(self, name: str) -> Optional[Server]:
        row = self.connection.execute(
            "SELECT * FROM servers WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        if row is None:
            return None
        return Server(
            id=row["id"],
            name=row["name"],
            endpoint=row["endpoint"],
            api_user=row["api_user"],
            api_password=self.secrets.decrypt(row["api_password_encrypted"]),
            api_token=self.secrets.decrypt(row["api_token_encrypted"]),
            enabled=bool(row["enabled"]),
        )


class SettingsRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def set(self, key: str, value: Any) -> None:
        self.connection.execute(
            """
            INSERT INTO settings(key, value_json) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            """,
            (key, json.dumps(value)),
        )

    def get(self, key: str, default: Any = None) -> Any:
        row = self.connection.execute(
            "SELECT value_json FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return default if row is None else json.loads(row["value_json"])
