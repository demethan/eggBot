import sqlite3
import unittest
from unittest.mock import Mock

from runtime_config import RuntimeConfig
from tracking_cog import TrackingCog


class RuntimeConfigTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            "CREATE TABLE settings(key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at TEXT)"
        )
        self.config = RuntimeConfig(self.connection, Mock())

    def tearDown(self):
        self.connection.close()

    def test_settings_and_notification_sources_are_database_backed(self):
        self.config.set("adminChannelID", 123)
        self.config.set("serverNotificationSources", {"456": "BACON"})

        self.assertEqual(self.config.require_int("adminChannelID"), 123)
        self.assertEqual(self.config.notification_source(456), "BACON")
        self.assertIsNone(self.config.notification_source(999))

    def test_required_integer_rejects_missing_setting(self):
        with self.assertRaisesRegex(RuntimeError, "generalChannelID"):
            self.config.require_int("generalChannelID")


class TrackingSourceAuthenticationTests(unittest.IsolatedAsyncioTestCase):
    async def test_unconfigured_bot_is_ignored(self):
        runtime_config = Mock()
        runtime_config.require_int.return_value = 100
        runtime_config.notification_source.return_value = None
        client = Mock(runtime_config=runtime_config)
        tracking = Mock()
        cog = TrackingCog(client, tracking)
        message = Mock()
        message.channel.id = 100
        message.author.bot = True
        message.author.id = 999
        message.webhook_id = None

        await cog.on_message(message)

        tracking.ingest_discord_message.assert_not_called()

    async def test_configured_webhook_is_recorded_with_mapped_server_name(self):
        runtime_config = Mock()
        runtime_config.require_int.return_value = 100
        runtime_config.notification_source.return_value = "BACON"
        client = Mock(runtime_config=runtime_config)
        tracking = Mock()
        tracking.ingest_discord_message.return_value.status = "ignored"
        cog = TrackingCog(client, tracking)
        message = Mock()
        message.channel.id = 100
        message.author.bot = True
        message.author.id = 999
        message.webhook_id = 456
        message.id = 1
        message.created_at = Mock()
        message.content = "Demethan joined the server"

        await cog.on_message(message)

        self.assertEqual(
            tracking.ingest_discord_message.call_args.kwargs["source_name"],
            "BACON",
        )
        runtime_config.notification_source.assert_called_once_with(456)


if __name__ == "__main__":
    unittest.main()
