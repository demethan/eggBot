"""Repository interfaces keep SQL out of Discord commands and API clients."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Optional

from .secrets import SecretBox
from .database import utc_now


@dataclass(frozen=True)
class ServerCapabilities:
    authentication: str = "unknown"
    metadata_read: str = "unknown"
    whitelist_read: str = "unknown"
    whitelist_write: str = "unknown"

    def status_for(self, capability: str) -> str:
        return getattr(self, capability, "unknown")


@dataclass(frozen=True)
class Server:
    id: int
    name: str
    endpoint: str
    api_user: Optional[str]
    api_password: Optional[str] = field(repr=False)
    api_token: Optional[str] = field(repr=False)
    enabled: bool
    capabilities: ServerCapabilities = field(default_factory=ServerCapabilities)


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
            """
            SELECT s.*,
                   c.authentication AS capability_authentication,
                   c.metadata_read AS capability_metadata_read,
                   c.whitelist_read AS capability_whitelist_read,
                   c.whitelist_write AS capability_whitelist_write
            FROM servers AS s
            LEFT JOIN server_capabilities AS c ON c.server_id = s.id
            WHERE s.name = ? COLLATE NOCASE
            """,
            (name,),
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
            capabilities=ServerCapabilities(
                authentication=row["capability_authentication"] or "unknown",
                metadata_read=row["capability_metadata_read"] or "unknown",
                whitelist_read=row["capability_whitelist_read"] or "unknown",
                whitelist_write=row["capability_whitelist_write"] or "unknown",
            ),
        )

    def list_enabled(self) -> list[Server]:
        names = self.connection.execute(
            "SELECT name FROM servers WHERE enabled = 1 ORDER BY name COLLATE NOCASE"
        ).fetchall()
        servers = []
        for row in names:
            server = self.get_by_name(row["name"])
            if server is not None:
                servers.append(server)
        return servers

    def update_token(self, server_id: int, token: str) -> None:
        self.connection.execute(
            """
            UPDATE servers
            SET api_token_encrypted = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ?
            """,
            (self.secrets.encrypt(token), server_id),
        )

    def set_capabilities(
        self,
        server_id: int,
        capabilities: ServerCapabilities,
        checked_at: Optional[str] = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO server_capabilities(
                server_id, authentication, metadata_read,
                whitelist_read, whitelist_write, checked_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(server_id) DO UPDATE SET
                authentication = excluded.authentication,
                metadata_read = excluded.metadata_read,
                whitelist_read = excluded.whitelist_read,
                whitelist_write = excluded.whitelist_write,
                checked_at = excluded.checked_at
            """,
            (
                server_id,
                capabilities.authentication,
                capabilities.metadata_read,
                capabilities.whitelist_read,
                capabilities.whitelist_write,
                checked_at,
            ),
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


class WhitelistAdminActionRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def record_remove(
        self,
        *,
        requested_by_discord_user_id: int,
        minecraft_name: str,
        server_id: int,
        status: str,
        response_message: Optional[str] = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO whitelist_admin_actions(
                requested_by_discord_user_id, minecraft_name, action,
                server_id, status, response_message, attempted_at
            ) VALUES (?, ?, 'remove', ?, ?, ?, ?)
            """,
            (
                requested_by_discord_user_id,
                minecraft_name,
                server_id,
                status,
                response_message,
                utc_now(),
            ),
        )
