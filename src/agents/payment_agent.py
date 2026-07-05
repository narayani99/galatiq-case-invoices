"""Payment Agent — processes or skips payment based on approval decision.

Pipeline:
    • If APPROVED: call mock_payment() and return PaymentReceipt.
    • If REJECTED: return PaymentReceipt with status='skipped'.
    • Handle payment gateway failures gracefully.

All actions are logged to the audit trail.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from decimal import Decimal

from src.config import get_logger
from src.models.invoice import ApprovalDecision, ExtractedInvoice, PaymentReceipt
from src.tools.payment_tools import mock_payment
from src.tools.audit_tools import log_action

logger = get_logger("agents.payment")


async def process_payment(
    invoice: ExtractedInvoice,
    approval: ApprovalDecision,
) -> PaymentReceipt:
    """Execute or skip payment for an invoice.

    Args:
        invoice: The extracted invoice data.
        approval: The approval decision from the Approval Agent.

    Returns:
        PaymentReceipt with transaction details (or status='skipped').
    """
    start_time = time.time()

    # ------------------------------------------------------------------
    # Rejected → skip payment
    # ------------------------------------------------------------------
    if approval.status != "approved":
        logger.info(
            "Payment skipped for %s (approval status=%s)",
            invoice.invoice_id,
            approval.status,
        )

        receipt = PaymentReceipt(
            invoice_id=invoice.invoice_id,
            transaction_id="N/A",
            vendor_name=invoice.vendor_name,
            amount=invoice.total_amount,
            status="skipped",
            error=f"Invoice was {approval.status}: {approval.reasoning}",
            paid_at=datetime.now(timezone.utc),
        )

        duration_ms = int((time.time() - start_time) * 1000)
        log_action(
            invoice_id=invoice.invoice_id,
            agent_name="payment",
            action="pay",
            result="skipped",
            reasoning=json.dumps({
                "status": "skipped",
                "approval_status": approval.status,
                "reasoning": approval.reasoning,
            }),
            duration_ms=duration_ms,
        )
        return receipt

    # ------------------------------------------------------------------
    # Approved → call mock payment
    # ------------------------------------------------------------------
    try:
        result = mock_payment(
            vendor=invoice.vendor_name,
            amount=float(invoice.total_amount),
            invoice_id=invoice.invoice_id,
        )

        receipt = PaymentReceipt(
            invoice_id=invoice.invoice_id,
            transaction_id=result["transaction_id"],
            vendor_name=invoice.vendor_name,
            amount=invoice.total_amount,
            status=result["status"],
            error=result.get("error"),
            paid_at=datetime.now(timezone.utc),
        )

        duration_ms = int((time.time() - start_time) * 1000)
        log_action(
            invoice_id=invoice.invoice_id,
            agent_name="payment",
            action="pay",
            result=result["status"],
            reasoning=json.dumps({
                "transaction_id": result["transaction_id"],
                "vendor": invoice.vendor_name,
                "amount": float(invoice.total_amount),
                "status": result["status"],
                "timestamp": result.get("timestamp"),
            }),
            error_msg=result.get("error"),
            duration_ms=duration_ms,
        )

        if result["status"] == "success":
            logger.info(
                "Payment successful: %s → %s ($%.2f) txn=%s",
                invoice.invoice_id,
                invoice.vendor_name,
                float(invoice.total_amount),
                result["transaction_id"],
            )
        else:
            logger.warning(
                "Payment failed: %s — %s",
                invoice.invoice_id,
                result.get("error", "unknown error"),
            )

        return receipt

    except Exception as exc:
        logger.error(
            "Payment processing error for %s: %s",
            invoice.invoice_id,
            exc,
        )

        receipt = PaymentReceipt(
            invoice_id=invoice.invoice_id,
            transaction_id="N/A",
            vendor_name=invoice.vendor_name,
            amount=invoice.total_amount,
            status="failure",
            error=str(exc),
            paid_at=datetime.now(timezone.utc),
        )

        duration_ms = int((time.time() - start_time) * 1000)
        log_action(
            invoice_id=invoice.invoice_id,
            agent_name="payment",
            action="pay",
            result="failure",
            reasoning=json.dumps({"error": str(exc)}),
            error_msg=str(exc),
            duration_ms=duration_ms,
        )

        return receipt
