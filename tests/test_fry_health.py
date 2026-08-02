import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from eggbot_db.database import Database
from fry_health import FryHealthService


class FryHealthServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        database = Database(Path(self.temporary.name) / "health.sqlite3")
        database.migrate()
        self.connection = database.connect()
        self.connection.execute(
            "INSERT INTO servers(name, endpoint) VALUES ('BACON', 'https://bacon')"
        )
        self.connection.execute(
            "INSERT INTO servers(name, endpoint) VALUES ('PIZZA', 'https://pizza')"
        )
        self.connection.commit()
        self.health = FryHealthService(self.connection)

    def tearDown(self):
        self.connection.close()
        self.temporary.cleanup()

    def cycle(self, minute, bacon=None, pizza=None):
        return self.health.record_cycle(
            {"bacon": bacon, "pizza": pizza},
            observed_at=datetime(2026, 8, 2, 12, minute, tzinfo=timezone.utc),
        )

    def test_two_failures_alert_once_then_recovery(self):
        first = self.cycle(0, "connection timeout", "connection timeout")
        alert = self.cycle(5, "connection timeout", "connection timeout")
        self.health.mark_alert_sent(
            alert.incident_id,
            channel_id=123,
            message_id=456,
            alerted_at=datetime(2026, 8, 2, 12, 5, tzinfo=timezone.utc),
        )
        unchanged = self.cycle(10, "connection timeout", "connection timeout")
        narrowed = self.cycle(15, None, "connection timeout")
        recovery = self.cycle(20, None, None)
        recovery_retry = self.cycle(25, None, None)
        self.health.mark_recovery_sent(
            recovery.incident_id,
            notified_at=datetime(2026, 8, 2, 12, 25, tzinfo=timezone.utc),
        )
        settled = self.cycle(30, None, None)

        self.assertEqual(first.action, "none")
        self.assertEqual(alert.action, "alert")
        self.assertTrue(alert.all_servers_unreachable)
        self.assertEqual(alert.affected_servers, ("BACON", "PIZZA"))
        self.assertEqual(unchanged.action, "none")
        self.assertEqual(narrowed.action, "update")
        self.assertEqual(narrowed.affected_servers, ("PIZZA",))
        self.assertFalse(narrowed.all_servers_unreachable)
        self.assertEqual(recovery.action, "recovery")
        self.assertEqual(recovery_retry.action, "recovery")
        self.assertEqual(recovery.alert_channel_id, 123)
        self.assertEqual(recovery.alert_message_id, 456)
        self.assertEqual(
            recovery.recovered_at,
            datetime(2026, 8, 2, 12, 20, tzinfo=timezone.utc),
        )
        self.assertEqual(settled.action, "none")

    def test_unsent_alert_is_retried_without_duplicate_incident(self):
        self.cycle(0, "HTTP 503", None)
        first_alert = self.cycle(5, "HTTP 503", None)
        retry = self.cycle(10, "HTTP 503", None)

        incidents = self.connection.execute(
            "SELECT count(*) FROM fry_connectivity_incidents"
        ).fetchone()[0]
        self.assertEqual(first_alert.action, "alert")
        self.assertEqual(retry.action, "alert")
        self.assertEqual(first_alert.incident_id, retry.incident_id)
        self.assertEqual(incidents, 1)

    def test_single_failed_poll_does_not_alert_and_resets_on_success(self):
        self.cycle(0, "ClientConnectorError", None)
        recovered_before_threshold = self.cycle(5, None, None)

        state = self.connection.execute(
            """
            SELECT consecutive_failures, outage_started_at
            FROM fry_health_state h JOIN servers s ON s.id = h.server_id
            WHERE s.name = 'BACON'
            """
        ).fetchone()
        self.assertEqual(recovered_before_threshold.action, "none")
        self.assertEqual(state["consecutive_failures"], 0)
        self.assertIsNone(state["outage_started_at"])


if __name__ == "__main__":
    unittest.main()
