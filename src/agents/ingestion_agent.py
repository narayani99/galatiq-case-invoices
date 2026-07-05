"""Ingestion Agent — extracts structured invoice data from raw files.

Pipeline:
1. Parse the invoice file via the parser factory (multi-format).
2. Send raw text to the LLM (Grok) for structured extraction.
3. Self-correction loop: re-prompt if any confidence score < 0.7 (max 2 retries).
4. Build and return an ExtractedInvoice Pydantic model.

Graceful degradation: if the LLM is unavailable, falls back to parser
output with all confidence scores set to 0.5.
"""

from __future__ import annotations

import json
import time
import traceback
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from src.config import Settings, get_llm_client, get_logger
from src.models.invoice import ExtractedInvoice, LineItem
from src.parsers import parse_invoice_file
from src.tools.audit_tools import log_action

logger = get_logger("agents.ingestion")

# ---------------------------------------------------------------------------
# Prompts (from 03_SPECIFICATIONS.md)
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """\
Extract invoice information from the following text.
Handle typos, abbreviations, and missing fields.
Return the data as JSON.

Fields to extract:
- vendor_name: Name of the vendor/company sending invoice
- invoice_number: Invoice or PO number
- invoice_date: Date invoice was issued
- due_date: Payment due date
- total_amount: Total invoice amount
- line_items: List of items ordered with quantity and unit price

Return JSON with structure:
{
  "vendor_name": "...",
  "invoice_number": "...",
  "invoice_date": "...",
  "due_date": "...",
  "total_amount": ...,
  "line_items": [
    {"item_name": "...", "quantity": ..., "unit_price": ...},
    ...
  ],
  "confidence_scores": {
    "vendor_name": 0.95,
    "total_amount": 0.99,
    ...
  }
}

If uncertain about a field, provide your best guess with confidence < 1.0.

Text:
"""

RETRY_PROMPT = """\
I'm unsure about the following fields: {low_confidence_fields}.
Please re-extract them with higher confidence from the same text.
Return the FULL JSON again (all fields), not just the uncertain ones.

Text:
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    """Convert a value to Decimal, returning *default* on failure."""
    if value is None:
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return default


def _parse_datetime(value: Any) -> datetime:
    """Best-effort datetime parsing; returns UTC now on failure."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        for fmt in (
            "%Y-%m-%d",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S%z",
            "%m/%d/%Y",
            "%d/%m/%Y",
            "%B %d, %Y",
            "%b %d, %Y",
            "%d %B %Y",
            "%d %b %Y",
        ):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return datetime.now(timezone.utc)


def _extract_json_from_text(text: str) -> dict | None:
    """Extract the first JSON object from LLM response text."""
    # Try direct parse first
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    # Try to find JSON block in markdown fences
    import re
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, TypeError):
            pass
    # Try to find first { ... } block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def _build_invoice_from_llm(
    llm_data: dict,
    raw_text: str,
    invoice_id: str,
) -> ExtractedInvoice:
    """Construct an ExtractedInvoice from LLM-extracted JSON."""
    line_items = []
    for item in llm_data.get("line_items", []):
        line_items.append(
            LineItem(
                item_name=item.get("item_name", "Unknown"),
                quantity=int(item.get("quantity", 0)),
                unit_price=_safe_decimal(item.get("unit_price")),
                total=_safe_decimal(item.get("total")),
            )
        )

    return ExtractedInvoice(
        invoice_id=invoice_id or llm_data.get("invoice_number") or "UNKNOWN",
        vendor_name=llm_data.get("vendor_name") or "Unknown Vendor",
        invoice_date=_parse_datetime(llm_data.get("invoice_date")),
        due_date=_parse_datetime(llm_data.get("due_date")),
        total_amount=_safe_decimal(llm_data.get("total_amount")),
        line_items=line_items,
        extracted_at=datetime.now(timezone.utc),
        raw_text=raw_text,
        extraction_method="grok",
        confidence_scores=llm_data.get("confidence_scores", {}),
        extraction_errors=[],
    )


def _build_invoice_from_parser(
    parsed_data: dict,
    raw_text: str,
) -> ExtractedInvoice:
    """Construct an ExtractedInvoice from parser output (fallback)."""
    line_items = []
    for item in parsed_data.get("line_items", []):
        line_items.append(
            LineItem(
                item_name=item.get("item_name", "Unknown"),
                quantity=int(item.get("quantity", 0)),
                unit_price=_safe_decimal(item.get("unit_price")),
                total=_safe_decimal(item.get("total")),
            )
        )

    invoice_id = parsed_data.get("invoice_number") or parsed_data.get("invoice_id", "UNKNOWN")

    # Set all confidence scores to 0.5 for parser-only extraction
    confidence_scores = {
        "vendor_name": 0.5,
        "invoice_number": 0.5,
        "invoice_date": 0.5,
        "due_date": 0.5,
        "total_amount": 0.5,
        "line_items": 0.5,
    }

    return ExtractedInvoice(
        invoice_id=invoice_id,
        vendor_name=parsed_data.get("vendor_name") or "Unknown Vendor",
        invoice_date=_parse_datetime(parsed_data.get("invoice_date")),
        due_date=_parse_datetime(parsed_data.get("due_date")),
        total_amount=_safe_decimal(parsed_data.get("total_amount")),
        line_items=line_items,
        extracted_at=datetime.now(timezone.utc),
        raw_text=raw_text,
        extraction_method="parser",
        confidence_scores=confidence_scores,
        extraction_errors=["LLM unavailable — used parser fallback"],
    )


