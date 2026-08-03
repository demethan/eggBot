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

    def test_recent_players_reports_server_activity_and_online_state(self):
        self.ingest(1, "Earlier has joined the server.", 1)
        self.ingest(2, "Earlier has left the server.", 20)
        self.ingest(3, "OnlineNow has joined the server.", 30)

        recent = self.tracking.recent_players("bacon")

        self.assertEqual(
            recent,
            [
                ("OnlineNow", "2026-08-02T12:30:00+00:00", True),
                ("Earlier", "2026-08-02T12:20:00+00:00", False),
            ],
        )

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

    def test_api_reconciliation_requires_two_absent_snapshots_to_close(self):
        first = self.tracking.reconcile_api_snapshot(
            server_name="BACON",
            players_online={"Demethan": {"duration": 1}},
            observed_at=datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc),
        )
        one_absent = self.tracking.reconcile_api_snapshot(
            server_name="BACON",
            players_online={},
            observed_at=datetime(2026, 8, 2, 12, 5, tzinfo=timezone.utc),
        )
        two_absent = self.tracking.reconcile_api_snapshot(
            server_name="BACON",
            players_online={},
            observed_at=datetime(2026, 8, 2, 12, 10, tzinfo=timezone.utc),
        )

        session = self.connection.execute(
            "SELECT * FROM player_sessions"
        ).fetchone()
        self.assertEqual(first.started_sessions, 1)
        self.assertEqual(one_absent.closed_sessions, 0)
        self.assertEqual(two_absent.closed_sessions, 1)
        self.assertEqual(session["ended_at"], "2026-08-02T12:10:00+00:00")
        self.assertEqual(session["confidence"], "api_derived")

    def test_api_snapshot_does_not_duplicate_discord_session(self):
        self.ingest(1, "Demethan has joined the server.")
        result = self.tracking.reconcile_api_snapshot(
            server_name="BACON",
            players_online=["Demethan"],
            observed_at=datetime(2026, 8, 2, 12, 5, tzinfo=timezone.utc),
        )
        sessions = self.connection.execute(
            "SELECT count(*) FROM player_sessions"
        ).fetchone()[0]
        self.assertEqual(result.started_sessions, 0)
        self.assertEqual(sessions, 1)

    def test_api_login_time_improves_initial_session_start(self):
        result = self.tracking.reconcile_api_snapshot(
            server_name="BACON",
            players_online={
                "Demethan": {"login_time": "2026-08-02 11:57:30.123456"}
            },
            observed_at=datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc),
        )

        session = self.connection.execute(
            "SELECT * FROM player_sessions"
        ).fetchone()
        self.assertEqual(result.started_sessions, 1)
        self.assertEqual(session["started_at"], "2026-08-02T11:57:30+00:00")

    def test_changed_api_login_time_splits_reconnect_between_polls(self):
        self.tracking.reconcile_api_snapshot(
            server_name="BACON",
            players_online={
                "Demethan": {"login_time": "2026-08-02 11:55:00"}
            },
            observed_at=datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc),
        )
        result = self.tracking.reconcile_api_snapshot(
            server_name="BACON",
            players_online={
                "Demethan": {"login_time": "2026-08-02 12:03:00"}
            },
            observed_at=datetime(2026, 8, 2, 12, 5, tzinfo=timezone.utc),
        )

        sessions = self.connection.execute(
            "SELECT * FROM player_sessions ORDER BY started_at"
        ).fetchall()
        self.assertEqual(result.closed_sessions, 1)
        self.assertEqual(result.started_sessions, 1)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0]["ended_at"], "2026-08-02T12:03:00+00:00")
        self.assertEqual(sessions[1]["started_at"], "2026-08-02T12:03:00+00:00")

    def test_invalid_or_future_api_login_time_uses_observation_time(self):
        for index, login_time in enumerate(("not-a-date", "2026-08-02 12:30:00")):
            name = f"Player{index}"
            self.tracking.reconcile_api_snapshot(
                server_name="BACON",
                players_online={name: {"login_time": login_time}},
                observed_at=datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc),
            )

        starts = [
            row[0]
            for row in self.connection.execute(
                "SELECT started_at FROM player_sessions ORDER BY started_at"
            )
        ]
        self.assertEqual(starts, [
            "2026-08-02T12:00:00+00:00",
            "2026-08-02T12:00:00+00:00",
        ])

    def test_api_metadata_opens_pack_installation_and_attaches_sessions(self):
        self.tracking.reconcile_api_snapshot(
            server_name="BACON",
            players_online={
                "Demethan": {"login_time": "2026-08-02 11:55:00"}
            },
            pack_name=" Test Pack ",
            pack_version=" 1.2.3 ",
            pack_metadata={"pack_url": "https://example/pack"},
            observed_at=datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc),
        )

        installation = self.connection.execute(
            """
            SELECT pi.*, p.name, pv.version, pv.metadata_json
            FROM pack_installations pi
            JOIN pack_versions pv ON pv.id = pi.pack_version_id
            JOIN packs p ON p.id = pv.pack_id
            """
        ).fetchone()
        session = self.connection.execute(
            "SELECT * FROM player_sessions"
        ).fetchone()
        observation = self.connection.execute(
            "SELECT * FROM server_observations"
        ).fetchone()
        self.assertEqual(installation["name"], "Test Pack")
        self.assertEqual(installation["version"], "1.2.3")
        self.assertEqual(installation["started_at"], "2026-08-02T12:00:00+00:00")
        self.assertIn("pack_url", installation["metadata_json"])
        self.assertEqual(session["pack_installation_id"], installation["id"])
        self.assertEqual(observation["pack_version_id"], installation["pack_version_id"])

    def test_pack_change_closes_installation_and_splits_online_session(self):
        player = {"Demethan": {"login_time": "2026-08-02 11:55:00"}}
        self.tracking.reconcile_api_snapshot(
            server_name="BACON",
            players_online=player,
            pack_name="Pack A",
            pack_version="1.0",
            observed_at=datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc),
        )
        result = self.tracking.reconcile_api_snapshot(
            server_name="BACON",
            players_online=player,
            pack_name="Pack B",
            pack_version="2.0",
            observed_at=datetime(2026, 8, 2, 13, 0, tzinfo=timezone.utc),
        )

        installations = self.connection.execute(
            "SELECT * FROM pack_installations ORDER BY started_at"
        ).fetchall()
        sessions = self.connection.execute(
            "SELECT * FROM player_sessions ORDER BY started_at"
        ).fetchall()
        self.assertEqual(result.closed_sessions, 1)
        self.assertEqual(result.started_sessions, 1)
        self.assertEqual(installations[0]["ended_at"], "2026-08-02T13:00:00+00:00")
        self.assertIsNone(installations[1]["ended_at"])
        self.assertEqual(sessions[0]["ended_at"], "2026-08-02T13:00:00+00:00")
        self.assertEqual(sessions[1]["started_at"], "2026-08-02T13:00:00+00:00")
        self.assertEqual(sessions[1]["pack_installation_id"], installations[1]["id"])

    def test_discord_join_uses_current_pack_installation(self):
        self.tracking.reconcile_api_snapshot(
            server_name="BACON",
            players_online={},
            pack_name="Test Pack",
            pack_version="1.0",
            observed_at=datetime(2026, 8, 2, 11, 55, tzinfo=timezone.utc),
        )
        self.ingest(1, "Demethan has joined the server.", minute=0)

        session = self.connection.execute(
            "SELECT * FROM player_sessions"
        ).fetchone()
        installation = self.connection.execute(
            "SELECT * FROM pack_installations"
        ).fetchone()
        self.assertEqual(session["pack_installation_id"], installation["id"])
        self.assertEqual(session["start_source"], "discord")

    def test_server_and_pack_statistics_report_tracked_time(self):
        self.tracking.reconcile_api_snapshot(
            server_name="BACON",
            players_online={
                "Demethan": {"login_time": "2026-08-02 11:55:00"}
            },
            pack_name="Test Pack",
            pack_version="1.0",
            observed_at=datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc),
        )
        self.tracking.reconcile_api_snapshot(
            server_name="BACON",
            players_online={},
            pack_name="Test Pack",
            pack_version="1.0",
            observed_at=datetime(2026, 8, 2, 13, 0, tzinfo=timezone.utc),
        )
        self.tracking.reconcile_api_snapshot(
            server_name="BACON",
            players_online={},
            pack_name="Test Pack",
            pack_version="1.0",
            observed_at=datetime(2026, 8, 2, 13, 5, tzinfo=timezone.utc),
        )

        servers = self.tracking.server_statistics(
            "BACON", now=datetime(2026, 8, 2, 14, 0, tzinfo=timezone.utc)
        )
        packs = self.tracking.pack_statistics(
            "BACON", now=datetime(2026, 8, 2, 14, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(len(servers), 1)
        self.assertAlmostEqual(servers[0].total_hours, 1 + 10 / 60, places=5)
        self.assertEqual(servers[0].sessions, 1)
        self.assertEqual(servers[0].unique_players, 1)
        self.assertEqual(servers[0].current_pack, "Test Pack")
        self.assertEqual(len(packs), 1)
        self.assertAlmostEqual(packs[0].installed_hours, 2.0)
        self.assertAlmostEqual(packs[0].played_hours, 1 + 5 / 60, places=5)
        self.assertEqual(packs[0].sessions, 1)
        self.assertEqual(packs[0].unique_players, 1)
        self.assertTrue(packs[0].baseline)

        period_start = datetime(2026, 8, 2, 12, 30, tzinfo=timezone.utc)
        period_servers = self.tracking.server_statistics(
            "BACON",
            now=datetime(2026, 8, 2, 14, 0, tzinfo=timezone.utc),
            since=period_start,
        )
        period_packs = self.tracking.pack_statistics(
            "BACON",
            now=datetime(2026, 8, 2, 14, 0, tzinfo=timezone.utc),
            since=period_start,
        )
        self.assertAlmostEqual(period_servers[0].total_hours, 35 / 60, places=5)
        self.assertAlmostEqual(period_packs[0].installed_hours, 1.5, places=5)
        self.assertAlmostEqual(period_packs[0].played_hours, 35 / 60, places=5)

    def test_engagement_score_rewards_breadth_with_diminishing_returns(self):
        self.tracking.reconcile_api_snapshot(
            server_name="BACON",
            players_online={},
            pack_name="Community Pack",
            pack_version="1.0",
            observed_at=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        )
        installation_id = self.connection.execute(
            "SELECT id FROM pack_installations"
        ).fetchone()[0]
        for index in range(4):
            player_id = self.connection.execute(
                """
                INSERT INTO players(current_name, first_seen_at)
                VALUES (?, '2026-08-02T12:00:00+00:00')
                """,
                (f"Player{index}",),
            ).lastrowid
            self.connection.execute(
                """
                INSERT INTO player_sessions(
                    player_id, server_id, pack_installation_id, started_at,
                    ended_at, start_source, end_source, confidence
                ) VALUES (?, ?, ?, '2026-08-02T12:00:00+00:00',
                          '2026-08-02T13:00:00+00:00', 'api', 'api', 'api_derived')
                """,
                (player_id, self.server_id, installation_id),
            )
        self.connection.commit()

        broad = self.tracking.engagement_statistics(
            "BACON", now=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        )[0].window(7)
        self.assertEqual(broad.active_players, 4)
        self.assertEqual(broad.person_hours, 4.0)
        self.assertEqual(broad.participation_score, 4.0)

        self.connection.execute("DELETE FROM player_sessions")
        self.connection.execute("DELETE FROM players")
        player_id = self.connection.execute(
            """
            INSERT INTO players(current_name, first_seen_at)
            VALUES ('SoloPlayer', '2026-08-02T09:00:00+00:00')
            """
        ).lastrowid
        self.connection.execute(
            """
            INSERT INTO player_sessions(
                player_id, server_id, pack_installation_id, started_at,
                ended_at, start_source, end_source, confidence
            ) VALUES (?, ?, ?, '2026-08-02T09:00:00+00:00',
                      '2026-08-02T13:00:00+00:00', 'api', 'api', 'api_derived')
            """,
            (player_id, self.server_id, installation_id),
        )
        self.connection.commit()

        concentrated = self.tracking.engagement_statistics(
            "BACON", now=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        )[0].window(7)
        self.assertEqual(concentrated.active_players, 1)
        self.assertEqual(concentrated.person_hours, 4.0)
        self.assertEqual(concentrated.participation_score, 2.0)

    def test_refresh_status_uses_14_and_28_day_inactivity_with_coverage_gate(self):
        self.tracking.reconcile_api_snapshot(
            server_name="BACON",
            players_online={},
            pack_name="Old Pack",
            pack_version="1.0",
            observed_at=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
        )
        now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        candidate = self.tracking.engagement_statistics("BACON", now=now)[0]
        self.assertEqual(candidate.refresh_status, "refresh_candidate")
        self.assertIsNone(candidate.last_activity_at)

        installation_id = self.connection.execute(
            "SELECT id FROM pack_installations"
        ).fetchone()[0]
        player_id = self.connection.execute(
            """
            INSERT INTO players(current_name, first_seen_at)
            VALUES ('RecentPlayer', '2026-07-15T12:00:00+00:00')
            """
        ).lastrowid
        self.connection.execute(
            """
            INSERT INTO player_sessions(
                player_id, server_id, pack_installation_id, started_at,
                ended_at, start_source, end_source, confidence
            ) VALUES (?, ?, ?, '2026-07-15T12:00:00+00:00',
                      '2026-07-15T13:00:00+00:00', 'api', 'api', 'api_derived')
            """,
            (player_id, self.server_id, installation_id),
        )
        self.connection.commit()
        watch = self.tracking.engagement_statistics("BACON", now=now)[0]
        self.assertEqual(watch.refresh_status, "refresh_watch")
        self.assertEqual(watch.window(14).active_players, 0)
        self.assertEqual(watch.window(28).active_players, 1)


if __name__ == "__main__":
    unittest.main()
