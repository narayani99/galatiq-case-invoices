"""CLI entry point for the Galatiq Invoice Processing System.

Subcommands::

    python src/main.py process       --invoice_path=<file>
    python src/main.py process-batch --invoice_dir=<dir> [--max_workers=N]
    python src/main.py resume        --invoice_id=<id>
    python src/main.py init-db

Exit codes: 0 = success, 1 = failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from tabulate import tabulate

from src.database import init_database

# Ensure the project root is on ``sys.path`` so ``src.*`` imports resolve
# when the script is invoked directly (``python src/main.py``).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_result(state: dict) -> dict:
    """Reshape a ProcessingState into the documented CLI output format."""
    validation = state.get("validation_result") or {}
    approval = state.get("approval_decision") or {}
    payment = state.get("payment_receipt") or {}
    errors = state.get("error_log", [])

    # Derive a human-friendly status
    if errors:
        status = "error"
    elif payment.get("status") == "failure":
        status = "error"
    elif validation.get("status") == "reject":
        status = "rejected"
    elif approval.get("status") == "rejected":
        status = "rejected"
    elif payment.get("status") == "success":
        status = "approved"
    elif payment.get("status") == "skipped":
        status = "rejected"
    else:
        status = approval.get("status", validation.get("status", "unknown"))

    return {
        "invoice_id": state.get("invoice_id", "UNKNOWN"),
        "status": status,
        "current_stage": state.get("current_stage", "unknown"),
        "extracted": state.get("extracted_invoice"),
        "validation": state.get("validation_result"),
        "approval": approval or None,
        "payment": payment or None,
        "errors": errors if errors else None,
    }


def _status_symbol(status: str) -> str:
    """Return a visual indicator for a processing result."""
    if status in ("approved", "success"):
        return "[OK]"
    elif status in ("rejected", "failure"):
        return "[REJECTED]"
    elif status == "error":
        return "[ERROR]"
    return "[?]"


# ---------------------------------------------------------------------------
# Subcommand Handlers
# ---------------------------------------------------------------------------

def cmd_process(args: argparse.Namespace) -> int:
    """Process a single invoice through the full pipeline."""
    from src.orchestrator import process_invoice

    invoice_path = Path(args.invoice_path)
    if not invoice_path.exists():
        print(f"Error: File not found: {invoice_path}", file=sys.stderr)
        return 1
    
    db_path = Path(os.environ.get("DATABASE_PATH", "./inventory.db"))
    if not db_path.exists():
        try:
            init_database()
        except Exception as exc:
            print(f"Error initializing database: {exc}", file=sys.stderr)
    
    try:
        state = process_invoice(str(invoice_path))
        output = _format_result(state)
        # print(json.dumps(output, indent=2, default=str))
        summary = summarize_invoice(output)

        print(tabulate(summary.items(),
               headers=["Field", "Value"],
               tablefmt="grid"))
        return 0 if output["status"] not in ("error",) else 1
    except Exception as exc:
        print(f"Error processing invoice: {exc}", file=sys.stderr)
        return 1


def cmd_process_batch(args: argparse.Namespace) -> int:
    """Process all invoices in a directory."""
    from src.orchestrator import process_invoice

    invoice_dir = Path(args.invoice_dir)
    if not invoice_dir.is_dir():
        print(f"Error: Directory not found: {invoice_dir}", file=sys.stderr)
        return 1

    # Collect all invoice files (supported extensions)
    supported_exts = {".txt", ".json", ".csv", ".xml", ".pdf"}
    files = sorted(
        f for f in invoice_dir.iterdir()
        if f.is_file() and f.suffix.lower() in supported_exts
    )

    if not files:
        print(f"No invoice files found in: {invoice_dir}")
        return 0

    print(f"Processing invoices in: {invoice_dir}")
    print(f"Found {len(files)} invoices.")

    approved = 0
    rejected = 0
    errors = 0
    results = []

    for idx, fpath in enumerate(files, 1):
        fname = fpath.name
        try:
            state = process_invoice(str(fpath))
            output = _format_result(state)
            status = output["status"]

            if status in ("approved", "success"):
                approved += 1
            elif status in ("rejected",):
                rejected += 1
            else:
                errors += 1

            sym = _status_symbol(status)
            print(f"[{idx}/{len(files)}] {fname} ... {status} {sym}")
            results.append(output)

        except Exception as exc:
            errors += 1
            print(f"[{idx}/{len(files)}] {fname} ... error [ERROR] ({exc})")
            results.append({
                "invoice_id": fname,
                "status": "error",
                "error": str(exc),
            })

    # Summary
    print()
    print("Summary:")
    print(f"  Processed: {len(files)}")
    print(f"  Approved:  {approved}")
    print(f"  Rejected:  {rejected}")
    print(f"  Errors:    {errors}")

    return 0 if errors == 0 else 1


def cmd_resume(args: argparse.Namespace) -> int:
    """Resume processing from last checkpoint."""
    from src.orchestrator import resume_processing

    invoice_id = args.invoice_id

    try:
        state = resume_processing(invoice_id)
        output = _format_result(state)
        output["resumed_from_stage"] = state.get("current_stage", "unknown")
        print(json.dumps(output, indent=2, default=str))
        return 0
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error resuming invoice: {exc}", file=sys.stderr)
        return 1


def summarize_invoice(data: dict) -> dict:
    extracted = data.get("extracted", {})
    validation = data.get("validation", {})
    approval = data.get("approval", {})
    payment = data.get("payment", {})

    return {
        "Invoice ID": data.get("invoice_id"),
        "Vendor": extracted.get("vendor_name"),
        "Invoice Date": extracted.get("invoice_date"),
        "Due Date": extracted.get("due_date"),
        "Amount": extracted.get("total_amount"),
        "Workflow Status": data.get("status"),
        "Current Stage": data.get("current_stage"),

        "Validation Status": validation.get("status"),
        "Risk Score": validation.get("risk_score"),
        "Validation Reason": validation.get("reasoning"),

        "Approval Status": approval.get("status"),
        "Approval Rule": approval.get("rule_applied"),
        "Approval Reason": approval.get("reasoning"),

        "Payment Status": payment.get("status"),
        "Transaction ID": payment.get("transaction_id"),
        "Paid At": payment.get("paid_at"),

        "Errors": data.get("errors"),
    }
    
def cmd_init_db(args: argparse.Namespace) -> int:
    """Initialise the database with schema and seed data."""
    from src.database import init_database

    try:
        init_database()
        return 0
    except Exception as exc:
        print(f"Error initializing database: {exc}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Argument Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="galatiq-invoices",
        description="Galatiq Invoice Processing System — CLI",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        help="Available commands",
    )

    # -- process --
    sp_process = subparsers.add_parser(
        "process",
        help="Process a single invoice file",
    )
    sp_process.add_argument(
        "--invoice_path",
        required=True,
        help="Path to the invoice file",
    )

    # -- process-batch --
    sp_batch = subparsers.add_parser(
        "process-batch",
        help="Process all invoices in a directory",
    )
    sp_batch.add_argument(
        "--invoice_dir",
        required=True,
        help="Directory containing invoice files",
    )
    sp_batch.add_argument(
        "--max_workers",
        type=int,
        default=5,
        help="Maximum parallel workers (default: 5, currently sequential)",
    )

    # -- resume --
    sp_resume = subparsers.add_parser(
        "resume",
        help="Resume processing a partially completed invoice",
    )
    sp_resume.add_argument(
        "--invoice_id",
        required=True,
        help="Invoice ID to resume",
    )

    # -- init-db --
    subparsers.add_parser(
        "init-db",
        help="Initialise the database with schema and seed data",
    )

    return parser


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    """Parse arguments and dispatch to the appropriate subcommand."""
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 1

    handlers = {
        "process": cmd_process,
        "process-batch": cmd_process_batch,
        "resume": cmd_resume,
        "init-db": cmd_init_db,
    }

    handler = handlers.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
