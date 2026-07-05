"""
FastAPI application for the Galatiq Invoice Processing System.

Provides REST endpoints for uploading invoices, querying processing state,
viewing audit trails, listing invoices, dashboard stats, and retrying failures.
Swagger UI auto-generated at ``/docs``.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import shutil
import string
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.models import (
    AuditEntryOut,
    AuditTrailResponse,
    ErrorResponse,
    InvoiceListResponse,
    InvoiceStatusResponse,
    InvoiceSummary,
    InvoiceUploadResponse,
    RetryResponse,
    StatsResponse,
    VendorStats,
    OverrideRequest,
    OverrideResponse,
)
from src.audit import AuditLogger
from src.config import get_logger, get_settings
from src.database import get_connection, init_database

logger = get_logger("api")

# ---------------------------------------------------------------------------
# Lazy import for the orchestrator (may not exist yet during development)
# ---------------------------------------------------------------------------

_orchestrator_available = False

try:
    from src.orchestrator import process_invoice, resume_processing, manual_override_invoice  # type: ignore[import-untyped]

    _orchestrator_available = True
except ImportError:
    logger.warning(
        "src.orchestrator not available — upload and retry endpoints will "
        "return 503 until the module is created."
    )

    async def process_invoice(file_path: str) -> dict:  # type: ignore[misc]
        """Placeholder until the real orchestrator is built."""
        raise RuntimeError("Orchestrator module not available")

    async def resume_processing(invoice_id: str) -> dict:  # type: ignore[misc]
        """Placeholder until the real orchestrator is built."""
        raise RuntimeError("Orchestrator module not available")

    async def manual_override_invoice(
        invoice_id: str,
        action: str,
        reason: str,
        corrected_extracted_invoice: Optional[dict] = None,
    ) -> dict:
        """Placeholder until the real orchestrator is built."""
        raise RuntimeError("Orchestrator module not available")


# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_UPLOAD_DIR = _PROJECT_ROOT / "data" / "uploads"
_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {".pdf", ".txt", ".json", ".csv", ".xml"}

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Galatiq Invoice Processing API",
    description=(
        "Upload invoices, track processing state, view audit trails, "
        "and monitor dashboard metrics."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS (allow local React dev server) ───────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Startup ───────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event() -> None:
    """Ensure the database schema exists on first launch."""
    init_database()
    logger.info("API server started — database initialised")


# ── Exception handlers ───────────────────────────────────────────────────

@app.exception_handler(400)
async def bad_request_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=ErrorResponse(error="Bad Request", detail=str(exc.detail)).model_dump(),
    )


@app.exception_handler(404)
async def not_found_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content=ErrorResponse(error="Not Found", detail=str(exc.detail)).model_dump(),
    )


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Unhandled exception: %s", traceback.format_exc())
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error="Internal Server Error",
            detail="An unexpected error occurred. Check server logs for details.",
        ).model_dump(),
    )


# ── Helpers ───────────────────────────────────────────────────────────────

def _generate_invoice_id() -> str:
    """Generate a unique invoice ID: INV-{timestamp}-{random}."""
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S")
    rand = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"INV-{ts}-{rand}"


async def _run_processing(invoice_id: str, file_path: str) -> None:
    """Run the orchestrator in the background; log errors."""
    try:
        if asyncio.iscoroutinefunction(process_invoice):
            await process_invoice(file_path, invoice_id)
        else:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, process_invoice, file_path, invoice_id)
    except Exception:
        logger.error(
            "Background processing failed for %s: %s",
            invoice_id,
            traceback.format_exc(),
        )


async def _run_retry(invoice_id: str) -> None:
    """Resume processing in the background; log errors."""
    try:
        if asyncio.iscoroutinefunction(resume_processing):
            await resume_processing(invoice_id)
        else:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, resume_processing, invoice_id)
    except Exception:
        logger.error(
            "Background retry failed for %s: %s",
            invoice_id,
            traceback.format_exc(),
        )


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    """Safely parse an ISO-format datetime string."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


# ── 1. POST /api/invoices/upload ──────────────────────────────────────────


