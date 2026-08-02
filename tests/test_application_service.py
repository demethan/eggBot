import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

from application_service import ApplicationAnswers, ApplicationService
from eggbot_db.database import Database
from eggbot_db.repositories import ServerRepository
from eggbot_db.secrets import SecretBox
from fry_api import FryErrorCode, FryResult


class FakeFryClient:
    def __init__(
        self,
        failures=None,
        already=None,
        pause=None,
        contains_failures=None,
        shared_updates=False,
    ):
        self.failures = set(failures or ())
        self.already = set(already or ())
        self.pause = pause
        self.contains_failures = set(contains_failures or ())
        self.shared_updates = shared_updates
        self.contains_calls = []
        self.add_calls = []
        self.remove_calls = []

    async def whitelist_contains(self, server, name):
        self.contains_calls.append((server.name, name))
        if server.name in self.contains_failures:
            return FryResult.failure(FryErrorCode.CONNECTION_ERROR, retryable=True)
        return FryResult.success(server.name in self.already)

    async def whitelist_add(self, server, name):
        self.add_calls.append((server.name, name))
        if self.pause is not None:
            await self.pause.wait()
        if server.name in self.failures:
            return FryResult.failure(FryErrorCode.CONNECTION_ERROR, retryable=True)
        if self.shared_updates:
            self.already.update({"BACON", "EGGS"})
        return FryResult.success(f"{name} added")

    async def whitelist_remove(self, server, name):
        self.remove_calls.append((server.name, name))
        if server.name in self.failures:
            return FryResult.failure(FryErrorCode.CONNECTION_ERROR, retryable=True)
        self.already.clear()
        return FryResult.success(f"{name} removed")


class ApplicationServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        database = Database(Path(self.temporary.name) / "eggbot.sqlite3")
        database.migrate()
        self.connection = database.connect()
        self.servers = ServerRepository(
            self.connection, SecretBox(Fernet.generate_key())
        )
        self.servers.upsert(name="BACON", endpoint="https://bacon.example")
        self.servers.upsert(name="EGGS", endpoint="https://eggs.example")
        self.connection.commit()

    def tearDown(self):
        self.connection.close()
        self.temporary.cleanup()

    def service(self, fry):
        return ApplicationService(self.connection, self.servers, fry)

    def submit(self, service, user_id=123):
        application = service.submit(
            ApplicationAnswers(
                discord_user_id=user_id,
                minecraft_name="Demethan",
                over_18=False,
                sponsor_type="discord",
                sponsor_identifier="SponsorName",
                source="A friend",
            )
        )
        return service.attach_admin_message(application.id, 10, application.id + 100)

    async def test_approval_whitelists_every_enabled_server_and_is_audited(self):
        fry = FakeFryClient(already={"BACON"})
        service = self.service(fry)
        application = self.submit(service)

        result = await service.approve(application.admin_message_id, 999)

        self.assertEqual(result.status, "approved")
        self.assertEqual(result.application.reviewer_discord_user_id, 999)
        self.assertEqual(fry.add_calls, [("EGGS", "Demethan")])
        stored = service.applications.whitelist_results(application.id)
        self.assertEqual(stored, {1: "succeeded", 2: "succeeded"})

        link = service.record_player_link(
            application.id, "discord_user", "Server Nickname"
        )
        self.assertEqual(link.minecraft_name, "Demethan")
        self.assertEqual(service.find_player_links("Demethan"), [link])
        self.assertEqual(service.find_player_links("discord_user"), [link])
        self.assertEqual(service.find_player_links("Server Nickname"), [link])
        self.assertEqual(service.find_player_links("123"), [link])

    def test_denied_application_cannot_create_player_link(self):
        service = self.service(FakeFryClient())
        application = self.submit(service)
        service.deny(application.admin_message_id, 999)

        with self.assertRaises(ValueError):
            service.record_player_link(application.id, "user", "nickname")

    async def test_partial_failure_retries_only_failed_server(self):
        fry = FakeFryClient(failures={"EGGS"})
        service = self.service(fry)
        application = self.submit(service)

        first = await service.approve(application.admin_message_id, 999)
        self.assertEqual(first.status, "partial_failure")

        fry.failures.clear()
        second = await service.approve(application.admin_message_id, 999)

        self.assertEqual(second.status, "approved")
        self.assertEqual(
            fry.add_calls,
            [("BACON", "Demethan"), ("EGGS", "Demethan"), ("EGGS", "Demethan")],
        )
        self.assertEqual(second.servers["BACON"].message, "Previously completed")

    async def test_concurrent_reaction_is_rejected_while_first_is_processing(self):
        pause = asyncio.Event()
        fry = FakeFryClient(pause=pause)
        service = self.service(fry)
        application = self.submit(service)

        first_task = asyncio.create_task(
            service.approve(application.admin_message_id, 999)
        )
        await asyncio.sleep(0)
        second = await service.approve(application.admin_message_id, 888)
        pause.set()
        first = await first_task

        self.assertEqual(second.status, "in_progress")
        self.assertEqual(first.status, "approved")

    async def test_role_failure_returns_approval_to_retryable_state(self):
        fry = FakeFryClient()
        service = self.service(fry)
        application = self.submit(service)
        approved = await service.approve(application.admin_message_id, 999)

        failed = service.mark_role_failure(approved.application.id, "Role failed")
        retried = await service.approve(application.admin_message_id, 999)

        self.assertEqual(failed.status, "partial_failure")
        self.assertEqual(retried.status, "approved")
        self.assertEqual(len(fry.add_calls), 2)

    async def test_denial_is_durable_and_cannot_later_approve(self):
        service = self.service(FakeFryClient())
        application = self.submit(service)

        denied = service.deny(application.admin_message_id, 999)
        approved = await service.approve(application.admin_message_id, 888)

        self.assertEqual(denied.status, "denied")
        self.assertEqual(approved.status, "already_decided")

    async def test_denial_audit_records_present_absent_and_failed_servers(self):
        fry = FakeFryClient(already={"BACON"}, contains_failures={"EGGS"})
        service = self.service(fry)
        application = self.submit(service)
        denied = service.deny(application.admin_message_id, 999)

        audit = await service.audit_denied_whitelist(denied.application)

        self.assertEqual(audit["BACON"].status, "present")
        self.assertEqual(audit["EGGS"].status, "failed")
        self.assertEqual(
            service.applications.denial_whitelist_checks(application.id),
            {1: "present", 2: "failed"},
        )

    async def test_admin_whitelist_removal_is_checked_and_audited(self):
        fry = FakeFryClient(already={"BACON"})
        service = self.service(fry)

        outcomes = await service.remove_from_whitelists("Demethan", 999)

        self.assertEqual(outcomes["BACON"].status, "removed")
        self.assertEqual(outcomes["EGGS"].status, "absent")
        self.assertEqual(fry.remove_calls, [("BACON", "Demethan")])
        self.assertEqual(len(fry.contains_calls), 4)
        rows = self.connection.execute(
            """
            SELECT requested_by_discord_user_id, minecraft_name, status
            FROM whitelist_admin_actions ORDER BY server_id
            """
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in rows],
            [(999, "Demethan", "removed"), (999, "Demethan", "absent")],
        )

    async def test_admin_whitelist_addition_uses_one_server_and_verifies_all(self):
        fry = FakeFryClient(shared_updates=True)
        service = self.service(fry)

        outcomes = await service.add_to_whitelists("Demethan", 999)

        self.assertEqual(outcomes["BACON"].status, "added")
        self.assertEqual(outcomes["EGGS"].status, "present")
        self.assertEqual(fry.add_calls, [("BACON", "Demethan")])
        self.assertEqual(len(fry.contains_calls), 4)
        rows = self.connection.execute(
            """
            SELECT requested_by_discord_user_id, minecraft_name, status
            FROM whitelist_admin_add_actions ORDER BY server_id
            """
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in rows],
            [(999, "Demethan", "added"), (999, "Demethan", "present")],
        )

    def test_denial_followup_offer_is_durable_and_single_response(self):
        service = self.service(FakeFryClient())
        application = self.submit(service)
        denied = service.deny(application.admin_message_id, 999)

        offered = service.offer_followup(denied.application.id, 555)
        requested = service.respond_to_followup(555, True)
        duplicate = service.respond_to_followup(555, False)

        self.assertEqual(offered.status, "offered")
        self.assertEqual(requested.status, "requested")
        self.assertIsNotNone(requested.responded_at)
        self.assertIsNone(duplicate)
        self.assertEqual(service.list_undelivered_followups(), [requested])
        service.mark_followup_notified(application.id)
        self.assertEqual(service.list_undelivered_followups(), [])

    def test_pending_list_excludes_completed_applications(self):
        service = self.service(FakeFryClient())
        pending = self.submit(service)
        completed = self.submit(service, user_id=456)
        service.deny(completed.admin_message_id, 999)

        self.assertEqual(service.list_pending(), [pending])

    def test_only_one_open_application_per_discord_user(self):
        service = self.service(FakeFryClient())
        self.submit(service)

        with self.assertRaises(sqlite3.IntegrityError):
            self.submit(service)

    def test_failed_admin_post_closes_application_so_user_can_retry(self):
        service = self.service(FakeFryClient())
        failed = self.submit(service)

        closed = service.fail_submission(failed.id, "Discord post failed")
        retried = self.submit(service)

        self.assertEqual(closed.status, "denied")
        self.assertEqual(closed.decision_note, "Discord post failed")
        self.assertNotEqual(retried.id, failed.id)


if __name__ == "__main__":
    unittest.main()
