"""
Audit logging utilities for the Galatiq Invoice Processing System.

Provides the ``AuditLogger`` class for recording, querying, and summarising
processing actions in the ``audit_log`` table.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.config import get_logger
from src.database import get_connection
from src.models.audit import AuditEntry

logger = get_logger("audit")


class AuditLogger:
    """Write and read audit entries from the ``audit_log`` SQLite table.

    All methods are synchronous (using the standard ``sqlite3`` driver)
    to keep the implementation straightforward for CLI and agent usage.
    """

    # ── Write ─────────────────────────────────────────────────────────

    def log_invoice_processing(
        self,
        invoice_id: str,
        agent_name: str,
        action: str,
        result: str,
        reasoning: Optional[Dict[str, Any]] = None,
        error_msg: Optional[str] = None,
        duration_ms: int = 0,
        tokens_used: Optional[int] = None,
    ) -> int:
        """Insert a new audit log entry.

        Args:
            invoice_id: The invoice being processed.
            agent_name: Agent responsible (``"ingestion"``, ``"validation"``, etc.).
            action: Action performed (``"extract"``, ``"validate"``, etc.).
            result: Outcome (``"success"``, ``"failure"``, ``"pass"``, ``"flag"``, ``"reject"``).
            reasoning: Structured reasoning dict (serialised to JSON).
            error_msg: Error description if the action failed.
            duration_ms: Wall-clock processing time in milliseconds.
            tokens_used: LLM tokens consumed (optional).

        Returns:
            int: The ``id`` of the newly inserted row.
        """
        if isinstance(reasoning, str):
            reasoning_json = reasoning
        else:
            reasoning_json = json.dumps(reasoning) if reasoning else None

        with get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO audit_log
                    (invoice_id, agent_name, action, result,
                     reasoning, error_msg, duration_ms, tokens_used)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    invoice_id,
                    agent_name,
                    action,
                    result,
                    reasoning_json,
                    error_msg,
                    duration_ms,
                    tokens_used,
                ),
            )
            row_id: int = cursor.lastrowid  # type: ignore[assignment]

        logger.info(
            "Audit entry recorded",
            extra={
                "invoice_id": invoice_id,
                "agent_name": agent_name,
                "action": action,
                "duration_ms": duration_ms,
            },
        )
        return row_id

    # ── Read ──────────────────────────────────────────────────────────

    def get_processing_history(self, invoice_id: str) -> List[AuditEntry]:
        """Return all audit entries for a given invoice, ordered by timestamp.

        Args:
            invoice_id: The invoice to query.

        Returns:
            List[AuditEntry]: Chronologically ordered audit entries.
        """
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, invoice_id, timestamp, agent_name,
                       action, result, reasoning, error_msg, duration_ms
                FROM audit_log
                WHERE invoice_id = ?
                ORDER BY timestamp ASC
                """,
                (invoice_id,),
            ).fetchall()

        entries: List[AuditEntry] = []
        for row in rows:
            reasoning_dict: Dict[str, Any] = {}
            if row["reasoning"]:
                try:
                    loaded = json.loads(row["reasoning"])
                    if isinstance(loaded, str):
                        try:
                            reasoning_dict = json.loads(loaded)
                            if not isinstance(reasoning_dict, dict):
                                reasoning_dict = {"raw": reasoning_dict}
                        except (json.JSONDecodeError, TypeError):
                            reasoning_dict = {"raw": loaded}
                    elif isinstance(loaded, dict):
                        reasoning_dict = loaded
                    else:
                        reasoning_dict = {"raw": loaded}
                except (json.JSONDecodeError, TypeError):
                    reasoning_dict = {"raw": row["reasoning"]}

            entries.append(
                AuditEntry(
                    id=row["id"],
                    invoice_id=row["invoice_id"],
                    timestamp=datetime.fromisoformat(row["timestamp"])
                    if isinstance(row["timestamp"], str)
                    else row["timestamp"],
                    agent_name=row["agent_name"],
                    action=row["action"],
                    result=row["result"],
                    reasoning=reasoning_dict,
                    error_msg=row["error_msg"],
                    duration_ms=row["duration_ms"] or 0,
                )
            )

        return entries

    # ── Summary ───────────────────────────────────────────────────────

    def get_audit_summary(self) -> Dict[str, Any]:
        """Return aggregate statistics from the audit log.

        Returns:
            dict: Summary containing counts by result, by agent, average
            duration, total entries, and latest timestamp.
        """
        with get_connection() as conn:
            # Total entries
            total = conn.execute(
                "SELECT COUNT(*) AS cnt FROM audit_log"
            ).fetchone()["cnt"]

            # Counts by result
            result_rows = conn.execute(
                """
                SELECT result, COUNT(*) AS cnt
                FROM audit_log
                GROUP BY result
                """
            ).fetchall()
            by_result = {r["result"]: r["cnt"] for r in result_rows}

            # Counts by agent
            agent_rows = conn.execute(
                """
                SELECT agent_name, COUNT(*) AS cnt
                FROM audit_log
                GROUP BY agent_name
                """
            ).fetchall()
            by_agent = {r["agent_name"]: r["cnt"] for r in agent_rows}

            # Average duration
            avg_row = conn.execute(
                "SELECT AVG(duration_ms) AS avg_ms FROM audit_log"
            ).fetchone()
            avg_duration_ms = round(avg_row["avg_ms"] or 0, 2)

            # Latest timestamp
            latest_row = conn.execute(
                "SELECT MAX(timestamp) AS latest FROM audit_log"
            ).fetchone()
            latest_timestamp = latest_row["latest"]

            # Unique invoices processed
            unique_invoices = conn.execute(
                "SELECT COUNT(DISTINCT invoice_id) AS cnt FROM audit_log"
            ).fetchone()["cnt"]

        return {
            "total_entries": total,
            "unique_invoices": unique_invoices,
            "by_result": by_result,
            "by_agent": by_agent,
            "avg_duration_ms": avg_duration_ms,
            "latest_timestamp": latest_timestamp,
        }
