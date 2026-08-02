"""Sanitized integrity validation for an imported EggBot database."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict

from .secrets import SecretBox


COUNT_TABLES = (
    "settings",
    "servers",
    "players",
    "reboot_schedules",
    "legacy_reboot_schedules",
    "legacy_server_player_state",
    "import_runs",
)


def validate_imported_database(
    connection: sqlite3.Connection,
    database_path: Path,
    secrets: SecretBox,
) -> Dict[str, Any]:
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    foreign_key_errors = len(connection.execute("PRAGMA foreign_key_check").fetchall())
    counts = {
        table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in COUNT_TABLES
    }
    migration_versions = [
        row[0]
        for row in connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )
    ]

    encrypted_rows = connection.execute(
        "SELECT api_password_encrypted, api_token_encrypted FROM servers"
    ).fetchall()
    decryptable_passwords = 0
    decryptable_tokens = 0
    plaintext_secrets = []
    for row in encrypted_rows:
        password = secrets.decrypt(row["api_password_encrypted"])
        token = secrets.decrypt(row["api_token_encrypted"])
        if password is not None:
            decryptable_passwords += 1
            plaintext_secrets.append(password.encode("utf-8"))
        if token is not None:
            decryptable_tokens += 1
            plaintext_secrets.append(token.encode("utf-8"))

    database_path = Path(database_path)
    storage_bytes = database_path.read_bytes()
    wal_path = Path(f"{database_path}-wal")
    if wal_path.exists():
        storage_bytes += wal_path.read_bytes()
    plaintext_secret_occurrences = sum(
        1 for secret in plaintext_secrets if secret and secret in storage_bytes
    )

    latest_import = connection.execute(
        """
        SELECT settings_count, server_count, player_count, schedule_count,
               unresolved_schedule_count, legacy_server_player_count
        FROM import_runs ORDER BY imported_at DESC LIMIT 1
        """
    ).fetchone()
    source_counts_match = bool(
        latest_import
        and latest_import["settings_count"] == counts["settings"]
        and latest_import["server_count"] == counts["servers"]
        and latest_import["player_count"] == counts["players"]
        and latest_import["schedule_count"]
        == counts["reboot_schedules"] + counts["legacy_reboot_schedules"]
        and latest_import["unresolved_schedule_count"]
        == counts["legacy_reboot_schedules"]
        and latest_import["legacy_server_player_count"]
        == counts["legacy_server_player_state"]
    )

    valid = (
        integrity == "ok"
        and foreign_key_errors == 0
        and plaintext_secret_occurrences == 0
        and source_counts_match
    )
    return {
        "valid": valid,
        "integrity": integrity,
        "foreign_key_errors": foreign_key_errors,
        "migration_versions": migration_versions,
        "counts": counts,
        "decryptable_passwords": decryptable_passwords,
        "decryptable_tokens": decryptable_tokens,
        "plaintext_secret_occurrences": plaintext_secret_occurrences,
        "source_counts_match": source_counts_match,
    }
