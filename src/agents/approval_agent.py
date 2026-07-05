"""Approval Agent — makes approval/rejection decisions on validated invoices.

Business rules:
    • validation.status == 'reject'  → auto-reject immediately
    • amount ≤ $5 000 AND PASS       → auto-approve
    • $5 000 < amount ≤ $10 000 AND PASS AND vendor_risk < 0.5 → auto-approve
    • amount > $10 000               → ALWAYS trigger LLM reasoning + reflection
    • Everything else (FLAG, mid-range + high risk, etc.) → LLM reasoning

Includes duplicate-invoice detection (same vendor in last 30 days).

Graceful degradation: if the LLM is unavailable for high-value invoices,
the agent uses deterministic rules and logs that LLM was skipped.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from decimal import Decimal

from src.config import Settings, get_llm_client, get_logger
from src.database import get_connection
from src.models.invoice import ApprovalDecision, ExtractedInvoice, ValidationResult
from src.tools.audit_tools import log_action

logger = get_logger("agents.approval")

# ---------------------------------------------------------------------------
# Prompts (from 03_SPECIFICATIONS.md)
# ---------------------------------------------------------------------------

APPROVAL_PROMPT = """\
This is a high-value invoice requiring VP-level review.

Invoice Details:
- Vendor: {vendor_name}
- Amount: ${amount}
- Items: {items_list}
- Validation Status: {validation_status}

Vendor History:
- Risk Score: {risk_score}
- Previous orders: {count}
- Last transaction: {date}

Question: Should we approve this invoice?

Consider:
1. Vendor reliability and history
2. Invoice legitimacy (matches typical orders?)
3. Fraud indicators
4. Payment terms and conditions

Recommendation (APPROVE or REJECT) with reasoning:
"""

REFLECTION_PROMPT = """\
Your recommendation is {recommendation}. Walk me through the top 3 risks \
and how they factor into your decision. Then confirm or revise your recommendation.

