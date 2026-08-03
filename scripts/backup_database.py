#!/usr/bin/env python3
"""Create and retain validated online EggBot SQLite backups."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eggbot_db.database import Database


BACKUP_PATTERN = re.compile(r"^eggbot-(?P<stamp>[0-9]{8}T[0-9]{6}Z)\.sqlite3$")


@dataclass(frozen=True)
class BackupResult:
    backup: str
    checksum: str
    bytes: int
    removed: tuple[str, ...]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def managed_backups(directory: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and BACKUP_PATTERN.fullmatch(path.name)
        ),
        key=lambda path: path.name,
        reverse=True,
    )


def prune_backups(directory: Path, keep: int) -> tuple[str, ...]:
    if keep < 1:
        raise ValueError("keep must be at least 1")
    removed = []
    for backup in managed_backups(directory)[keep:]:
        checksum = backup.with_suffix(f"{backup.suffix}.sha256")
        sqlite_companions = (
            Path(f"{backup}-wal"),
            Path(f"{backup}-shm"),
        )
        backup.unlink()
        if checksum.is_file():
            checksum.unlink()
        for companion in sqlite_companions:
            if companion.is_file():
                companion.unlink()
        removed.append(backup.name)
    return tuple(removed)


def create_backup(
    database_path: Path,
    destination: Path,
    *,
    keep: int = 30,
    now: datetime | None = None,
) -> BackupResult:
    if keep < 1:
        raise ValueError("keep must be at least 1")
    database_path = database_path.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if not database_path.is_file():
        raise FileNotFoundError(f"database does not exist: {database_path}")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.chmod(0o700)
    if destination == database_path.parent:
        raise ValueError("backup destination must differ from the database directory")

    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    filename = f"eggbot-{timestamp:%Y%m%dT%H%M%SZ}.sqlite3"
    backup_path = destination / filename
    if backup_path.exists():
        raise FileExistsError(f"backup already exists: {backup_path.name}")

    lock_path = destination / ".eggbot-backup.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        lock_path.chmod(0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        Database(database_path).backup(backup_path)
        checksum = sha256_file(backup_path)
        checksum_path = backup_path.with_suffix(f"{backup_path.suffix}.sha256")
        checksum_path.write_text(
            f"{checksum}  {backup_path.name}\n", encoding="utf-8"
        )
        checksum_path.chmod(0o600)
        removed = prune_backups(destination, keep)

    return BackupResult(
        backup=backup_path.name,
        checksum=checksum,
        bytes=backup_path.stat().st_size,
        removed=removed,
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--database",
        type=Path,
        default=os.environ.get("EGGBOT_DB_PATH"),
        required=os.environ.get("EGGBOT_DB_PATH") is None,
        help="SQLite database path (default: EGGBOT_DB_PATH)",
    )
    result.add_argument(
        "--destination",
        type=Path,
        default=os.environ.get("EGGBOT_BACKUP_DIR"),
        required=os.environ.get("EGGBOT_BACKUP_DIR") is None,
        help="Protected backup directory (default: EGGBOT_BACKUP_DIR)",
    )
    result.add_argument(
        "--keep",
        type=int,
        default=30,
        help="Number of newest backup generations to retain (default: 30)",
    )
    return result


def main() -> int:
    arguments = parser().parse_args()
    result = create_backup(
        arguments.database,
        arguments.destination,
        keep=arguments.keep,
    )
    print(json.dumps(asdict(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
