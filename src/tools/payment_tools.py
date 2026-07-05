"""Mock payment tool.

Simulates a payment gateway with a 95% success rate.
Implements idempotency by checking audit_log for prior successful payments.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timezone

from src.config import get_logger
from src.database import get_connection

logger = get_logger("tools.payment")


def mock_payment(vendor: str, amount: float, invoice_id: str) -> dict:
    """Simulate a payment API call.

    Idempotency: if a successful payment already exists for *invoice_id* in the
    audit_log, the original transaction details are returned instead of
    creating a duplicate.

    Args:
        vendor: Vendor name receiving the payment.
        amount: Dollar amount to pay.
        invoice_id: Unique invoice identifier (idempotency key).

    Returns:
        dict with keys:
            transaction_id, vendor, amount, invoice_id,
            status ('success' | 'failure'),
            error (None or str),
            timestamp (ISO-8601 string)
    """
    # ------------------------------------------------------------------
    # Idempotency check – look for an existing successful payment
    # ------------------------------------------------------------------
    try:
        with get_connection() as conn:
            cursor = conn.execute(
                "SELECT reasoning FROM audit_log "
                "WHERE invoice_id = ? AND agent_name = 'payment' "
                "AND action = 'pay' AND result = 'success' "
                "ORDER BY timestamp DESC LIMIT 1",
                (invoice_id,),
            )
            row = cursor.fetchone()

        if row is not None:
            import json

            try:
                existing = json.loads(row[0]) if row[0] else {}
            except (json.JSONDecodeError, TypeError):
                existing = {}

            if existing.get("transaction_id"):
                logger.info(
                    "Idempotent hit: returning existing payment for %s",
                    invoice_id,
                )
                return {
                    "transaction_id": existing["transaction_id"],
                    "vendor": vendor,
                    "amount": amount,
                    "invoice_id": invoice_id,
                    "status": "success",
                    "error": None,
                    "timestamp": existing.get(
                        "timestamp", datetime.now(timezone.utc).isoformat()
                    ),
                }
    except Exception as exc:
        # Non-critical – continue with new payment attempt
        logger.warning("Idempotency check failed: %s", exc)

    # ------------------------------------------------------------------
    # Simulate payment
    # ------------------------------------------------------------------
    transaction_id = f"TXN-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    if random.random() < 0.05:
        logger.warning(
            "Simulated payment failure for invoice %s (vendor=%s, amount=%.2f)",
            invoice_id,
            vendor,
            amount,
        )
        return {
            "transaction_id": transaction_id,
            "vendor": vendor,
            "amount": amount,
            "invoice_id": invoice_id,
            "status": "failure",
            "error": "Simulated payment gateway timeout",
            "timestamp": now,
        }

    logger.info(
        "Payment successful: %s → %s ($%.2f)",
        invoice_id,
        vendor,
        amount,
    )
    return {
        "transaction_id": transaction_id,
        "vendor": vendor,
        "amount": amount,
        "invoice_id": invoice_id,
        "status": "success",
        "error": None,
        "timestamp": now,
    }