def _low_confidence_fields(confidence_scores: dict, threshold: float = 0.7) -> list[str]:
    """Return field names with confidence below *threshold*."""
    return [
        field
        for field, score in confidence_scores.items()
        if isinstance(score, (int, float)) and score < threshold
    ]


# ---------------------------------------------------------------------------
# LLM interaction
# ---------------------------------------------------------------------------

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
                    "content": "You are an expert invoice data extraction assistant. Always respond with valid JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )

        content = response.choices[0].message.content
        return _extract_json_from_text(content)

    except Exception as exc:
        logger.warning("LLM call failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def ingest_invoice(file_path: str) -> ExtractedInvoice:
    """Ingest an invoice file and return structured data.

    Steps:
        1. Parse the file to get (parsed_data, raw_text).
        2. Call LLM for structured extraction.
        3. Self-correction loop if any confidence < 0.7 (max 2 retries).
        4. Build and return ExtractedInvoice.

    If the LLM is unavailable, uses parser output directly with
    confidence scores of 0.5.
    """
    start_time = time.time()
    errors: list[str] = []

    # ------------------------------------------------------------------
    # Step 1: Parse the file
    # ------------------------------------------------------------------
    try:
        parsed_data, raw_text = parse_invoice_file(file_path)
        logger.info("Parsed invoice file: %s", file_path)
    except Exception as exc:
        logger.error("Parser failed for %s: %s", file_path, exc)
        errors.append(f"Parser error: {exc}")
        parsed_data = {}
        raw_text = ""
        # Try reading raw text as fallback
        try:
            with open(file_path, "r", errors="replace") as f:
                raw_text = f.read()
        except Exception:
            pass

    invoice_id = (
        parsed_data.get("invoice_number")
        or parsed_data.get("invoice_id")
        or "UNKNOWN"
    )

    # ------------------------------------------------------------------
    # Step 2: LLM extraction
    # ------------------------------------------------------------------
    prompt = EXTRACTION_PROMPT + raw_text
    llm_data = _call_llm(prompt)

    if llm_data is None:
        # Fallback: use parser output directly
        logger.warning("LLM unavailable — falling back to parser output")
        duration_ms = int((time.time() - start_time) * 1000)
        invoice = _build_invoice_from_parser(parsed_data, raw_text)
        log_action(
            invoice_id=invoice.invoice_id,
            agent_name="ingestion",
            action="extract",
            result="success",
            reasoning=json.dumps({
                "method": "parser_fallback",
                "vendor_name": invoice.vendor_name,
                "amount": float(invoice.total_amount),
                "confidence_scores": invoice.confidence_scores,
                "errors": errors,
            }),
            duration_ms=duration_ms,
        )
        return invoice

    # ------------------------------------------------------------------
    # Step 3: Self-correction loop
    # ------------------------------------------------------------------
    confidence = llm_data.get("confidence_scores", {})
    max_retries = 2
    retry_count = 0

    while retry_count < max_retries:
        low_fields = _low_confidence_fields(confidence)
        if not low_fields:
            break

        logger.info(
            "Low confidence on fields %s (attempt %d/%d) — retrying",
            low_fields,
            retry_count + 1,
            max_retries,
        )

        retry_prompt = RETRY_PROMPT.format(
            low_confidence_fields=", ".join(low_fields)
        ) + raw_text

        retry_data = _call_llm(retry_prompt)
        if retry_data is not None:
            # Merge improved fields into llm_data
            new_confidence = retry_data.get("confidence_scores", {})
            for field in low_fields:
                if field in retry_data:
                    llm_data[field] = retry_data[field]
                if field in new_confidence:
                    confidence[field] = new_confidence[field]
            llm_data["confidence_scores"] = confidence
        else:
            logger.warning("Retry LLM call failed — keeping previous extraction")
            break

        retry_count += 1

    # Use invoice_id from LLM if parser didn't find one
    if invoice_id == "UNKNOWN" and llm_data.get("invoice_number"):
        invoice_id = llm_data["invoice_number"]

    # ------------------------------------------------------------------
    # Step 4: Build ExtractedInvoice
    # ------------------------------------------------------------------
    try:
        invoice = _build_invoice_from_llm(llm_data, raw_text, invoice_id)
    except Exception as exc:
        logger.error("Failed to build invoice from LLM data: %s", exc)
        errors.append(f"Model build error: {exc}")
        invoice = _build_invoice_from_parser(parsed_data, raw_text)

    duration_ms = int((time.time() - start_time) * 1000)

    log_action(
        invoice_id=invoice.invoice_id,
        agent_name="ingestion",
        action="extract",
        result="success",
        reasoning=json.dumps({
            "method": invoice.extraction_method,
            "vendor_name": invoice.vendor_name,
            "amount": float(invoice.total_amount),
            "confidence_scores": invoice.confidence_scores,
            "retries": retry_count,
            "errors": errors,
        }),
        duration_ms=duration_ms,
    )

    logger.info(
        "Ingestion complete: %s (method=%s, duration=%dms)",
        invoice.invoice_id,
        invoice.extraction_method,
        duration_ms,
    )
    return invoice
