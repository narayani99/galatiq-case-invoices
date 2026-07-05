"""
Pydantic models for invoice processing.

All models from the 03_SPECIFICATIONS.md schema:
- LineItem, ExtractedInvoice, InventoryMismatch
- ValidationResult, ApprovalDecision, PaymentReceipt
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class LineItem(BaseModel):
    """A single line item on an invoice."""

    item_name: str
    quantity: int = Field(description="Number of units ordered (negative values flagged by validation)")
    unit_price: Optional[Decimal] = Field(
        default=None, description="Price per unit"
    )
    total: Optional[Decimal] = Field(
        default=None, description="Line total (quantity × unit_price)"
    )


class ExtractedInvoice(BaseModel):
    """Structured data extracted from a raw invoice file."""

    invoice_id: str
    vendor_name: str
    invoice_date: datetime
    due_date: datetime
    total_amount: Decimal = Field(description="Invoice grand total")
    line_items: List[LineItem]
    extracted_at: datetime
    raw_text: str = Field(description="Original text from the source file")
    extraction_method: str = Field(
        description='How the data was extracted: "parser" or "grok"'
    )
    confidence_scores: Dict[str, float] = Field(
        default_factory=dict,
        description="Per-field confidence (0.0–1.0)",
    )
    extraction_errors: List[str] = Field(
        default_factory=list,
        description="Non-fatal issues encountered during extraction",
    )


class InventoryMismatch(BaseModel):
    """A single inventory discrepancy found during validation."""

    item_name: str
    requested_qty: int
    available_stock: int
    status: str = Field(
        description='Mismatch type: "unknown", "mismatch", or "out_of_stock"'
    )


class ValidationResult(BaseModel):
    """Result of validating an extracted invoice against inventory & vendor data."""

    invoice_id: str
    status: str = Field(
        description='Validation outcome: "pass", "flag", or "reject"'
    )
    mismatches: List[InventoryMismatch] = Field(default_factory=list)
    risk_score: Decimal = Field(
        ge=0, le=1, description="Aggregate risk score (0.0–1.0)"
    )
    vendor_risk: Optional[Decimal] = Field(
        default=None, description="Vendor-specific risk score"
    )
    reasoning: str = Field(description="Human-readable explanation")
    validated_at: datetime


class ApprovalDecision(BaseModel):
    """Approval or rejection decision for a validated invoice."""

    invoice_id: str
    status: str = Field(
        description='Decision: "approved" or "rejected"'
    )
    reasoning: str
    rule_applied: str = Field(
        description=(
            'Business rule that drove the decision, e.g. '
            '"auto_approve_low_value", "manual_review_high_value"'
        )
    )
    override_reason: Optional[str] = Field(
        default=None,
        description="Reason for overriding the default rule (if any)",
    )
    approved_at: datetime


class PaymentReceipt(BaseModel):
    """Receipt from a (mock) payment transaction."""

    invoice_id: str
    transaction_id: str
    vendor_name: str
    amount: Decimal
    status: str = Field(description='Payment status: "success" or "failure"')
    error: Optional[str] = Field(
        default=None, description="Error details on failure"
    )
    paid_at: datetime
