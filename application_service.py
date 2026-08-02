"""Membership application submission and durable review workflow."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from typing import Dict, Optional

from eggbot_db.applications import Application, ApplicationRepository, PlayerDiscordLink
from eggbot_db.repositories import Server, ServerRepository
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
    ):
        self.connection = connection
        self.applications = ApplicationRepository(connection)
        self.servers = servers
        self.fry = fry

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