@app.post(
    "/api/invoices/upload",
    response_model=InvoiceUploadResponse,
    status_code=202,
    summary="Upload an invoice file for processing",
    responses={400: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def upload_invoice(file: UploadFile = File(...)) -> InvoiceUploadResponse:
    """Accept a multipart file upload, save it, and kick off background processing."""

    # Validate file extension
    filename = file.filename or "unknown"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type '{ext}'. Expected: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    # Generate ID & save file
    invoice_id = _generate_invoice_id()
    dest = _UPLOAD_DIR / f"{invoice_id}{ext}"
    with open(dest, "wb") as buf:
        shutil.copyfileobj(file.file, buf)

    now = datetime.now(tz=timezone.utc)

    # Persist initial processing_state row so GET endpoints work immediately
    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO processing_state
                    (invoice_id, current_stage, full_state_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (invoice_id, "ingestion", json.dumps({"invoice_id": invoice_id, "file_path": str(dest)}), now.isoformat(), now.isoformat()),
            )
    except Exception:
        logger.warning("Could not persist initial state for %s", invoice_id)

    # Fire-and-forget background processing
    if not _orchestrator_available:
        logger.warning("Orchestrator unavailable — file saved but processing will not start")
    else:
        asyncio.create_task(_run_processing(invoice_id, str(dest)))

    return InvoiceUploadResponse(
        invoice_id=invoice_id,
        status="processing",
        created_at=now,
    )


# ── 2. GET /api/invoices/{invoice_id} ────────────────────────────────────


@app.get(
    "/api/invoices/{invoice_id}",
    response_model=InvoiceStatusResponse,
    summary="Fetch full processing state for an invoice",
    responses={404: {"model": ErrorResponse}},
)
async def get_invoice(invoice_id: str) -> InvoiceStatusResponse:
    """Query ``processing_state`` and deserialise ``full_state_json``."""

    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM processing_state WHERE invoice_id = ?",
            (invoice_id,),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Invoice '{invoice_id}' not found")

    # Deserialise the full state blob
    state: dict = {}
    if row["full_state_json"]:
        try:
            state = json.loads(row["full_state_json"])
        except (json.JSONDecodeError, TypeError):
            state = {}

    return InvoiceStatusResponse(
        invoice_id=row["invoice_id"],
        current_stage=row["current_stage"],
        extracted_invoice=state.get("extracted_invoice"),
        validation_result=state.get("validation_result"),
        approval_decision=state.get("approval_decision"),
        payment_receipt=state.get("payment_receipt"),
        error_log=state.get("error_log", []),
        created_at=_parse_datetime(row["created_at"]),
        updated_at=_parse_datetime(row["updated_at"]),
    )


# ── 3. GET /api/invoices/{invoice_id}/audit ──────────────────────────────


@app.get(
    "/api/invoices/{invoice_id}/audit",
    response_model=AuditTrailResponse,
    summary="Fetch the full audit trail for an invoice",
)
async def get_audit_trail(invoice_id: str) -> AuditTrailResponse:
    """Query ``audit_log`` for the given invoice."""

    audit_logger = AuditLogger()
    entries = audit_logger.get_processing_history(invoice_id)

    return AuditTrailResponse(
        invoice_id=invoice_id,
        audit_entries=[
            AuditEntryOut(
                id=e.id,
                timestamp=e.timestamp,
                agent_name=e.agent_name,
                action=e.action,
                result=e.result,
                reasoning=e.reasoning,
                error_msg=e.error_msg,
                duration_ms=e.duration_ms,
            )
            for e in entries
        ],
    )


# ── 4. GET /api/invoices ─────────────────────────────────────────────────


@app.get(
    "/api/invoices",
    response_model=InvoiceListResponse,
    summary="List invoices with optional filters and pagination",
)
async def list_invoices(
    status: Optional[str] = Query(None, description='Filter by status: "processing", "approved", "rejected"'),
    vendor: Optional[str] = Query(None, description="Vendor name substring filter"),
    date_from: Optional[str] = Query(None, description="Start date (ISO 8601)"),
    date_to: Optional[str] = Query(None, description="End date (ISO 8601)"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(10, ge=1, le=100, description="Items per page"),
) -> InvoiceListResponse:
    """List invoices from ``processing_state`` with optional filters."""

    conditions: list[str] = []
    params: list[str | int] = []

    if date_from:
        conditions.append("ps.created_at >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("ps.created_at <= ?")
        params.append(date_to)

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    with get_connection() as conn:
        # Fetch all matching rows (we filter status/vendor in Python because
        # they live inside the JSON blob, not as top-level columns).
        rows = conn.execute(
            f"""
            SELECT invoice_id, current_stage, full_state_json,
                   created_at, updated_at
            FROM processing_state ps
            {where_clause}
            ORDER BY created_at DESC
            """,
            tuple(params),
        ).fetchall()

    # Post-filter and build summaries
    summaries: list[InvoiceSummary] = []
    for row in rows:
        state: dict = {}
        if row["full_state_json"]:
            try:
                state = json.loads(row["full_state_json"])
            except (json.JSONDecodeError, TypeError):
                state = {}

        vendor_name = None
        total_amount = None
        inv_status = row["current_stage"]

        extracted = state.get("extracted_invoice")
        if isinstance(extracted, dict):
            vendor_name = extracted.get("vendor_name")
            total_amount = extracted.get("total_amount")

        # Derive higher-level status from approval / stage
        approval = state.get("approval_decision")
        if isinstance(approval, dict):
            status_val = approval.get("status", inv_status)
            rule = approval.get("rule_applied", "")
            if rule and rule.startswith("manual_override"):
                inv_status = f"{status_val}_manual"
            else:
                inv_status = status_val

        # Apply filters
        match_filter = True
        if status:
            if status == "approved":
                match_filter = inv_status in ("approved", "approved_manual")
            elif status == "rejected":
                match_filter = inv_status in ("rejected", "rejected_manual")
            else:
                match_filter = inv_status == status
        
        if not match_filter:
            continue
        if vendor and (not vendor_name or vendor.lower() not in vendor_name.lower()):
            continue

        summaries.append(
            InvoiceSummary(
                invoice_id=row["invoice_id"],
                vendor_name=vendor_name,
                total_amount=float(total_amount) if total_amount is not None else None,
                status=inv_status,
                current_stage=row["current_stage"],
                created_at=_parse_datetime(row["created_at"]),
            )
        )

    total = len(summaries)
    offset = (page - 1) * limit
    page_items = summaries[offset : offset + limit]

    return InvoiceListResponse(
        total=total,
        page=page,
        limit=limit,
        invoices=page_items,
    )


# ── 5. GET /api/stats ────────────────────────────────────────────────────


@app.get(
    "/api/stats",
    response_model=StatsResponse,
    summary="Dashboard metrics aggregated from processing state",
)
async def get_stats(
    date_from: Optional[str] = Query(None, description="Start date (ISO 8601)"),
    date_to: Optional[str] = Query(None, description="End date (ISO 8601)"),
) -> StatsResponse:
    """Aggregate stats from ``processing_state`` and ``audit_log``."""

    conditions: list[str] = []
    params: list[str] = []

    if date_from:
        conditions.append("ps.created_at >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("ps.created_at <= ?")
        params.append(date_to)

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT invoice_id, current_stage, full_state_json
            FROM processing_state ps
            {where_clause}
            """,
            tuple(params),
        ).fetchall()

        # Average processing time from audit_log
        avg_row = conn.execute(
            "SELECT AVG(duration_ms) AS avg_ms FROM audit_log"
        ).fetchone()

    avg_time = round(avg_row["avg_ms"] or 0, 2) if avg_row else 0.0

    # Tally metrics
    total_invoices = len(rows)
    approved = 0
    rejected = 0
    processing = 0
    total_amount_approved = 0.0
    by_vendor: dict[str, dict] = {}
    by_rule: dict[str, int] = {}

    for row in rows:
        state: dict = {}
        if row["full_state_json"]:
            try:
                state = json.loads(row["full_state_json"])
            except (json.JSONDecodeError, TypeError):
                state = {}

        approval = state.get("approval_decision")
        extracted = state.get("extracted_invoice")
        vendor_name = extracted.get("vendor_name", "Unknown") if isinstance(extracted, dict) else "Unknown"
        amount = 0.0
        if isinstance(extracted, dict):
            try:
                amount = float(extracted.get("total_amount", 0))
            except (ValueError, TypeError):
                amount = 0.0

        if isinstance(approval, dict):
            dec = approval.get("status", "").lower()
            rule = approval.get("rule_applied", "unknown")
            if dec == "approved":
                approved += 1
                total_amount_approved += amount
            elif dec == "rejected":
                rejected += 1
            else:
                processing += 1

            by_rule[rule] = by_rule.get(rule, 0) + 1
        else:
            processing += 1

        # Vendor breakdown
        if vendor_name not in by_vendor:
            by_vendor[vendor_name] = {"count": 0, "approved": 0, "total": 0.0}
        by_vendor[vendor_name]["count"] += 1
        if isinstance(approval, dict) and approval.get("status", "").lower() == "approved":
            by_vendor[vendor_name]["approved"] += 1
            by_vendor[vendor_name]["total"] += amount

    approved_pct = round((approved / total_invoices) * 100, 1) if total_invoices else 0.0
    rejected_pct = round((rejected / total_invoices) * 100, 1) if total_invoices else 0.0

    return StatsResponse(
        total_invoices=total_invoices,
        approved=approved,
        rejected=rejected,
        processing=processing,
        approved_percent=approved_pct,
        rejected_percent=rejected_pct,
        total_amount_approved=round(total_amount_approved, 2),
        avg_processing_time_ms=avg_time,
        by_vendor={k: VendorStats(**v) for k, v in by_vendor.items()},
        by_approval_rule=by_rule,
    )


# ── 6. POST /api/invoices/{invoice_id}/retry ─────────────────────────────


@app.post(
    "/api/invoices/{invoice_id}/retry",
    response_model=RetryResponse,
    status_code=202,
    summary="Retry processing a failed invoice",
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def retry_invoice(invoice_id: str) -> RetryResponse:
    """Resume processing from the last successful stage."""

    # Verify the invoice exists
    with get_connection() as conn:
        row = conn.execute(
            "SELECT current_stage FROM processing_state WHERE invoice_id = ?",
            (invoice_id,),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Invoice '{invoice_id}' not found")

    current_stage = row["current_stage"]

    # Count previous retries from audit log
    with get_connection() as conn:
        retry_row = conn.execute(
            """
            SELECT COUNT(*) AS cnt FROM audit_log
            WHERE invoice_id = ? AND action = 'retry'
            """,
            (invoice_id,),
        ).fetchone()
    retry_count = (retry_row["cnt"] if retry_row else 0) + 1

    # Log the retry action
    try:
        audit = AuditLogger()
        audit.log_invoice_processing(
            invoice_id=invoice_id,
            agent_name="api",
            action="retry",
            result="initiated",
            reasoning={"resumed_from_stage": current_stage, "retry_count": retry_count},
        )
    except Exception:
        logger.warning("Could not log retry audit entry for %s", invoice_id)

    if not _orchestrator_available:
        raise HTTPException(
            status_code=503,
            detail="Orchestrator module not available — retry cannot proceed",
        )

    asyncio.create_task(_run_retry(invoice_id))

    return RetryResponse(
        invoice_id=invoice_id,
        status="processing",
        resumed_from_stage=current_stage,
        retry_count=retry_count,
    )


# ── 7. POST /api/invoices/{invoice_id}/override ──────────────────────────


@app.post(
    "/api/invoices/{invoice_id}/override",
    response_model=OverrideResponse,
    status_code=200,
    summary="Manually override or edit and re-process an invoice",
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def override_invoice(
    invoice_id: str,
    payload: OverrideRequest,
) -> OverrideResponse:
    """Manually override approval/rejection or re-validate corrected data."""
    if not _orchestrator_available:
        raise HTTPException(
            status_code=503,
            detail="Orchestrator module not available — override cannot proceed",
        )

    # Verify the invoice exists
    with get_connection() as conn:
        row = conn.execute(
            "SELECT current_stage FROM processing_state WHERE invoice_id = ?",
            (invoice_id,),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail=f"Invoice '{invoice_id}' not found")

    try:
        if asyncio.iscoroutinefunction(manual_override_invoice):
            result_state = await manual_override_invoice(
                invoice_id,
                payload.action,
                payload.reason,
                payload.corrected_extracted_invoice,
            )
        else:
            loop = asyncio.get_running_loop()
            result_state = await loop.run_in_executor(
                None,
                manual_override_invoice,
                invoice_id,
                payload.action,
                payload.reason,
                payload.corrected_extracted_invoice,
            )

        # Determine new status
        final_status = "processing"
        approval = result_state.get("approval_decision") or {}
        payment = result_state.get("payment_receipt") or {}
        if result_state.get("error_log"):
            final_status = "error"
        elif approval.get("status") == "rejected":
            final_status = "rejected"
        elif payment.get("status") == "success":
            final_status = "approved"
        elif payment.get("status") == "skipped":
            final_status = "rejected"

        return OverrideResponse(
            invoice_id=invoice_id,
            status=final_status,
            action=payload.action,
        )
    except Exception as exc:
        logger.error(
            "Manual override failed for %s: %s",
            invoice_id,
            traceback.format_exc(),
        )
        raise HTTPException(
            status_code=500,
            detail=f"Override operation failed: {str(exc)}",
        )


# ── Health Check ──────────────────────────────────────────────────────────


@app.get("/health", summary="Health check", include_in_schema=False)
async def health_check() -> dict:
    """Simple liveness probe."""
    return {"status": "ok", "timestamp": datetime.now(tz=timezone.utc).isoformat()}
