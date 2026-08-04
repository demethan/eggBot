import sqlite3
import shutil
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

from eggbot_db.database import Database
from eggbot_db.repositories import (
    RebootScheduleRepository,
    ServerCapabilities,
    ServerRepository,
    SettingsRepository,
)
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

        self.assertEqual(
            [migration.version for migration in first],
            [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
        )
        self.assertEqual(second, [])
        with self.database.connect() as connection:
            applied = connection.execute(
                "SELECT version, name FROM schema_migrations"
            ).fetchall()
            self.assertEqual(
                [tuple(row) for row in applied],
                [
                    (1, "initial_schema"),
                    (2, "legacy_import"),
                    (3, "application_workflow"),
                    (4, "player_discord_links"),
                    (5, "denial_whitelist_audit"),
                    (6, "application_followups"),
                    (7, "followup_delivery"),
                    (8, "whitelist_admin_actions"),
                    (9, "whitelist_admin_add_actions"),
                    (10, "fry_connectivity_health"),
                    (11, "self_reported_player_links"),
                ],
            )

    def test_connection_enables_integrity_and_wal_settings(self):
        self.database.migrate()
        with self.database.connect() as connection:
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(self.database.path.stat().st_mode & 0o777, 0o600)

    def test_self_reported_link_migration_preserves_approved_links(self):
        legacy_migrations = self.directory / "legacy_migrations"
        legacy_migrations.mkdir()
        for migration in self.database.migrations():
            if migration.version <= 10:
                shutil.copy2(migration.path, legacy_migrations / migration.path.name)
        legacy_database = Database(self.database.path, legacy_migrations)
        legacy_database.migrate()
        with legacy_database.connect() as connection:
            application_id = connection.execute(
                """
                INSERT INTO applications(
                    discord_user_id, minecraft_name, over_18, source, status,
                    submitted_at, decided_at, reviewer_discord_user_id
                ) VALUES (123, 'Demethan', 1, 'Friend', 'approved',
                          '2026-08-01T00:00:00+00:00',
                          '2026-08-01T01:00:00+00:00', 999)
                """
            ).lastrowid
            connection.execute(
                """
                INSERT INTO player_discord_links(
                    discord_user_id, discord_username, discord_display_name,
                    minecraft_name, application_id, linked_at, updated_at
                ) VALUES (123, 'discord_user', 'Display Name', 'Demethan', ?,
                          '2026-08-01T01:00:00+00:00',
                          '2026-08-01T01:00:00+00:00')
                """,
                (application_id,),
            )
            connection.commit()

        applied = self.database.migrate()
        with self.database.connect() as connection:
            link = connection.execute(
                "SELECT * FROM player_discord_links WHERE discord_user_id = 123"
            ).fetchone()

        self.assertEqual([migration.version for migration in applied], [11])
        self.assertEqual(link["minecraft_name"], "Demethan")
        self.assertEqual(link["application_id"], application_id)
        self.assertEqual(link["link_source"], "approved_application")

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

    def test_server_repository_round_trips_capabilities_and_refreshed_token(self):
        self.database.migrate()
        secret_box = SecretBox(Fernet.generate_key())
        with self.database.connect() as connection:
            servers = ServerRepository(connection, secret_box)
            server_id = servers.upsert(
                name="BACON",
                endpoint="https://bacon.example",
                api_user="user",
                api_password="password",
                api_token="old-token",
            )
            servers.set_capabilities(
                server_id,
                ServerCapabilities(
                    authentication="authenticated",
                    metadata_read="supported",
                    whitelist_read="supported",
                    whitelist_write="permission_denied",
                ),
                "2026-08-02T00:00:00+00:00",
            )
            servers.update_token(server_id, "new-token")
            connection.commit()
            stored = servers.get_by_name("bacon")
            enabled = servers.list_enabled()

        self.assertEqual(stored.api_token, "new-token")
        self.assertEqual(stored.capabilities.metadata_read, "supported")
        self.assertEqual(stored.capabilities.whitelist_write, "permission_denied")
        self.assertEqual([item.name for item in enabled], ["BACON"])

    def test_server_disable_retains_record_and_excludes_it_from_enabled_list(self):
        self.database.migrate()
        secret_box = SecretBox(Fernet.generate_key())
        with self.database.connect() as connection:
            servers = ServerRepository(connection, secret_box)
            server_id = servers.upsert(name="BACON", endpoint="https://bacon.example")
            servers.set_enabled(server_id, False)
            connection.commit()

            stored = servers.get_by_name("BACON")
            enabled = servers.list_enabled()

        self.assertIsNotNone(stored)
        self.assertFalse(stored.enabled)
        self.assertEqual(enabled, [])

    def test_reboot_schedule_repository_round_trips_enabled_schedules(self):
        self.database.migrate()
        secret_box = SecretBox(Fernet.generate_key())
        with self.database.connect() as connection:
            servers = ServerRepository(connection, secret_box)
            schedules = RebootScheduleRepository(connection)
            server_id = servers.upsert(name="BACON", endpoint="https://bacon.example")
            schedules.set(
                server_id,
                time_value="2026-08-03T12:00:00+00:00",
                frequency="daily",
                timezone="America/Montreal",
            )
            connection.commit()
            stored = schedules.list_enabled()

        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].server_name, "BACON")
        self.assertEqual(stored[0].frequency, "daily")
        self.assertEqual(stored[0].timezone, "America/Montreal")

if __name__ == "__main__":
    unittest.main()