Return JSON:
{{
  "status": "approved|rejected",
  "reasoning": "...",
  "top_risks": ["...", "...", "..."]
}}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_json_from_text(text: str) -> dict | None:
    """Extract the first JSON object from LLM response text."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    import re
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, TypeError):
            pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def _call_llm(prompt: str) -> dict | None:
    """Call the LLM and return parsed JSON, or None on failure."""
    try:
        client = get_llm_client()
        settings = Settings()

        response = client.chat.completions.create(
            model=settings.GROK_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an expert invoice approval agent acting as a "
                        "VP of Finance. Always respond with valid JSON."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
        )

        content = response.choices[0].message.content
        return _extract_json_from_text(content)

    except Exception as exc:
        logger.warning("LLM call failed during approval: %s", exc)
        return None


def check_duplicate_invoice(invoice_id: str, vendor_name: str) -> dict:
    """Check for recent invoices from the same vendor in the last 30 days.

    Returns:
        dict with keys: count_last_30_days, avg_amount, last_date
    """
    try:
        with get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT COUNT(*), AVG(CAST(
                  json_extract(reasoning, '$.amount') AS REAL
                )), MAX(timestamp)
                FROM audit_log
                WHERE agent_name = 'ingestion'
                AND action = 'extract'
                AND result = 'success'
                AND LOWER(json_extract(reasoning, '$.vendor_name')) = LOWER(?)
                AND timestamp >= datetime('now', '-30 days')
                AND invoice_id != ?
                """,
                (vendor_name, invoice_id),
            )
            row = cursor.fetchone()

        if row and row[0]:
            return {
                "count_last_30_days": row[0],
                "avg_amount": round(float(row[1] or 0), 2),
                "last_date": row[2] or "N/A",
            }
    except Exception as exc:
        logger.warning("Duplicate check failed: %s", exc)

    return {
        "count_last_30_days": 0,
        "avg_amount": 0.0,
        "last_date": "N/A",
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def approve_invoice(
    invoice: ExtractedInvoice,
    validation: ValidationResult,
) -> ApprovalDecision:
    """Decide whether to approve or reject a validated invoice.

    Uses tiered business rules with LLM escalation for high-value invoices.

    Args:
        invoice: Extracted invoice data.
        validation: Validation result from the Validation Agent.

    Returns:
        ApprovalDecision with status, reasoning, and rule_applied.
    """
    start_time = time.time()
    amount = float(invoice.total_amount)
    vendor_risk = float(validation.vendor_risk or Decimal("0.5"))

    # ------------------------------------------------------------------
    # Duplicate check
    # ------------------------------------------------------------------
    dup_info = check_duplicate_invoice(invoice.invoice_id, invoice.vendor_name)
    if dup_info["count_last_30_days"] > 0:
        logger.info(
            "Duplicate check: vendor %s has %d invoices in last 30 days",
            invoice.vendor_name,
            dup_info["count_last_30_days"],
        )

    # ------------------------------------------------------------------
    # Rule 1: Validation REJECT → auto-reject
    # ------------------------------------------------------------------
    if validation.status == "reject":
        decision = ApprovalDecision(
            invoice_id=invoice.invoice_id,
            status="rejected",
            reasoning=(
                f"Auto-rejected: validation status is REJECT. "
                f"Reason: {validation.reasoning}"
            ),
            rule_applied="auto_reject_validation_failed",
            approved_at=datetime.now(timezone.utc),
        )
        duration_ms = int((time.time() - start_time) * 1000)
        log_action(
            invoice_id=invoice.invoice_id,
            agent_name="approval",
            action="approve",
            result="reject",
            reasoning=json.dumps({
                "status": "rejected",
                "rule": "auto_reject_validation_failed",
                "validation_status": validation.status,
                "reasoning": decision.reasoning,
            }),
            duration_ms=duration_ms,
        )
        logger.info("Approval: %s → rejected (validation REJECT)", invoice.invoice_id)
        return decision

    # ------------------------------------------------------------------
    # Rule 2: ≤ $5K AND PASS → auto-approve
    # ------------------------------------------------------------------
    if amount <= 5000 and validation.status == "pass":
        decision = ApprovalDecision(
            invoice_id=invoice.invoice_id,
            status="approved",
            reasoning=(
                f"Auto-approved: amount ${amount:,.2f} ≤ $5,000 threshold "
                f"and validation passed."
            ),
            rule_applied="auto_approve_low_value",
            approved_at=datetime.now(timezone.utc),
        )
        duration_ms = int((time.time() - start_time) * 1000)
        log_action(
            invoice_id=invoice.invoice_id,
            agent_name="approval",
            action="approve",
            result="approved",
            reasoning=json.dumps({
                "status": "approved",
                "rule": "auto_approve_low_value",
                "amount": amount,
                "reasoning": decision.reasoning,
            }),
            duration_ms=duration_ms,
        )
        logger.info("Approval: %s → approved (low value)", invoice.invoice_id)
        return decision

    # ------------------------------------------------------------------
    # Rule 3: $5K–$10K AND PASS AND vendor_risk < 0.5 → auto-approve
    # ------------------------------------------------------------------
    if (
        5000 < amount <= 10000
        and validation.status == "pass"
        and vendor_risk < 0.5
    ):
        decision = ApprovalDecision(
            invoice_id=invoice.invoice_id,
            status="approved",
            reasoning=(
                f"Auto-approved: amount ${amount:,.2f} in mid-range, "
                f"validation passed, vendor risk {vendor_risk:.2f} < 0.5."
            ),
            rule_applied="auto_approve_medium_value",
            approved_at=datetime.now(timezone.utc),
        )
        duration_ms = int((time.time() - start_time) * 1000)
        log_action(
            invoice_id=invoice.invoice_id,
            agent_name="approval",
            action="approve",
            result="approved",
            reasoning=json.dumps({
                "status": "approved",
                "rule": "auto_approve_medium_value",
                "amount": amount,
                "vendor_risk": vendor_risk,
                "reasoning": decision.reasoning,
            }),
            duration_ms=duration_ms,
        )
        logger.info("Approval: %s → approved (medium value)", invoice.invoice_id)
        return decision

    # ------------------------------------------------------------------
    # Rule 4 & catch-all: LLM reasoning required
    # ------------------------------------------------------------------
    rule_label = (
        "manual_review_high_value" if amount > 10000
        else "manual_review_flagged"
    )

    items_list = ", ".join(
        f"{it.item_name} x{it.quantity}" for it in invoice.line_items
    )

    prompt = APPROVAL_PROMPT.format(
        vendor_name=invoice.vendor_name,
        amount=f"{amount:,.2f}",
        items_list=items_list,
        validation_status=validation.status,
        risk_score=f"{vendor_risk:.2f}",
        count=dup_info["count_last_30_days"],
        date=dup_info["last_date"],
    )

    llm_result = _call_llm(prompt)

    if llm_result is not None:
        recommendation = llm_result.get("status", "").lower()
        if recommendation not in ("approved", "rejected"):
            # Normalise common LLM synonyms
            if recommendation in ("approve", "pass", "accept"):
                recommendation = "approved"
            elif recommendation in ("reject", "deny", "decline"):
                recommendation = "rejected"
            else:
                recommendation = "rejected"  # conservative default

        llm_reasoning = llm_result.get("reasoning", "")

        # Reflection loop for high-value invoices
        if amount > 10000:
            logger.info("High-value invoice — triggering reflection loop")
            reflection = REFLECTION_PROMPT.format(recommendation=recommendation)
            reflection_result = _call_llm(reflection)
            if reflection_result is not None:
                recommendation = reflection_result.get("status", recommendation).lower()
                if recommendation not in ("approved", "rejected"):
                    recommendation = (
                        "approved" if recommendation in ("approve", "pass") else "rejected"
                    )
                llm_reasoning = reflection_result.get("reasoning", llm_reasoning)
                top_risks = reflection_result.get("top_risks", [])
                if top_risks:
                    llm_reasoning += f" | Top risks: {'; '.join(top_risks)}"

        decision = ApprovalDecision(
            invoice_id=invoice.invoice_id,
            status=recommendation,
            reasoning=llm_reasoning or f"LLM-reviewed ({rule_label})",
            rule_applied=rule_label,
            approved_at=datetime.now(timezone.utc),
        )
    else:
        # LLM unavailable fallback
        logger.warning(
            "LLM unavailable for approval — using conservative rules"
        )
        # Conservative: reject high-value, approve flagged mid-range cautiously
        if amount > 10000:
            fallback_status = "rejected"
            fallback_reasoning = (
                f"LLM unavailable for high-value review (${amount:,.2f}). "
                "Conservative auto-reject applied."
            )
        elif validation.status == "flag":
            fallback_status = "rejected"
            fallback_reasoning = (
                f"LLM unavailable; validation flagged issues. "
                f"Conservative auto-reject applied."
            )
        else:
            fallback_status = "approved"
            fallback_reasoning = (
                f"LLM unavailable; validation passed with "
                f"vendor risk {vendor_risk:.2f}. Auto-approved by rules."
            )

        decision = ApprovalDecision(
            invoice_id=invoice.invoice_id,
            status=fallback_status,
            reasoning=fallback_reasoning,
            rule_applied=f"{rule_label}_llm_unavailable",
            approved_at=datetime.now(timezone.utc),
        )

    duration_ms = int((time.time() - start_time) * 1000)

    log_action(
        invoice_id=invoice.invoice_id,
        agent_name="approval",
        action="approve",
        result=decision.status,
        reasoning=json.dumps({
            "status": decision.status,
            "rule": decision.rule_applied,
            "amount": amount,
            "vendor_risk": vendor_risk,
            "duplicate_info": dup_info,
            "reasoning": decision.reasoning,
        }, default=str),
        duration_ms=duration_ms,
    )

    logger.info(
        "Approval complete: %s → %s (rule=%s, duration=%dms)",
        invoice.invoice_id,
        decision.status,
        decision.rule_applied,
        duration_ms,
    )
    return decision
