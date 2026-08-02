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
