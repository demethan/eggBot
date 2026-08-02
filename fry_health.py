"""Durable Fry API connectivity monitoring and incident state transitions."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


FAILURE_THRESHOLD = 2


@dataclass(frozen=True)
class FryHealthTransition:
    action: str
    incident_id: Optional[int] = None
    started_at: Optional[datetime] = None
    recovered_at: Optional[datetime] = None
    affected_servers: tuple[str, ...] = ()
    all_servers_unreachable: bool = False
    last_error: Optional[str] = None
    alert_channel_id: Optional[int] = None
    alert_message_id: Optional[int] = None


class FryHealthService:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def record_cycle(
        self,
        results: dict[str, Optional[str]],
        *,
        observed_at: Optional[datetime] = None,
    ) -> FryHealthTransition:
        timestamp = (observed_at or datetime.now(timezone.utc)).astimezone(
            timezone.utc
        ).isoformat(timespec="seconds")
        servers = self.connection.execute(
            "SELECT id, name FROM servers WHERE enabled = 1 ORDER BY name COLLATE NOCASE"
        ).fetchall()
        server_by_name = {row["name"].casefold(): row for row in servers}
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            for supplied_name, error in results.items():
                server = server_by_name.get(supplied_name.strip().casefold())
                if server is None:
                    continue
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO fry_health_state(server_id)
                    VALUES (?)
                    """,
                    (server["id"],),
                )
                if error is None:
                    self.connection.execute(
                        """
                        UPDATE fry_health_state
                        SET consecutive_failures = 0, outage_started_at = NULL,
                            last_success_at = ?, last_error = NULL
                        WHERE server_id = ?
                        """,
                        (timestamp, server["id"]),
                    )
                else:
                    self.connection.execute(
                        """
                        UPDATE fry_health_state
                        SET consecutive_failures = consecutive_failures + 1,
                            outage_started_at = COALESCE(outage_started_at, ?),
                            last_failure_at = ?, last_error = ?
                        WHERE server_id = ?
                        """,
                        (timestamp, timestamp, str(error)[:500], server["id"]),
                    )

            unhealthy = self.connection.execute(
                """
                SELECT s.name, h.outage_started_at, h.last_error
                FROM fry_health_state AS h
                JOIN servers AS s ON s.id = h.server_id
                WHERE s.enabled = 1 AND h.consecutive_failures >= ?
                ORDER BY s.name COLLATE NOCASE
                """,
                (FAILURE_THRESHOLD,),
            ).fetchall()
            affected = tuple(row["name"] for row in unhealthy)
            affected_json = json.dumps(affected)
            last_error = next(
                (row["last_error"] for row in reversed(unhealthy) if row["last_error"]),
                None,
            )
            incident = self.connection.execute(
                """
                SELECT * FROM fry_connectivity_incidents
                WHERE recovered_at IS NULL LIMIT 1
                """
            ).fetchone()
            action = "none"
            if affected:
                started_at = min(row["outage_started_at"] for row in unhealthy)
                if incident is None:
                    incident_id = self.connection.execute(
                        """
                        INSERT INTO fry_connectivity_incidents(
                            started_at, affected_servers_json, last_error
                        ) VALUES (?, ?, ?)
                        """,
                        (started_at, affected_json, last_error),
                    ).lastrowid
                    action = "alert"
                    incident = self.connection.execute(
                        "SELECT * FROM fry_connectivity_incidents WHERE id = ?",
                        (incident_id,),
                    ).fetchone()
                else:
                    previous_affected = tuple(
                        json.loads(incident["affected_servers_json"])
                    )
                    self.connection.execute(
                        """
                        UPDATE fry_connectivity_incidents
                        SET affected_servers_json = ?, last_error = ?
                        WHERE id = ?
                        """,
                        (affected_json, last_error, incident["id"]),
                    )
                    if incident["alerted_at"] is None:
                        action = "alert"
                    elif previous_affected != affected:
                        action = "update"
            elif incident is not None:
                self.connection.execute(
                    """
                    UPDATE fry_connectivity_incidents SET recovered_at = ?
                    WHERE id = ?
                    """,
                    (timestamp, incident["id"]),
                )
                action = "recovery"
            else:
                pending_recovery = self.connection.execute(
                    """
                    SELECT * FROM fry_connectivity_incidents
                    WHERE recovered_at IS NOT NULL AND recovery_notified_at IS NULL
                    ORDER BY recovered_at LIMIT 1
                    """
                ).fetchone()
                if pending_recovery is not None:
                    incident = pending_recovery
                    timestamp = incident["recovered_at"]
                    action = "recovery"
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

        if action == "none":
            return FryHealthTransition("none")
        recovered_at = timestamp if action == "recovery" else None
        incident_affected = affected
        if action == "recovery":
            incident_affected = tuple(json.loads(incident["affected_servers_json"]))
            last_error = incident["last_error"]
        return FryHealthTransition(
            action=action,
            incident_id=incident["id"],
            started_at=datetime.fromisoformat(incident["started_at"]),
            recovered_at=(datetime.fromisoformat(recovered_at) if recovered_at else None),
            affected_servers=incident_affected,
            all_servers_unreachable=(
                bool(servers) and len(incident_affected) == len(servers)
            ),
            last_error=last_error,
            alert_channel_id=incident["alert_channel_id"],
            alert_message_id=incident["alert_message_id"],
        )

    def mark_alert_sent(
        self,
        incident_id: int,
        *,
        channel_id: int,
        message_id: int,
        alerted_at: Optional[datetime] = None,
    ) -> None:
        timestamp = (alerted_at or datetime.now(timezone.utc)).astimezone(
            timezone.utc
        ).isoformat(timespec="seconds")
        self.connection.execute(
            """
            UPDATE fry_connectivity_incidents
            SET alerted_at = ?, alert_channel_id = ?, alert_message_id = ?
            WHERE id = ?
            """,
            (timestamp, channel_id, message_id, incident_id),
        )
        self.connection.commit()

    def mark_recovery_sent(
        self,
        incident_id: int,
        *,
        notified_at: Optional[datetime] = None,
    ) -> None:
        timestamp = (notified_at or datetime.now(timezone.utc)).astimezone(
            timezone.utc
        ).isoformat(timespec="seconds")
        self.connection.execute(
            """
            UPDATE fry_connectivity_incidents SET recovery_notified_at = ?
            WHERE id = ?
            """,
            (timestamp, incident_id),
        )
        self.connection.commit()
