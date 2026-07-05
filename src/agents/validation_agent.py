"""Validation Agent — validates extracted invoice data against inventory & vendors.

Pipeline:
1. For each line item, call check_inventory(item_name, quantity).
2. Call query_vendor(vendor_name) for risk/blacklist info.
3. Apply deterministic business rules (PASS / FLAG / REJECT).
4. Call LLM for reasoning narrative.
5. Self-correction: if LLM says PASS but high-risk signals exist, retry with
   reflection prompt.

Graceful degradation: if the LLM is unavailable, returns rule-based result
without LLM reasoning.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from decimal import Decimal

from src.config import Settings, get_llm_client, get_logger
from src.models.invoice import ExtractedInvoice, InventoryMismatch, ValidationResult
from src.tools.inventory_tools import check_inventory, query_vendor
from src.tools.audit_tools import log_action

logger = get_logger("agents.validation")

# ---------------------------------------------------------------------------
# Prompts (from 03_SPECIFICATIONS.md)
# ---------------------------------------------------------------------------

VALIDATION_PROMPT = """\
Analyze this invoice against our inventory database.

Invoice Items:
{invoice_items}

Inventory Status:
{inventory_status}

Vendor Information:
{vendor_info}

Apply these validation rules:
1. If item not found in inventory → REJECT with reason "Unknown item"
2. If requested quantity > available stock → FLAG as "Stock mismatch"
3. If vendor blacklisted → REJECT with reason "Blacklisted vendor"
4. If vendor risk score > 0.7 → FLAG as "High vendor risk"
5. If all items available and vendor OK → PASS

Return JSON:
{{
  "status": "pass|flag|reject",
  "mismatches": [...],
  "risk_score": 0.0-1.0,
  "reasoning": "..."
}}
"""

REFLECTION_PROMPT = """\
You suggested PASS, but I see the following high-risk signals:
{risk_signals}

