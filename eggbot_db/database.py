"""SQLite connection, migration, and backup management."""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


MIGRATION_PATTERN = re.compile(r"^(?P<version>[0-9]{3})_(?P<name>[a-z0-9_]+)\.sql$")
DEFAULT_MIGRATIONS = Path(__file__).with_name("migrations")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path, migrations_path: Path = DEFAULT_MIGRATIONS):
        self.path = Path(path)
        self.migrations_path = Path(migrations_path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        if self.path.exists():
            self.path.chmod(0o600)
        return connection

    def migrations(self) -> Iterable[Migration]:
        discovered = []
        for path in self.migrations_path.iterdir():
            match = MIGRATION_PATTERN.fullmatch(path.name)
            if match:
                discovered.append(
                    Migration(
                        version=int(match.group("version")),
                        name=match.group("name"),
                        path=path,
                    )
                )
        return sorted(discovered, key=lambda migration: migration.version)

    def migrate(self) -> list[Migration]:
        applied = []
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                )
                """
            )
            connection.commit()

            for migration in self.migrations():
                exists = connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = ?",
                    (migration.version,),
                ).fetchone()
                if exists:
                    continue

                name = migration.name.replace("'", "''")
                applied_at = utc_now().replace("'", "''")
                script = migration.path.read_text(encoding="utf-8")
                try:
                    connection.executescript(
                        f"""
                        BEGIN IMMEDIATE;
                        {script}
                        INSERT INTO schema_migrations(version, name, applied_at)
                        VALUES ({migration.version}, '{name}', '{applied_at}');
                        COMMIT;
                        """
                    )
                    applied.append(migration)
                except Exception:
                    connection.rollback()
                    raise
        return applied

    def backup(self, destination: Path) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(f"{destination.suffix}.tmp")
        if temporary.exists():
            temporary.unlink()

        source = self.connect()
        target = sqlite3.connect(temporary)
        try:
            source.backup(target)
            integrity = target.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise sqlite3.DatabaseError(f"backup integrity check failed: {integrity}")
        finally:
            target.close()
            source.close()

        temporary.chmod(0o600)
        os.replace(temporary, destination)
        return destination
