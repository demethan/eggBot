import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from eggbot_db.database import Database
from player_tracking import PlayerTrackingService, parse_player_event


class PlayerTrackingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        database = Database(Path(self.temporary.name) / "tracking.sqlite3")
        database.migrate()
        self.connection = database.connect()
        self.server_id = self.connection.execute(
            "INSERT INTO servers(name, endpoint) VALUES ('BACON', 'https://example')"
        ).lastrowid
        self.connection.commit()
        self.tracking = PlayerTrackingService(self.connection)

    def tearDown(self):
        self.connection.close()
        self.temporary.cleanup()

    def ingest(self, message_id, content, minute=0, source_name="BACON"):
        return self.tracking.ingest_discord_message(
            discord_message_id=message_id,
            discord_channel_id=123,
            source_discord_id=456,
            source_name=source_name,
            occurred_at=datetime(2026, 8, 2, 12, minute, tzinfo=timezone.utc),
            raw_content=content,
        )

    def test_parser_accepts_join_leave_variants_and_rejects_chat(self):
        self.assertEqual(parse_player_event("Demethan has joined the server.").event_type, "join")
        self.assertEqual(parse_player_event("Demethan has left the game").event_type, "leave")
        self.assertIsNone(parse_player_event("[Demethan] hello"))
        self.assertIsNone(parse_player_event("Demethan has left the building."))

    def test_join_and_leave_create_exact_session(self):
        joined = self.ingest(1, "Demethan has joined the server.", 1)
        left = self.ingest(2, "Demethan has left the server.", 31)

        session = self.connection.execute(
            "SELECT * FROM player_sessions"
        ).fetchone()
        self.assertEqual(joined.status, "recorded")
        self.assertEqual(left.status, "recorded")
        self.assertEqual(session["started_at"], "2026-08-02T12:01:00+00:00")
        self.assertEqual(session["ended_at"], "2026-08-02T12:31:00+00:00")
        self.assertEqual(session["confidence"], "exact")
        self.assertEqual(session["start_source"], "discord")
        self.assertEqual(session["end_source"], "discord")

    def test_duplicate_message_and_duplicate_join_do_not_duplicate_session(self):
        self.ingest(1, "Demethan has joined the server.")
        duplicate = self.ingest(1, "Demethan has joined the server.")
        self.ingest(2, "Demethan has joined the server.", 1)

        count = self.connection.execute(
            "SELECT count(*) FROM player_sessions"
        ).fetchone()[0]
        event_count = self.connection.execute(
            "SELECT count(*) FROM discord_events"
        ).fetchone()[0]
        self.assertEqual(duplicate.status, "duplicate")
        self.assertEqual(count, 1)
        self.assertEqual(event_count, 2)

    def test_leave_without_join_is_preserved_without_inventing_session(self):
        result = self.ingest(1, "Demethan has left the server.")

        sessions = self.connection.execute(
            "SELECT count(*) FROM player_sessions"
        ).fetchone()[0]
        events = self.connection.execute(
            "SELECT count(*) FROM discord_events"
        ).fetchone()[0]
        self.assertEqual(result.status, "recorded")
        self.assertEqual(sessions, 0)
        self.assertEqual(events, 1)

    def test_unknown_server_bot_is_not_recorded(self):
        result = self.ingest(1, "Demethan has joined the server.", source_name="UNKNOWN")
        self.assertEqual(result.status, "unknown_server")
        self.assertEqual(
            self.connection.execute("SELECT count(*) FROM discord_events").fetchone()[0],
            0,
        )

    def test_weekly_hours_clip_cross_boundary_and_include_open_session(self):
        player_id = self.connection.execute(
            """
            INSERT INTO players(current_name, first_seen_at)
            VALUES ('Demethan', '2026-07-01T00:00:00+00:00')
            """
        ).lastrowid
        self.connection.execute(
            """
            INSERT INTO player_sessions(
                player_id, server_id, started_at, ended_at,
                start_source, end_source, confidence
            ) VALUES (?, ?, '2026-08-03T02:00:00+00:00',
                      '2026-08-03T06:00:00+00:00', 'discord', 'discord', 'exact')
            """,
            (player_id, self.server_id),
        )
        self.connection.execute(
            """
            INSERT INTO player_sessions(
                player_id, server_id, started_at, start_source, confidence
            ) VALUES (?, ?, '2026-08-04T12:00:00+00:00', 'discord', 'exact')
            """,
            (player_id, self.server_id),
        )
        self.connection.commit()

        start, end, players = self.tracking.weekly_hours(
            now=datetime(2026, 8, 4, 14, 0, tzinfo=timezone.utc)
        )

        self.assertEqual(start.isoformat(), "2026-08-03T00:00:00-04:00")
        self.assertEqual(end.isoformat(), "2026-08-04T10:00:00-04:00")
        self.assertEqual(len(players), 1)
        self.assertAlmostEqual(players[0].total_hours, 4.0)
        self.assertEqual(players[0].server_hours, {"BACON": 4.0})
        self.assertEqual(players[0].open_sessions, 1)


if __name__ == "__main__":
    unittest.main()
