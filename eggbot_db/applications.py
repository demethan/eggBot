"""Persistence repository for membership applications and decisions."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from .database import utc_now


REVIEWABLE_STATUSES = ("pending", "needs_information", "partial_failure")


@dataclass(frozen=True)
class Application:
    id: int
    discord_user_id: int
    minecraft_name: str
    over_18: bool
    sponsor_type: Optional[str]
    sponsor_identifier: Optional[str]
    source: str
    status: str
    admin_channel_id: Optional[int]
    admin_message_id: Optional[int]
    submitted_at: str
    decided_at: Optional[str]
    reviewer_discord_user_id: Optional[int]
    decision_note: Optional[str]


@dataclass(frozen=True)
class PlayerDiscordLink:
    id: int
    discord_user_id: int
    discord_username: str
    discord_display_name: str
    minecraft_name: str
    application_id: int
    linked_at: str
    updated_at: str


@dataclass(frozen=True)
class ApplicationFollowup:
    application_id: int
    prompt_message_id: int
    status: str
    offered_at: str
    responded_at: Optional[str]
    notified_at: Optional[str]


class ApplicationRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Application:
        return Application(
            id=row["id"],
            discord_user_id=row["discord_user_id"],
            minecraft_name=row["minecraft_name"],
            over_18=bool(row["over_18"]),
            sponsor_type=row["sponsor_type"],
            sponsor_identifier=row["sponsor_identifier"],
            source=row["source"],
            status=row["status"],
            admin_channel_id=row["admin_channel_id"],
            admin_message_id=row["admin_message_id"],
            submitted_at=row["submitted_at"],
            decided_at=row["decided_at"],
            reviewer_discord_user_id=row["reviewer_discord_user_id"],
            decision_note=row["decision_note"],
        )

    def create(
        self,
        *,
        discord_user_id: int,
        minecraft_name: str,
        over_18: bool,
        source: str,
        sponsor_type: Optional[str] = None,
        sponsor_identifier: Optional[str] = None,
    ) -> Application:
        cursor = self.connection.execute(
            """
            INSERT INTO applications(
                discord_user_id, minecraft_name, over_18,
                sponsor_type, sponsor_identifier, source, submitted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                discord_user_id,
                minecraft_name,
                int(over_18),
                sponsor_type,
                sponsor_identifier,
                source,
                utc_now(),
            ),
        )
        return self.get(cursor.lastrowid)

    def get(self, application_id: int) -> Optional[Application]:
        row = self.connection.execute(
            "SELECT * FROM applications WHERE id = ?", (application_id,)
        ).fetchone()
        return None if row is None else self._from_row(row)

    def get_by_admin_message(self, message_id: int) -> Optional[Application]:
        row = self.connection.execute(
            "SELECT * FROM applications WHERE admin_message_id = ?", (message_id,)
        ).fetchone()
        return None if row is None else self._from_row(row)

    def list_pending(self) -> list[Application]:
        rows = self.connection.execute(
            """
            SELECT * FROM applications
            WHERE status = 'pending' AND admin_message_id IS NOT NULL
            ORDER BY submitted_at
            """
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def attach_admin_message(
        self,
        application_id: int,
        channel_id: int,
        message_id: int,
    ) -> Application:
        self.connection.execute(
            """
            UPDATE applications
            SET admin_channel_id = ?, admin_message_id = ?
            WHERE id = ?
            """,
            (channel_id, message_id, application_id),
        )
        return self.get(application_id)

    def claim(
        self,
        application_id: int,
        reviewer_discord_user_id: int,
        *,
        stale_after: timedelta = timedelta(minutes=10),
    ) -> bool:
        stale_before = (
            datetime.now(timezone.utc) - stale_after
        ).isoformat(timespec="seconds")
        self.connection.execute(
            "DELETE FROM application_decision_locks WHERE claimed_at < ?",
            (stale_before,),
        )
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO application_decision_locks(
                application_id, reviewer_discord_user_id, claimed_at
            )
            SELECT id, ?, ? FROM applications
            WHERE id = ? AND status IN ('pending', 'needs_information', 'partial_failure')
            """,
            (reviewer_discord_user_id, utc_now(), application_id),
        )
        return cursor.rowcount == 1

    def release_claim(self, application_id: int) -> None:
        self.connection.execute(
            "DELETE FROM application_decision_locks WHERE application_id = ?",
            (application_id,),
        )

    def finish(
        self,
        application_id: int,
        reviewer_discord_user_id: int,
        status: str,
        note: Optional[str] = None,
    ) -> Application:
        self.connection.execute(
            """
            UPDATE applications
            SET status = ?, decided_at = ?, reviewer_discord_user_id = ?, decision_note = ?
            WHERE id = ?
            """,
            (status, utc_now(), reviewer_discord_user_id, note, application_id),
        )
        self.release_claim(application_id)
        return self.get(application_id)

    def mark_partial_failure(self, application_id: int, note: str) -> Application:
        """Return a completed whitelist decision to a retryable state."""
        self.connection.execute(
            """
            UPDATE applications
            SET status = 'partial_failure', decided_at = NULL, decision_note = ?
            WHERE id = ? AND status = 'approved'
            """,
            (note, application_id),
        )
        return self.get(application_id)

    def whitelist_results(self, application_id: int) -> dict[int, str]:
        rows = self.connection.execute(
            """
            SELECT server_id, status FROM application_whitelist_results
            WHERE application_id = ?
            """,
            (application_id,),
        ).fetchall()
        return {row["server_id"]: row["status"] for row in rows}

    def record_whitelist_result(
        self,
        application_id: int,
        server_id: int,
        status: str,
        response_message: Optional[str] = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO application_whitelist_results(
                application_id, server_id, attempted_at, status, response_message
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(application_id, server_id) DO UPDATE SET
                attempted_at = excluded.attempted_at,
                status = excluded.status,
                response_message = excluded.response_message
            """,
            (application_id, server_id, utc_now(), status, response_message),
        )

    def record_denial_whitelist_check(
        self,
        application_id: int,
        server_id: int,
        status: str,
        response_message: Optional[str] = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO application_denial_whitelist_checks(
                application_id, server_id, checked_at, status, response_message
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(application_id, server_id) DO UPDATE SET
                checked_at = excluded.checked_at,
                status = excluded.status,
                response_message = excluded.response_message
            """,
            (application_id, server_id, utc_now(), status, response_message),
        )

    def denial_whitelist_checks(self, application_id: int) -> dict[int, str]:
        rows = self.connection.execute(
            """
            SELECT server_id, status FROM application_denial_whitelist_checks
            WHERE application_id = ?
            """,
            (application_id,),
        ).fetchall()
        return {row["server_id"]: row["status"] for row in rows}

    def offer_followup(
        self, application_id: int, prompt_message_id: int
    ) -> ApplicationFollowup:
        self.connection.execute(
            """
            INSERT INTO application_followup_requests(
                application_id, prompt_message_id, status, offered_at
            ) VALUES (?, ?, 'offered', ?)
            ON CONFLICT(application_id) DO UPDATE SET
                prompt_message_id = excluded.prompt_message_id,
                status = 'offered',
                offered_at = excluded.offered_at,
                responded_at = NULL
            """,
            (application_id, prompt_message_id, utc_now()),
        )
        return self.get_followup_by_message(prompt_message_id)

    def get_followup_by_message(
        self, prompt_message_id: int
    ) -> Optional[ApplicationFollowup]:
        row = self.connection.execute(
            """
            SELECT * FROM application_followup_requests
            WHERE prompt_message_id = ?
            """,
            (prompt_message_id,),
        ).fetchone()
        return None if row is None else ApplicationFollowup(**dict(row))

    def respond_to_followup(
        self, prompt_message_id: int, status: str
    ) -> Optional[ApplicationFollowup]:
        cursor = self.connection.execute(
            """
            UPDATE application_followup_requests
            SET status = ?, responded_at = ?
            WHERE prompt_message_id = ? AND status = 'offered'
            """,
            (status, utc_now(), prompt_message_id),
        )
        if cursor.rowcount != 1:
            return None
        return self.get_followup_by_message(prompt_message_id)

    def list_undelivered_followups(self) -> list[ApplicationFollowup]:
        rows = self.connection.execute(
            """
            SELECT * FROM application_followup_requests
            WHERE status = 'requested' AND notified_at IS NULL
            ORDER BY responded_at
            """
        ).fetchall()
        return [ApplicationFollowup(**dict(row)) for row in rows]

    def mark_followup_notified(self, application_id: int) -> None:
        self.connection.execute(
            """
            UPDATE application_followup_requests
            SET notified_at = ?
            WHERE application_id = ? AND status = 'requested'
            """,
            (utc_now(), application_id),
        )

    def record_player_link(
        self,
        application_id: int,
        discord_username: str,
        discord_display_name: str,
    ) -> PlayerDiscordLink:
        application = self.get(application_id)
        if application is None or application.status != "approved":
            raise ValueError("only approved applications can create player links")
        now = utc_now()
        self.connection.execute(
            """
            INSERT INTO player_discord_links(
                discord_user_id, discord_username, discord_display_name,
                minecraft_name, application_id, linked_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(discord_user_id, minecraft_name) DO UPDATE SET
                discord_username = excluded.discord_username,
                discord_display_name = excluded.discord_display_name,
                application_id = excluded.application_id,
                updated_at = excluded.updated_at
            """,
            (
                application.discord_user_id,
                discord_username,
                discord_display_name,
                application.minecraft_name,
                application.id,
                now,
                now,
            ),
        )
        return self.find_player_links(str(application.discord_user_id))[0]

    def find_player_links(self, query: str) -> list[PlayerDiscordLink]:
        normalized = query.strip()
        discord_id = int(normalized) if normalized.isdecimal() else -1
        rows = self.connection.execute(
            """
            SELECT * FROM player_discord_links
            WHERE discord_user_id = ?
               OR minecraft_name = ? COLLATE NOCASE
               OR discord_username = ? COLLATE NOCASE
               OR discord_display_name = ? COLLATE NOCASE
            ORDER BY updated_at DESC
            """,
            (discord_id, normalized, normalized, normalized),
        ).fetchall()
        return [PlayerDiscordLink(**dict(row)) for row in rows]