Please reconsider your assessment. Are you sure PASS is correct?
Return JSON:
{{
  "status": "pass|flag|reject",
  "risk_score": 0.0-1.0,
  "reasoning": "..."
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
                        "You are an expert invoice validation assistant. "
                        "Always respond with valid JSON."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )

        content = response.choices[0].message.content
        return _extract_json_from_text(content)

    except Exception as exc:
        logger.warning("LLM call failed during validation: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def validate_invoice(invoice: ExtractedInvoice) -> ValidationResult:
    """Validate an extracted invoice against inventory and vendor databases.

    Steps:
        1. Check each line item against inventory.
        2. Query vendor risk/blacklist status.
        3. Apply deterministic business rules.
        4. Call LLM for reasoning (optional).
        5. Self-correction: if LLM says PASS but risks exist, retry.

    Returns:
        ValidationResult with status (pass/flag/reject), mismatches,
        risk_score, and reasoning.
    """
    start_time = time.time()

    mismatches: list[InventoryMismatch] = []
    inventory_checks: list[dict] = []
    rule_status = "pass"  # start optimistic
    risk_signals: list[str] = []

    # ------------------------------------------------------------------
    # Step 1: Check inventory for each line item
    # ------------------------------------------------------------------
    for item in invoice.line_items:
        result = check_inventory(item.item_name, item.quantity)
        inventory_checks.append(result)

        if result["status"] == "unknown":
            mismatches.append(
                InventoryMismatch(
                    item_name=item.item_name,
                    requested_qty=item.quantity,
                    available_stock=0,
                    status="unknown",
                )
            )
            rule_status = "reject"
            risk_signals.append(f"Unknown item: {item.item_name}")

        elif result["status"] == "out_of_stock":
            mismatches.append(
                InventoryMismatch(
                    item_name=item.item_name,
                    requested_qty=item.quantity,
                    available_stock=0,
                    status="out_of_stock",
                )
            )
            rule_status = "reject"
            risk_signals.append(f"Out of stock: {item.item_name}")

        elif result["status"] == "mismatch":
            mismatches.append(
                InventoryMismatch(
                    item_name=item.item_name,
                    requested_qty=item.quantity,
                    available_stock=result["available_stock"],
                    status="mismatch",
                )
            )
            if rule_status != "reject":
                rule_status = "flag"
            risk_signals.append(
                f"Stock mismatch: {item.item_name} "
                f"(requested {item.quantity}, available {result['available_stock']})"
            )

        # Negative quantity check
        if item.quantity < 0:
            if rule_status != "reject":
                rule_status = "reject"
            risk_signals.append(f"Negative quantity: {item.item_name} ({item.quantity})")

    # ------------------------------------------------------------------
    # Step 2: Check vendor
    # ------------------------------------------------------------------
    vendor_info = query_vendor(invoice.vendor_name)

    if vendor_info["is_blacklisted"]:
        rule_status = "reject"
        risk_signals.append(f"Blacklisted vendor: {invoice.vendor_name}")

    if vendor_info["risk_score"] > 0.7:
        if rule_status != "reject":
            rule_status = "flag"
        risk_signals.append(
            f"High vendor risk: {invoice.vendor_name} "
            f"(score={vendor_info['risk_score']:.2f})"
        )

    vendor_risk = Decimal(str(vendor_info["risk_score"]))

    # ------------------------------------------------------------------
    # Step 3: Compute aggregate risk score
    # ------------------------------------------------------------------
    # Simple heuristic: vendor risk + penalty per mismatch
    risk_score = float(vendor_risk)
    if mismatches:
        risk_score = min(1.0, risk_score + 0.15 * len(mismatches))
    risk_score = round(risk_score, 2)

    # ------------------------------------------------------------------
    # Step 4: LLM reasoning (optional)
    # ------------------------------------------------------------------
    reasoning = f"Rule-based: status={rule_status}"
    if risk_signals:
        reasoning += f"; signals: {'; '.join(risk_signals)}"

    prompt = VALIDATION_PROMPT.format(
        invoice_items=json.dumps(
            [
                {
                    "item": item.item_name,
                    "qty": item.quantity,
                    "unit_price": str(item.unit_price) if item.unit_price else "N/A",
                }
                for item in invoice.line_items
            ],
            indent=2,
        ),
        inventory_status=json.dumps(inventory_checks, indent=2),
        vendor_info=json.dumps(vendor_info, indent=2),
    )

    llm_result = _call_llm(prompt)

    if llm_result is not None:
        llm_status = llm_result.get("status", rule_status)
        llm_reasoning = llm_result.get("reasoning", "")

        # ------------------------------------------------------------------
        # Step 5: Self-correction — LLM says PASS but risks exist
        # ------------------------------------------------------------------
        if llm_status == "pass" and risk_signals:
            logger.info(
                "LLM suggested PASS but risks exist — triggering reflection"
            )
            reflection = REFLECTION_PROMPT.format(
                risk_signals="\n".join(f"- {s}" for s in risk_signals)
            )
            retry_result = _call_llm(reflection)
            if retry_result is not None:
                llm_status = retry_result.get("status", rule_status)
                llm_reasoning = retry_result.get("reasoning", llm_reasoning)
                logger.info("Reflection result: status=%s", llm_status)
            else:
                # LLM failed on reflection — fall back to rule-based status
                llm_status = rule_status

        # Precedence order of severity: reject > flag > pass
        severity = {"reject": 2, "flag": 1, "pass": 0}
        r_sev = severity.get(rule_status, 0)
        l_sev = severity.get(llm_status, 0)
        
        if r_sev > l_sev:
            final_status = rule_status
        else:
            final_status = llm_status

        reasoning = llm_reasoning or reasoning
    else:
        logger.warning("LLM unavailable — using rule-based validation only")
        final_status = rule_status

    # ------------------------------------------------------------------
    # Build result
    # ------------------------------------------------------------------
    duration_ms = int((time.time() - start_time) * 1000)

    validation = ValidationResult(
        invoice_id=invoice.invoice_id,
        status=final_status,
        mismatches=mismatches,
        risk_score=Decimal(str(risk_score)),
        vendor_risk=vendor_risk,
        reasoning=reasoning,
        validated_at=datetime.now(timezone.utc),
    )

    log_action(
        invoice_id=invoice.invoice_id,
        agent_name="validation",
        action="validate",
        result=final_status,
        reasoning=json.dumps({
            "status": final_status,
            "mismatches": [m.model_dump() for m in mismatches],
            "risk_score": risk_score,
            "vendor_risk": float(vendor_risk),
            "risk_signals": risk_signals,
            "reasoning": reasoning,
        }, default=str),
        duration_ms=duration_ms,
    )

    logger.info(
        "Validation complete: %s → %s (risk=%.2f, mismatches=%d, duration=%dms)",
        invoice.invoice_id,
        final_status,
        risk_score,
        len(mismatches),
        duration_ms,
    )
    return validation
