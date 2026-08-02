"""Membership application submission and durable review workflow."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from typing import Dict, Optional

from eggbot_db.applications import (
    Application,
    ApplicationFollowup,
    ApplicationRepository,
    PlayerDiscordLink,
)
from eggbot_db.repositories import (
    Server,
    ServerRepository,
    WhitelistAdminActionRepository,
)
from fry_api import FryApiClient, FryErrorCode, FryResult


@dataclass(frozen=True)
class ApplicationAnswers:
    discord_user_id: int
    minecraft_name: str
    over_18: bool
    source: str
    sponsor_type: Optional[str] = None
    sponsor_identifier: Optional[str] = None


@dataclass(frozen=True)
class ServerWhitelistOutcome:
    status: str
    message: str


@dataclass(frozen=True)
class DecisionResult:
    status: str
    application: Optional[Application]
    servers: Dict[str, ServerWhitelistOutcome]
    message: str = ""


class ApplicationService:
    def __init__(
        self,
        connection: sqlite3.Connection,
        servers: ServerRepository,
        fry: FryApiClient,
        *,
        whitelist_propagation_delays: tuple[float, ...] = (1.0, 2.0, 4.0),
    ):
        self.connection = connection
        self.applications = ApplicationRepository(connection)
        self.servers = servers
        self.fry = fry
        self.whitelist_propagation_delays = whitelist_propagation_delays
        self.whitelist_actions = WhitelistAdminActionRepository(connection)

    def submit(self, answers: ApplicationAnswers) -> Application:
        application = self.applications.create(
            discord_user_id=answers.discord_user_id,
            minecraft_name=answers.minecraft_name.strip(),
            over_18=answers.over_18,
            sponsor_type=answers.sponsor_type,
            sponsor_identifier=(
                answers.sponsor_identifier.strip()
                if answers.sponsor_identifier
                else None
            ),
            source=answers.source.strip(),
        )
        self.connection.commit()
        return application

    def attach_admin_message(
        self,
        application_id: int,
        channel_id: int,
        message_id: int,
    ) -> Application:
        application = self.applications.attach_admin_message(
            application_id, channel_id, message_id
        )
        self.connection.commit()
        return application

    def get_by_admin_message(self, message_id: int) -> Optional[Application]:
        return self.applications.get_by_admin_message(message_id)

    def get_application(self, application_id: int) -> Optional[Application]:
        return self.applications.get(application_id)

    def list_pending(self) -> list[Application]:
        return self.applications.list_pending()

    def mark_role_failure(self, application_id: int, note: str) -> Application:
        application = self.applications.mark_partial_failure(application_id, note)
        self.connection.commit()
        return application

    def fail_submission(self, application_id: int, note: str) -> Application:
        application = self.applications.finish(application_id, 0, "denied", note)
        self.connection.commit()
        return application

    def record_player_link(
        self,
        application_id: int,
        discord_username: str,
        discord_display_name: str,
    ) -> PlayerDiscordLink:
        link = self.applications.record_player_link(
            application_id, discord_username, discord_display_name
        )
        self.connection.commit()
        return link

    def find_player_links(self, query: str) -> list[PlayerDiscordLink]:
        return self.applications.find_player_links(query)

    def offer_followup(
        self, application_id: int, prompt_message_id: int
    ) -> ApplicationFollowup:
        followup = self.applications.offer_followup(
            application_id, prompt_message_id
        )
        self.connection.commit()
        return followup

    def get_followup_by_message(
        self, prompt_message_id: int
    ) -> Optional[ApplicationFollowup]:
        return self.applications.get_followup_by_message(prompt_message_id)

    def respond_to_followup(
        self, prompt_message_id: int, requested: bool
    ) -> Optional[ApplicationFollowup]:
        followup = self.applications.respond_to_followup(
            prompt_message_id, "requested" if requested else "declined"
        )
        self.connection.commit()
        return followup

    def list_undelivered_followups(self) -> list[ApplicationFollowup]:
        return self.applications.list_undelivered_followups()

    def mark_followup_notified(self, application_id: int) -> None:
        self.applications.mark_followup_notified(application_id)
        self.connection.commit()

    async def _whitelist_server(
        self,
        server: Server,
        minecraft_name: str,
    ) -> ServerWhitelistOutcome:
        existing = await self.fry.whitelist_contains(server, minecraft_name)
        if existing.ok and existing.value:
            return ServerWhitelistOutcome("succeeded", "Already whitelisted")
        if not existing.ok:
            return self._failure_outcome(existing)

        added = await self.fry.whitelist_add(server, minecraft_name)
        if added.ok:
            return ServerWhitelistOutcome("succeeded", "Added")
        return self._failure_outcome(added)

    @staticmethod
    def _failure_outcome(result: FryResult) -> ServerWhitelistOutcome:
        if result.error.code == FryErrorCode.UNSUPPORTED:
            return ServerWhitelistOutcome("unsupported", "API unsupported")
        if result.error.code == FryErrorCode.PERMISSION_DENIED:
            return ServerWhitelistOutcome("permission_denied", "Permission denied")
        return ServerWhitelistOutcome("failed", result.error.code.value)

    async def approve(self, admin_message_id: int, reviewer_id: int) -> DecisionResult:
        application = self.applications.get_by_admin_message(admin_message_id)
        if application is None:
            return DecisionResult("not_found", None, {}, "Application not found")
        if application.status in ("approved", "denied"):
            return DecisionResult("already_decided", application, {})
        if not self.applications.claim(application.id, reviewer_id):
            self.connection.commit()
            return DecisionResult("in_progress", application, {})
        self.connection.commit()

        try:
            enabled_servers = self.servers.list_enabled()
            prior = self.applications.whitelist_results(application.id)
            outcomes: Dict[str, ServerWhitelistOutcome] = {}
            pending = []
            pending_servers = []
            for server in enabled_servers:
                if prior.get(server.id) == "succeeded":
                    outcomes[server.name] = ServerWhitelistOutcome(
                        "succeeded", "Previously completed"
                    )
                else:
                    pending_servers.append(server)
                    pending.append(
                        self._whitelist_server(server, application.minecraft_name)
                    )

            completed = await asyncio.gather(*pending, return_exceptions=True)
            for server, outcome in zip(pending_servers, completed):
                if isinstance(outcome, Exception):
                    outcome = ServerWhitelistOutcome("failed", "internal_error")
                outcomes[server.name] = outcome

            for server in enabled_servers:
                outcome = outcomes[server.name]
                self.applications.record_whitelist_result(
                    application.id,
                    server.id,
                    outcome.status,
                    outcome.message,
                )

            all_succeeded = bool(enabled_servers) and all(
                outcome.status == "succeeded" for outcome in outcomes.values()
            )
            status = "approved" if all_succeeded else "partial_failure"
            note = None if all_succeeded else "One or more servers require retry"
            application = self.applications.finish(
                application.id,
                reviewer_id,
                status,
                note,
            )
            self.connection.commit()
            return DecisionResult(status, application, outcomes)
        except Exception:
            self.connection.rollback()
            self.applications.release_claim(application.id)
            self.connection.commit()
            return DecisionResult("failed", application, {}, "Unexpected processing failure")

    async def audit_denied_whitelist(
        self, application: Application
    ) -> Dict[str, ServerWhitelistOutcome]:
        """Read every enabled whitelist and persist the post-denial audit."""
        servers = self.servers.list_enabled()
        checks = await asyncio.gather(
            *(
                self.fry.whitelist_contains(server, application.minecraft_name)
                for server in servers
            ),
            return_exceptions=True,
        )
        outcomes = {}
        for server, check in zip(servers, checks):
            if isinstance(check, Exception):
                outcome = ServerWhitelistOutcome("failed", "internal_error")
            elif check.ok:
                outcome = ServerWhitelistOutcome(
                    "present" if check.value else "absent",
                    "Still whitelisted" if check.value else "Not whitelisted",
                )
            else:
                failure = self._failure_outcome(check)
                outcome = ServerWhitelistOutcome(failure.status, failure.message)
            outcomes[server.name] = outcome
            self.applications.record_denial_whitelist_check(
                application.id, server.id, outcome.status, outcome.message
            )
        self.connection.commit()
        return outcomes

    async def remove_from_whitelists(
        self, minecraft_name: str, requested_by_discord_user_id: int
    ) -> Dict[str, ServerWhitelistOutcome]:
        name = minecraft_name.strip()
        if not name:
            raise ValueError("Minecraft name is required")
        servers = self.servers.list_enabled()
        initial_checks = await asyncio.gather(
            *(self.fry.whitelist_contains(server, name) for server in servers),
            return_exceptions=True,
        )
        present_servers = [
            server
            for server, check in zip(servers, initial_checks)
            if not isinstance(check, Exception) and check.ok and check.value
        ]
        primary = present_servers[0] if present_servers else None
        removal = None
        removal_results = {}
        if primary is not None:
            try:
                removal = await self.fry.whitelist_remove(primary, name)
            except Exception:
                removal = FryResult.failure(FryErrorCode.INTERNAL_ERROR)
            removal_results[primary.id] = removal

        if primary is not None and removal.ok:
            final_checks = initial_checks
            for delay in self.whitelist_propagation_delays:
                if delay:
                    await asyncio.sleep(delay)
                final_checks = await asyncio.gather(
                    *(self.fry.whitelist_contains(server, name) for server in servers),
                    return_exceptions=True,
                )
                if not any(
                    not isinstance(check, Exception) and check.ok and check.value
                    for check in final_checks
                ):
                    break

            remaining = [
                server
                for server, check in zip(servers, final_checks)
                if not isinstance(check, Exception) and check.ok and check.value
            ]
            if remaining:
                targeted = await asyncio.gather(
                    *(self.fry.whitelist_remove(server, name) for server in remaining),
                    return_exceptions=True,
                )
                for server, result in zip(remaining, targeted):
                    if isinstance(result, Exception):
                        result = FryResult.failure(FryErrorCode.INTERNAL_ERROR)
                    removal_results[server.id] = result
                final_checks = await asyncio.gather(
                    *(self.fry.whitelist_contains(server, name) for server in servers),
                    return_exceptions=True,
                )
        else:
            final_checks = initial_checks

        outcomes = {}
        for server, check in zip(servers, final_checks):
            if isinstance(check, Exception):
                outcome = ServerWhitelistOutcome("failed", "internal_error")
            elif not check.ok:
                outcome = self._failure_outcome(check)
            elif check.value:
                attempted = removal_results.get(server.id) or removal
                if attempted is not None and not attempted.ok:
                    failure = self._failure_outcome(attempted)
                    outcome = ServerWhitelistOutcome(
                        failure.status,
                        f"Removal failed: {failure.message}",
                    )
                else:
                    outcome = ServerWhitelistOutcome(
                        "failed",
                        "Still whitelisted after shared and targeted removal",
                    )
            elif server.id in removal_results and removal_results[server.id].ok:
                outcome = ServerWhitelistOutcome(
                    "removed", f"Removed through {server.name} and verified absent"
                )
            elif primary is not None and removal.ok:
                outcome = ServerWhitelistOutcome(
                    "absent", f"Verified absent after removal through {primary.name}"
                )
            else:
                outcome = ServerWhitelistOutcome("absent", "Not whitelisted")
            outcomes[server.name] = outcome
            self.whitelist_actions.record_remove(
                requested_by_discord_user_id=requested_by_discord_user_id,
                minecraft_name=name,
                server_id=server.id,
                status=outcome.status,
                response_message=outcome.message,
            )
        self.connection.commit()
        return outcomes

    async def add_to_whitelists(
        self, minecraft_name: str, requested_by_discord_user_id: int
    ) -> Dict[str, ServerWhitelistOutcome]:
        name = minecraft_name.strip()
        if not name:
            raise ValueError("Minecraft name is required")
        servers = self.servers.list_enabled()
        initial_checks = await asyncio.gather(
            *(self.fry.whitelist_contains(server, name) for server in servers),
            return_exceptions=True,
        )
        absent_servers = [
            server
            for server, check in zip(servers, initial_checks)
            if not isinstance(check, Exception) and check.ok and not check.value
        ]
        primary = absent_servers[0] if absent_servers else None
        addition = None
        addition_results = {}
        if primary is not None:
            try:
                addition = await self.fry.whitelist_add(primary, name)
            except Exception:
                addition = FryResult.failure(FryErrorCode.INTERNAL_ERROR)
            addition_results[primary.id] = addition

        if primary is not None and addition.ok:
            final_checks = initial_checks
            for delay in self.whitelist_propagation_delays:
                if delay:
                    await asyncio.sleep(delay)
                final_checks = await asyncio.gather(
                    *(self.fry.whitelist_contains(server, name) for server in servers),
                    return_exceptions=True,
                )
                if not any(
                    not isinstance(check, Exception) and check.ok and not check.value
                    for check in final_checks
                ):
                    break

            remaining = [
                server
                for server, check in zip(servers, final_checks)
                if not isinstance(check, Exception) and check.ok and not check.value
            ]
            if remaining:
                targeted = await asyncio.gather(
                    *(self.fry.whitelist_add(server, name) for server in remaining),
                    return_exceptions=True,
                )
                for server, result in zip(remaining, targeted):
                    if isinstance(result, Exception):
                        result = FryResult.failure(FryErrorCode.INTERNAL_ERROR)
                    addition_results[server.id] = result
                final_checks = await asyncio.gather(
                    *(self.fry.whitelist_contains(server, name) for server in servers),
                    return_exceptions=True,
                )
        else:
            final_checks = initial_checks

        outcomes = {}
        for server, check in zip(servers, final_checks):
            if isinstance(check, Exception):
                outcome = ServerWhitelistOutcome("failed", "internal_error")
            elif not check.ok:
                outcome = self._failure_outcome(check)
            elif not check.value:
                attempted = addition_results.get(server.id) or addition
                if attempted is not None and not attempted.ok:
                    failure = self._failure_outcome(attempted)
                    outcome = ServerWhitelistOutcome(
                        failure.status,
                        f"Addition failed: {failure.message}",
                    )
                else:
                    outcome = ServerWhitelistOutcome(
                        "failed",
                        "Still absent after shared and targeted addition",
                    )
            elif server.id in addition_results and addition_results[server.id].ok:
                outcome = ServerWhitelistOutcome(
                    "added", f"Added through {server.name} and verified present"
                )
            elif primary is not None and addition.ok:
                outcome = ServerWhitelistOutcome(
                    "present", f"Verified present after addition through {primary.name}"
                )
            else:
                outcome = ServerWhitelistOutcome("present", "Already whitelisted")
            outcomes[server.name] = outcome
            self.whitelist_actions.record_add(
                requested_by_discord_user_id=requested_by_discord_user_id,
                minecraft_name=name,
                server_id=server.id,
                status=outcome.status,
                response_message=outcome.message,
            )
        self.connection.commit()
        return outcomes

    def deny(self, admin_message_id: int, reviewer_id: int) -> DecisionResult:
        application = self.applications.get_by_admin_message(admin_message_id)
        if application is None:
            return DecisionResult("not_found", None, {}, "Application not found")
        if application.status in ("approved", "denied"):
            return DecisionResult("already_decided", application, {})
        if not self.applications.claim(application.id, reviewer_id):
            self.connection.commit()
            return DecisionResult("in_progress", application, {})
        application = self.applications.finish(
            application.id,
            reviewer_id,
            "denied",
        )
        self.connection.commit()
        return DecisionResult("denied", application, {})
