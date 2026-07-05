"""LangGraph orchestrator for the Galatiq Invoice Processing System.

Defines a ``StateGraph`` that wires four agents (ingestion, validation,
approval, payment) plus a final audit node into a linear pipeline with
conditional edges:

    ingestion ──► validation ──► approval ──► payment ──► audit ──► END
         │              │              │
         └──► audit     └──► audit     └──► audit

State is persisted to the ``processing_state`` SQLite table after every
node so that processing can be resumed from the last checkpoint.

Key public functions:
    - ``process_invoice(file_path)``  — run the full pipeline
    - ``resume_processing(invoice_id)`` — continue from last checkpoint
    - ``build_graph()``               — return the compiled graph
"""

from __future__ import annotations

import asyncio
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from langgraph.graph import END, StateGraph

from src.config import get_logger
from src.database import get_connection

try:
    from typing import TypedDict
except ImportError:  # Python < 3.8 fallback
    from typing_extensions import TypedDict

logger = get_logger("orchestrator")


# ---------------------------------------------------------------------------
# Processing State
# ---------------------------------------------------------------------------

class ProcessingState(TypedDict):
    """Full state that flows through the LangGraph pipeline."""

    invoice_id: str
    file_path: str
    raw_invoice: Optional[bytes]
    extracted_invoice: Optional[dict]      # serialised ExtractedInvoice
    validation_result: Optional[dict]      # serialised ValidationResult
    approval_decision: Optional[dict]      # serialised ApprovalDecision
    payment_receipt: Optional[dict]        # serialised PaymentReceipt
    error_log: List[str]
    current_stage: str
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# State Persistence Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def save_state(state: dict) -> None:
    """Persist the full processing state to the ``processing_state`` table.

    Uses INSERT-or-UPDATE (upsert) semantics keyed on ``invoice_id``.
    """
    invoice_id = state.get("invoice_id", "UNKNOWN")
    current_stage = state.get("current_stage", "unknown")
    has_errors = 1 if state.get("error_log") else 0

    # Serialise the whole state dict to JSON (bytes are dropped to keep it
    # JSON-friendly).
    serialisable = {k: v for k, v in state.items() if k != "raw_invoice"}
    full_json = json.dumps(serialisable, default=str)

    try:
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO processing_state
                    (invoice_id, current_stage, full_state_json, has_errors,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(invoice_id) DO UPDATE SET
                    current_stage   = excluded.current_stage,
                    full_state_json = excluded.full_state_json,
                    has_errors      = excluded.has_errors,
                    updated_at      = CURRENT_TIMESTAMP
                """,
                (invoice_id, current_stage, full_json, has_errors),
            )
        logger.debug("State saved: invoice=%s stage=%s", invoice_id, current_stage)
    except Exception as exc:
        logger.error("Failed to save state for %s: %s", invoice_id, exc)


def load_state(invoice_id: str) -> Optional[dict]:
    """Load the last saved processing state from the database.

    Returns:
        The deserialised state dict, or ``None`` if not found.
    """
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT full_state_json FROM processing_state WHERE invoice_id = ?",
                (invoice_id,),
            ).fetchone()

        if row and row["full_state_json"]:
            return json.loads(row["full_state_json"])
    except Exception as exc:
        logger.error("Failed to load state for %s: %s", invoice_id, exc)

    return None


# ---------------------------------------------------------------------------
# Helper — run an async function from a sync context
# ---------------------------------------------------------------------------

def _run_async(coro):
    """Run an async coroutine from a synchronous LangGraph node.

    Tries to use an existing event loop first (e.g. when running inside
    an already-async context), otherwise creates a new one.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # We are inside an existing event loop — create a new one in a thread
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    else:
        return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Pydantic ↔ dict helpers
# ---------------------------------------------------------------------------

def _model_to_dict(model) -> dict:
    """Serialise a Pydantic model to a plain dict (JSON-safe)."""
    if model is None:
        return None
    raw = model.model_dump() if hasattr(model, "model_dump") else model.dict()
    return json.loads(json.dumps(raw, default=str))


# ---------------------------------------------------------------------------
# Graph Nodes
# ---------------------------------------------------------------------------

def ingestion_node(state: dict) -> dict:
    """Parse and extract structured data from the invoice file."""
    from src.agents.ingestion_agent import ingest_invoice

    logger.info("[Ingestion] Start: %s", state.get("file_path"))
    state["current_stage"] = "ingestion"
    state["updated_at"] = _now_iso()

    try:
        invoice = _run_async(ingest_invoice(state["file_path"]))
        state["extracted_invoice"] = _model_to_dict(invoice)
        ext_id = invoice.invoice_id
        if not state.get("invoice_id") or state.get("invoice_id") == "UNKNOWN":
            state["invoice_id"] = ext_id or "UNKNOWN"
        logger.info("[Ingestion] Complete: %s", state["invoice_id"])
    except Exception as exc:
        err = f"Ingestion failed: {exc}\n{traceback.format_exc()}"
        state["error_log"] = state.get("error_log", []) + [err]
        state["extracted_invoice"] = None
        logger.error(err)

    save_state(state)
    return state


def validation_node(state: dict) -> dict:
    """Validate extracted invoice against inventory and vendor databases."""
    from src.agents.validation_agent import validate_invoice
    from src.models.invoice import ExtractedInvoice

    logger.info("[Validation] Start: %s", state.get("invoice_id"))
    state["current_stage"] = "validation"
    state["updated_at"] = _now_iso()

    try:
        extracted = dict(state["extracted_invoice"])
        extracted["invoice_id"] = state["invoice_id"]
        invoice = ExtractedInvoice(**extracted)
        result = _run_async(validate_invoice(invoice))
        state["validation_result"] = _model_to_dict(result)
        logger.info("[Validation] Complete: %s - status: %s", state["invoice_id"], result.status)
    except Exception as exc:
        err = f"Validation failed: {exc}\n{traceback.format_exc()}"
        state["error_log"] = state.get("error_log", []) + [err]
        state["validation_result"] = None
        logger.error(err)

    save_state(state)
    return state


def approval_node(state: dict) -> dict:
    """Make approval/rejection decision on the validated invoice."""
    from src.agents.approval_agent import approve_invoice
    from src.models.invoice import ExtractedInvoice, ValidationResult

    logger.info("[Approval] Start: %s", state.get("invoice_id"))
    state["current_stage"] = "approval"
    state["updated_at"] = _now_iso()

    try:
        extracted = dict(state["extracted_invoice"])
        extracted["invoice_id"] = state["invoice_id"]
        invoice = ExtractedInvoice(**extracted)

        val_res = dict(state["validation_result"])
        val_res["invoice_id"] = state["invoice_id"]
        validation = ValidationResult(**val_res)

        decision = _run_async(approve_invoice(invoice, validation))
        state["approval_decision"] = _model_to_dict(decision)
        logger.info("[Approval] Complete: %s - status: %s", state["invoice_id"], decision.status)
    except Exception as exc:
        err = f"Approval failed: {exc}\n{traceback.format_exc()}"
        state["error_log"] = state.get("error_log", []) + [err]
        state["approval_decision"] = None
        logger.error(err)

    save_state(state)
    return state


def payment_node(state: dict) -> dict:
    """Process or skip payment based on the approval decision."""
    from src.agents.payment_agent import process_payment
    from src.models.invoice import ApprovalDecision, ExtractedInvoice

    logger.info("[Payment] Start: %s", state.get("invoice_id"))
    state["current_stage"] = "payment"
    state["updated_at"] = _now_iso()

    try:
        extracted_data = state.get("extracted_invoice")
        if not extracted_data:
            extracted_data = {
                "invoice_id": state["invoice_id"],
                "vendor_name": "Unknown Vendor",
                "invoice_date": _now_iso(),
                "due_date": _now_iso(),
                "total_amount": "0",
                "line_items": [],
                "extracted_at": _now_iso(),
                "raw_text": "",
                "extraction_method": "override"
            }
        extracted = dict(extracted_data)
        extracted["invoice_id"] = state["invoice_id"]
        invoice = ExtractedInvoice(**extracted)

        app_dec = dict(state["approval_decision"])
        app_dec["invoice_id"] = state["invoice_id"]
        approval = ApprovalDecision(**app_dec)

        receipt = _run_async(process_payment(invoice, approval))
        state["payment_receipt"] = _model_to_dict(receipt)
        logger.info("[Payment] Complete: %s - status: %s", state["invoice_id"], receipt.status)
    except Exception as exc:
        err = f"Payment failed: {exc}\n{traceback.format_exc()}"
        state["error_log"] = state.get("error_log", []) + [err]
        state["payment_receipt"] = None
        logger.error(err)

    save_state(state)
    return state


def audit_node(state: dict) -> dict:
    """Final audit logging — mark the invoice as fully processed."""
    from src.tools.audit_tools import log_action

    logger.info("[Audit] Start: %s", state.get("invoice_id"))
    state["current_stage"] = "audit"
    state["updated_at"] = _now_iso()

    invoice_id = state.get("invoice_id", "UNKNOWN")
    errors = state.get("error_log", [])

    # Determine final result
    approval = state.get("approval_decision") or {}
    payment = state.get("payment_receipt") or {}
    if errors:
        final_result = "failure"
    elif approval.get("status") == "rejected":
        final_result = "rejected"
    elif payment.get("status") == "success":
        final_result = "approved"
    elif payment.get("status") == "skipped":
        final_result = "rejected"
    else:
        final_result = payment.get("status", "unknown")

    log_action(
        invoice_id=invoice_id,
        agent_name="orchestrator",
        action="complete",
        result=final_result,
        reasoning=json.dumps({
            "final_status": final_result,
            "stages_completed": state.get("current_stage"),
            "error_count": len(errors),
        }),
        error_msg="; ".join(errors) if errors else None,
    )

    save_state(state)
    logger.info("[Audit] Complete: %s - status: %s", invoice_id, final_result)
    return state


# ---------------------------------------------------------------------------
# Conditional Routing Functions
# ---------------------------------------------------------------------------

def route_after_ingestion(state: dict) -> str:
    """Route after ingestion: success → validation, failure → audit."""
    if state.get("extracted_invoice") is not None:
        return "validation"
    return "audit"


def route_after_validation(state: dict) -> str:
    """Route after validation: pass/flag → approval, reject → audit."""
    validation = state.get("validation_result")
    if validation is None:
        return "audit"
    if validation.get("status") == "reject":
        return "audit"
    return "approval"


def route_after_approval(state: dict) -> str:
    """Route after approval: approved → payment, rejected → audit."""
    approval = state.get("approval_decision")
    if approval is None:
        return "audit"
    if approval.get("status") == "approved":
        return "payment"
    return "audit"


# ---------------------------------------------------------------------------
# Graph Builder
# ---------------------------------------------------------------------------

def build_graph():
    """Build and compile the LangGraph state machine.

    Returns:
        A compiled LangGraph ``CompiledGraph`` ready to ``.invoke()``.
    """
    graph = StateGraph(ProcessingState)

    # Add nodes
    graph.add_node("ingestion", ingestion_node)
    graph.add_node("validation", validation_node)
    graph.add_node("approval", approval_node)
    graph.add_node("payment", payment_node)
    graph.add_node("audit", audit_node)

    # Set entry point
    graph.set_entry_point("ingestion")

    # Conditional edges
    graph.add_conditional_edges(
        "ingestion",
        route_after_ingestion,
        {"validation": "validation", "audit": "audit"},
    )
    graph.add_conditional_edges(
        "validation",
        route_after_validation,
        {"approval": "approval", "audit": "audit"},
    )
    graph.add_conditional_edges(
        "approval",
        route_after_approval,
        {"payment": "payment", "audit": "audit"},
    )

    # Payment always goes to audit
    graph.add_edge("payment", "audit")

    # Audit is the terminal node
    graph.add_edge("audit", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _make_initial_state(file_path: str, invoice_id: Optional[str] = None) -> dict:
    """Create a fresh ProcessingState for a new invoice."""
    now = _now_iso()
    return {
        "invoice_id": invoice_id or "UNKNOWN",
        "file_path": str(file_path),
        "raw_invoice": None,
        "extracted_invoice": None,
        "validation_result": None,
        "approval_decision": None,
        "payment_receipt": None,
        "error_log": [],
        "current_stage": "pending",
        "created_at": now,
        "updated_at": now,
    }


def process_invoice(file_path: str, invoice_id: Optional[str] = None) -> dict:
    """Run a single invoice through the full LangGraph pipeline.

    Args:
        file_path: Path to the invoice file.
        invoice_id: Optional unique identifier to preserve.

    Returns:
        The final ProcessingState dict.
    """
    file_path = str(Path(file_path).resolve())
    logger.info("Processing invoice: %s (id=%s)", file_path, invoice_id)

    initial_state = _make_initial_state(file_path, invoice_id)
    app = build_graph()
    result = app.invoke(initial_state)

    logger.info("Pipeline finished: %s", result.get("invoice_id"))
    return dict(result)


def resume_processing(invoice_id: str) -> dict:
    """Resume processing from the last saved checkpoint.

    Loads state from the database and re-enters the graph at the
    appropriate node.

    Args:
        invoice_id: The invoice ID to resume.

    Returns:
        The final ProcessingState dict.

    Raises:
        ValueError: If no saved state exists for the given invoice_id.
    """
    state = load_state(invoice_id)
    if state is None:
        raise ValueError(f"No saved state found for invoice_id={invoice_id}")

    # Resolve file_path if missing from historical saved state
    if "file_path" not in state or not state["file_path"]:
        found_path = None
        for folder in ["data/uploads", "data/invoices"]:
            folder_path = Path(folder)
            if folder_path.exists():
                for ext in [".pdf", ".txt", ".json", ".csv", ".xml"]:
                    test_path = folder_path / f"{invoice_id}{ext}"
                    if test_path.exists():
                        found_path = str(test_path.resolve())
                        break
                if found_path:
                    break
        if not found_path:
            raise ValueError(f"Cannot resume invoice: source file path not found for invoice_id={invoice_id}")
        state["file_path"] = found_path

    last_stage = state.get("current_stage", "pending")
    logger.info("Resuming %s from stage: %s", invoice_id, last_stage)

    # Determine next stage to run
    stage_order = ["pending", "ingestion", "validation", "approval", "payment", "audit"]
    try:
        idx = stage_order.index(last_stage)
    except ValueError:
        idx = 0

    # If already complete, just return
    if last_stage == "audit":
        logger.info("Invoice %s already complete — nothing to resume", invoice_id)
        return state

    # Move to the next stage (the failed stage's successor)
    # Re-run from the current stage if it had errors, else from next
    errors = state.get("error_log", [])

    # Build map of stage → whether it completed successfully
    payment_receipt = state.get("payment_receipt")
    payment_ok = payment_receipt is not None and payment_receipt.get("status") in ("success", "skipped")

    completed = {
        "ingestion": state.get("extracted_invoice") is not None,
        "validation": state.get("validation_result") is not None,
        "approval": state.get("approval_decision") is not None,
        "payment": payment_ok,
    }

    # Find the first incomplete stage
    resume_from = None
    for stage in stage_order[1:5]:  # ingestion through payment
        if not completed.get(stage, False):
            resume_from = stage
            break

    if resume_from is None:
        resume_from = "audit"

    logger.info("Resuming from stage: %s", resume_from)

    # Re-run from the determined stage through the pipeline
    # Build a sub-graph starting from resume_from
    stages_to_run = stage_order[stage_order.index(resume_from):]

    node_functions = {
        "ingestion": ingestion_node,
        "validation": validation_node,
        "approval": approval_node,
        "payment": payment_node,
        "audit": audit_node,
    }

    route_functions = {
        "ingestion": route_after_ingestion,
        "validation": route_after_validation,
        "approval": route_after_approval,
    }

    # Run stages sequentially with routing
    current_state = state
    for stage in stages_to_run:
        if stage == "pending":
            continue
        if stage not in node_functions:
            continue

        current_state = node_functions[stage](current_state)

        # Check routing
        if stage in route_functions:
            next_node = route_functions[stage](current_state)
            if next_node == "audit" and stage != "payment":
                # Short-circuit to audit
                current_state = audit_node(current_state)
                break

    return current_state


def manual_override_invoice(
    invoice_id: str,
    action: str,
    reason: str,
    corrected_extracted_invoice: Optional[dict] = None,
) -> dict:
    """Manually override or edit and re-process an invoice.

    Args:
        invoice_id: The ID of the invoice.
        action: 'approve' (force approve), 'reject' (force reject), or 'reprocess'.
        reason: Why the human operator is overriding.
        corrected_extracted_invoice: Optional dictionary with edited invoice fields.
    """
    from src.tools.audit_tools import log_action

    state = load_state(invoice_id)
    if state is None:
        raise ValueError(f"No saved state found for invoice_id={invoice_id}")

    now = _now_iso()
    state["updated_at"] = now
    state["has_errors"] = 0
    state["error_log"] = []

    if action == "reprocess":
        logger.info("[Override] Reprocessing invoice %s with corrected data", invoice_id)
        if not corrected_extracted_invoice:
            raise ValueError("Reprocessing requires corrected_extracted_invoice data")

        # Save edits to state
        state["extracted_invoice"] = corrected_extracted_invoice

        # Log manual correction
        log_action(
            invoice_id=invoice_id,
            agent_name="human_operator",
            action="edit_data",
            result="success",
            reasoning=json.dumps({
                "override_reason": reason,
                "corrected_fields": list(corrected_extracted_invoice.keys())
            }),
        )

        # Clear downstream stages to force re-evaluation
        state["validation_result"] = None
        state["approval_decision"] = None
        state["payment_receipt"] = None

        save_state(state)
        return resume_processing(invoice_id)

    elif action == "approve":
        logger.info("[Override] Forcing approval of invoice %s", invoice_id)

        # Force approval
        state["approval_decision"] = {
            "invoice_id": invoice_id,
            "status": "approved",
            "reasoning": f"Manual human override: {reason}",
            "rule_applied": "manual_override_approved",
            "approved_at": now
        }

        # Log override
        log_action(
            invoice_id=invoice_id,
            agent_name="human_operator",
            action="override_approval",
            result="approved",
            reasoning=json.dumps({"override_reason": reason}),
        )

        state = payment_node(state)
        state = audit_node(state)
        return state

    elif action == "reject":
        logger.info("[Override] Forcing rejection of invoice %s", invoice_id)

        # Force rejection
        state["approval_decision"] = {
            "invoice_id": invoice_id,
            "status": "rejected",
            "reasoning": f"Manual human override: {reason}",
            "rule_applied": "manual_override_rejected",
            "approved_at": now
        }

        # Log override
        log_action(
            invoice_id=invoice_id,
            agent_name="human_operator",
            action="override_approval",
            result="rejected",
            reasoning=json.dumps({"override_reason": reason}),
        )

        state["payment_receipt"] = {
            "invoice_id": invoice_id,
            "transaction_id": "",
            "vendor_name": state.get("extracted_invoice", {}).get("vendor_name", "Unknown"),
            "amount": 0.0,
            "status": "skipped",
            "error": f"Manual override rejection: {reason}",
            "paid_at": now
        }

        state = audit_node(state)
        return state

    else:
        raise ValueError(f"Unknown override action: {action}")
