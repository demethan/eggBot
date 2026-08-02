import sqlite3
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

from eggbot_db.database import Database
from eggbot_db.repositories import ServerRepository, SettingsRepository
from eggbot_db.secrets import SecretBox


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.database = Database(self.directory / "eggbot.sqlite3")

    def tearDown(self):
        self.temporary.cleanup()

    def test_migrations_are_forward_only_and_idempotent(self):
        first = self.database.migrate()
        second = self.database.migrate()

        self.assertEqual([migration.version for migration in first], [1])
        self.assertEqual(second, [])
        with self.database.connect() as connection:
            applied = connection.execute(
                "SELECT version, name FROM schema_migrations"
            ).fetchall()
            self.assertEqual([tuple(row) for row in applied], [(1, "initial_schema")])

    def test_connection_enables_integrity_and_wal_settings(self):
        self.database.migrate()
        with self.database.connect() as connection:
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(self.database.path.stat().st_mode & 0o777, 0o600)

    def test_failed_migration_rolls_back_its_schema_changes(self):
        migrations = self.directory / "migrations"
        migrations.mkdir()
        (migrations / "001_good.sql").write_text(
            "CREATE TABLE stable(id INTEGER PRIMARY KEY);", encoding="utf-8"
        )
        (migrations / "002_bad.sql").write_text(
            "CREATE TABLE transient(id INTEGER); SELECT * FROM missing_table;",
            encoding="utf-8",
        )
        database = Database(self.directory / "failure.sqlite3", migrations)

        with self.assertRaises(sqlite3.OperationalError):
            database.migrate()

        with database.connect() as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertIn("stable", tables)
            self.assertNotIn("transient", tables)
            versions = connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
            self.assertEqual([row[0] for row in versions], [1])

    def test_online_backup_contains_wal_commits(self):
        self.database.migrate()
        with self.database.connect() as connection:
            connection.execute(
                "INSERT INTO settings(key, value_json) VALUES ('answer', '42')"
            )
            connection.commit()

        backup = self.database.backup(self.directory / "backup.sqlite3")
        with sqlite3.connect(backup) as connection:
            value = connection.execute(
                "SELECT value_json FROM settings WHERE key = 'answer'"
            ).fetchone()[0]
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        self.assertEqual(value, "42")
        self.assertEqual(integrity, "ok")
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)

    def test_repositories_encrypt_server_secrets_and_round_trip_settings(self):
        self.database.migrate()
        secret_box = SecretBox(Fernet.generate_key())
        with self.database.connect() as connection:
            servers = ServerRepository(connection, secret_box)
            server_id = servers.upsert(
                name="BACON",
                endpoint="https://bacon.example/",
                api_user="eggbot",
                api_password="password-value",
                api_token="token-value",
            )
            settings = SettingsRepository(connection)
            settings.set("adminChannelID", 12345)
            connection.commit()

            raw = connection.execute(
                "SELECT api_password_encrypted, api_token_encrypted FROM servers WHERE id = ?",
                (server_id,),
            ).fetchone()
            server = servers.get_by_name("bacon")
            setting = settings.get("adminChannelID")

        self.assertNotIn(b"password-value", raw["api_password_encrypted"])
        self.assertNotIn(b"token-value", raw["api_token_encrypted"])
        self.assertEqual(server.api_password, "password-value")
        self.assertEqual(server.api_token, "token-value")
        self.assertEqual(server.endpoint, "https://bacon.example")
        self.assertEqual(setting, 12345)

    def test_schema_enforces_open_session_and_application_invariants(self):
        self.database.migrate()
        with self.database.connect() as connection:
            server_id = connection.execute(
                "INSERT INTO servers(name, endpoint) VALUES ('BACON', 'https://example')"
            ).lastrowid
            player_id = connection.execute(
                """
                INSERT INTO players(current_name, first_seen_at)
                VALUES ('Demethan', '2026-08-02T00:00:00+00:00')
                """
            ).lastrowid
            connection.execute(
                """
                INSERT INTO player_sessions(
                    player_id, server_id, started_at, start_source, confidence
                ) VALUES (?, ?, '2026-08-02T00:00:00+00:00', 'discord', 'exact')
                """,
                (player_id, server_id),
            )

            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO player_sessions(
                        player_id, server_id, started_at, start_source, confidence
                    ) VALUES (?, ?, '2026-08-02T01:00:00+00:00', 'api', 'estimated')
                    """,
                    (player_id, server_id),
                )

            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO applications(
                        discord_user_id, minecraft_name, over_18,
                        sponsor_type, source, submitted_at
                    ) VALUES (
                        123, 'Demethan', 0, 'discord', 'Friend',
                        '2026-08-02T00:00:00+00:00'
                    )
                    """
                )


if __name__ == "__main__":
    unittest.main()
