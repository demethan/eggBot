import hashlib
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from eggbot_db.database import Database
from scripts.backup_database import create_backup, managed_backups


class BackupDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.database_path = self.directory / "runtime" / "eggbot.sqlite3"
        database = Database(self.database_path)
        database.migrate()
        self.connection = database.connect()
        self.connection.execute(
            "INSERT INTO settings(key, value_json) VALUES ('test', '\"wal-value\"')"
        )
        self.connection.commit()
        self.backup_directory = self.directory / "backups"

    def tearDown(self):
        self.connection.close()
        self.temporary.cleanup()

    def test_online_backup_has_integrity_checksum_and_private_permissions(self):
        result = create_backup(
            self.database_path,
            self.backup_directory,
            now=datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc),
        )

        backup = self.backup_directory / result.backup
        checksum = hashlib.sha256(backup.read_bytes()).hexdigest()
        with sqlite3.connect(backup) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(
                connection.execute(
                    "SELECT value_json FROM settings WHERE key = 'test'"
                ).fetchone()[0],
                '"wal-value"',
            )
        self.assertEqual(result.checksum, checksum)
        self.assertEqual(
            backup.with_suffix(".sqlite3.sha256").read_text(encoding="utf-8"),
            f"{checksum}  {backup.name}\n",
        )
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.backup_directory.stat().st_mode & 0o777, 0o700)

    def test_retention_removes_only_old_managed_backup_generations(self):
        start = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        unrelated = self.backup_directory / "do-not-delete.txt"
        self.backup_directory.mkdir()
        unrelated.write_text("keep", encoding="utf-8")
        old_wal = self.backup_directory / "eggbot-20260802T120000Z.sqlite3-wal"
        old_shm = self.backup_directory / "eggbot-20260802T120000Z.sqlite3-shm"
        old_wal.write_bytes(b"old wal")
        old_shm.write_bytes(b"old shm")
        last_result = None
        for offset in range(4):
            last_result = create_backup(
                self.database_path,
                self.backup_directory,
                keep=2,
                now=start + timedelta(days=offset),
            )

        self.assertEqual(len(managed_backups(self.backup_directory)), 2)
        self.assertEqual(last_result.removed, ("eggbot-20260802T120000Z.sqlite3",))
        self.assertTrue(unrelated.exists())
        self.assertFalse(
            (self.backup_directory / "eggbot-20260802T120000Z.sqlite3.sha256").exists()
        )
        self.assertFalse(old_wal.exists())
        self.assertFalse(old_shm.exists())

    def test_rejects_missing_source_same_directory_and_invalid_retention(self):
        with self.assertRaises(FileNotFoundError):
            create_backup(
                self.directory / "missing.sqlite3",
                self.backup_directory,
            )
        with self.assertRaises(ValueError):
            create_backup(self.database_path, self.database_path.parent)
        with self.assertRaises(ValueError):
            create_backup(self.database_path, self.backup_directory, keep=0)


if __name__ == "__main__":
    unittest.main()
