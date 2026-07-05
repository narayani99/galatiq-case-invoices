"""Audit tools — thin wrappers around AuditLogger for agent use.

These functions provide a simplified interface for agents to record
actions and retrieve processing history without directly importing
the AuditLogger class.
"""

from __future__ import annotations

import json
from typing import Optional

from src.config import get_logger
from src.audit import AuditLogger

logger = get_logger("tools.audit")

# Module-level singleton so all agents share one logger instance
_audit_logger = AuditLogger()


def log_action(
    invoice_id: str,
    agent_name: str,
    action: str,
    result: str,
    reasoning: Optional[str] = None,
    error_msg: Optional[str] = None,
    duration_ms: int = 0,
) -> None:
    """Record an agent action in the audit log.

    Args:
        invoice_id: Invoice being processed.
        agent_name: Name of the agent ('ingestion', 'validation', etc.).
        action: Action performed ('extract', 'validate', 'approve', 'pay').
        result: Outcome ('success', 'failure', 'pass', 'flag', 'reject').
        reasoning: Optional JSON-serialisable reasoning string.
        error_msg: Optional error message if the action failed.
        duration_ms: Wall-clock time for the action in milliseconds.
    """
    try:
        _audit_logger.log_invoice_processing(
            invoice_id=invoice_id,
            agent_name=agent_name,
            action=action,
            result=result,
            reasoning=reasoning,
            error_msg=error_msg,
            duration_ms=duration_ms,
        )
        logger.debug(
            "Audit logged: invoice=%s agent=%s action=%s result=%s",
            invoice_id,
            agent_name,
            action,
            result,
        )
    except Exception as exc:
        # Audit logging must never crash the pipeline
        logger.error("Failed to write audit log entry: %s", exc)


def get_history(invoice_id: str) -> list[dict]:
    """Retrieve the full processing history for an invoice.

    Args:
        invoice_id: Invoice to look up.

    Returns:
        List of audit entry dicts, ordered chronologically.
    """
    try:
        entries = _audit_logger.get_processing_history(invoice_id)
        # Normalise to plain dicts (handles both Pydantic models and dicts)
        result = []
        for entry in entries:
            if hasattr(entry, "model_dump"):
                result.append(entry.model_dump())
            elif hasattr(entry, "dict"):
                result.append(entry.dict())
            elif isinstance(entry, dict):
                result.append(entry)
            else:
                result.append(dict(entry))
        return result
    except Exception as exc:
        logger.error("Failed to retrieve audit history for %s: %s", invoice_id, exc)
        return []
