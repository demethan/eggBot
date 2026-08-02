import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

from eggbot_db.database import Database
from eggbot_db.json_importer import ImportValidationError, LegacyJsonImporter
from eggbot_db.repositories import ServerRepository
from eggbot_db.secrets import SecretBox
from eggbot_db.validation import validate_imported_database


def fixture_payload():
    return {
        "adminChannelID": 100,
        "supportChannelID": 200,
        "memberRoleID": 300,
        "fryBotRoleID": 400,
        "applyUrl": "https://example.test/apply",
        "connectUrl": "https://example.test/connect",
        "joinMessage": "Welcome {member}",
        "server_list": {
            "bacon": {
                "endpoint": "https://bacon.example/api/",
                "id": "api-user",
                "password": "plain-password",
                "token": "plain-token",
                "players": {
                    "Demethan": {
                        "duration": 12,
                        "login_time": "2026-08-02 13:00:00.000000",
                    }
                },
            }
        },
        "players": {
            "Demethan": {"last_seen": "2026-08-02 09:15:00"},
            "arwhal": {"last_seen": "2026-08-01 20:00:00"},
        },
        "reboot_schedule": {
            "bacon": {
                "time": "2026-08-02T10:00:00+00:00",
                "frequency": "daily",
                "timezone": "America/Montreal",
            }
        },
    }


class LegacyJsonImporterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.source = self.directory / "data.json"
        self.source.write_text(json.dumps(fixture_payload()), encoding="utf-8")
        self.database = Database(self.directory / "eggbot.sqlite3")
        self.database.migrate()
        self.key = Fernet.generate_key()

    def tearDown(self):
        self.temporary.cleanup()

    def importer(self, connection):
        return LegacyJsonImporter(connection, SecretBox(self.key), "America/Montreal")

    def test_imports_all_legacy_categories_and_encrypts_secrets(self):
        with self.database.connect() as connection:
            report = self.importer(connection).import_file(self.source)
            counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "settings",
                    "servers",
                    "players",
                    "player_names",
                    "reboot_schedules",
                    "legacy_server_player_state",
                    "import_runs",
                )
            }
            server = ServerRepository(connection, SecretBox(self.key)).get_by_name("BACON")
            last_seen = connection.execute(
                "SELECT last_seen_at FROM players WHERE current_name = 'Demethan'"
            ).fetchone()[0]

        self.assertEqual(report.settings, 7)
        self.assertEqual(report.servers, 1)
        self.assertEqual(report.players, 2)
        self.assertEqual(report.schedules, 1)
        self.assertEqual(report.legacy_server_players, 1)
        self.assertEqual(
            counts,
            {
                "settings": 7,
                "servers": 1,
                "players": 2,
                "player_names": 2,
                "reboot_schedules": 1,
                "legacy_server_player_state": 1,
                "import_runs": 1,
            },
        )
        self.assertEqual(server.api_password, "plain-password")
        self.assertEqual(server.api_token, "plain-token")
        self.assertEqual(last_seen, "2026-08-02T13:15:00+00:00")

        database_bytes = self.database.path.read_bytes()
        self.assertNotIn(b"plain-password", database_bytes)
        self.assertNotIn(b"plain-token", database_bytes)

    def test_same_source_is_idempotent(self):
        with self.database.connect() as connection:
            first = self.importer(connection).import_file(self.source)
            second = self.importer(connection).import_file(self.source)
            runs = connection.execute("SELECT COUNT(*) FROM import_runs").fetchone()[0]
            states = connection.execute(
                "SELECT COUNT(*) FROM legacy_server_player_state"
            ).fetchone()[0]

        self.assertFalse(first.already_imported)
        self.assertTrue(second.already_imported)
        self.assertEqual(runs, 1)
        self.assertEqual(states, 1)

    def test_updated_source_upserts_records_without_duplication(self):
        with self.database.connect() as connection:
            self.importer(connection).import_file(self.source)
            payload = fixture_payload()
            payload["server_list"]["bacon"]["endpoint"] = "https://new.example"
            self.source.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            self.importer(connection).import_file(self.source)
            server_count = connection.execute("SELECT COUNT(*) FROM servers").fetchone()[0]
            run_count = connection.execute("SELECT COUNT(*) FROM import_runs").fetchone()[0]
            endpoint = connection.execute("SELECT endpoint FROM servers").fetchone()[0]

        self.assertEqual(server_count, 1)
        self.assertEqual(run_count, 2)
        self.assertEqual(endpoint, "https://new.example")

    def test_validation_errors_prevent_import(self):
        payload = fixture_payload()
        payload["server_list"]["bacon"]["endpoint"] = None
        self.source.write_text(json.dumps(payload), encoding="utf-8")

        with self.database.connect() as connection:
            with self.assertRaises(ImportValidationError):
                self.importer(connection).import_file(self.source)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM servers").fetchone()[0], 0)

    def test_preserves_unresolved_discord_keyed_schedule(self):
        payload = fixture_payload()
        schedule = payload["reboot_schedule"].pop("bacon")
        payload["reboot_schedule"]["<@123456789>"] = schedule
        self.source.write_text(json.dumps(payload), encoding="utf-8")

        with self.database.connect() as connection:
            report = self.importer(connection).import_file(self.source)
            mapped = connection.execute("SELECT COUNT(*) FROM reboot_schedules").fetchone()[0]
            unresolved = connection.execute(
                "SELECT schedule_key, time_value FROM legacy_reboot_schedules"
            ).fetchone()

        self.assertEqual(report.schedules, 1)
        self.assertEqual(report.unresolved_schedules, 1)
        self.assertEqual(mapped, 0)
        self.assertEqual(unresolved["schedule_key"], "<@123456789>")
        self.assertIn("schedules require server mapping", report.warnings[0])

    def test_failure_rolls_back_every_category(self):
        original = ServerRepository.upsert

        def fail_after_server(repository, **kwargs):
            original(repository, **kwargs)
            raise RuntimeError("simulated import failure")

        with self.database.connect() as connection:
            with patch.object(ServerRepository, "upsert", fail_after_server):
                with self.assertRaises(RuntimeError):
                    self.importer(connection).import_file(self.source)
            for table in ("settings", "servers", "players", "import_runs"):
                self.assertEqual(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],
                    0,
                )

    def test_cli_dry_run_needs_no_database_or_secret_key(self):
        result = subprocess.run(
            [
                str(Path(__file__).parents[1] / ".venv" / "bin" / "python"),
                "scripts/import_legacy_json.py",
                "--source",
                str(self.source),
                "--dry-run",
            ],
            cwd=Path(__file__).parents[1],
            check=True,
            capture_output=True,
            text=True,
        )
        report = json.loads(result.stdout)
        self.assertEqual(report["mode"], "dry_run")
        self.assertEqual(report["servers"], 1)
        self.assertFalse((self.directory / "unused.sqlite3").exists())

    def test_sanitized_database_validation(self):
        with self.database.connect() as connection:
            self.importer(connection).import_file(self.source)
            report = validate_imported_database(
                connection,
                self.database.path,
                SecretBox(self.key),
            )

        self.assertTrue(report["valid"])
        self.assertEqual(report["integrity"], "ok")
        self.assertEqual(report["foreign_key_errors"], 0)
        self.assertEqual(report["plaintext_secret_occurrences"], 0)
        self.assertTrue(report["source_counts_match"])


if __name__ == "__main__":
    unittest.main()
