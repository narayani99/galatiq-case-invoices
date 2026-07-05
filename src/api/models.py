"""
API-specific Pydantic request/response schemas.

These models define the wire format for the FastAPI endpoints
and are separate from the core domain models in ``src.models``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Upload ────────────────────────────────────────────────────────────────


class InvoiceUploadResponse(BaseModel):
    """Returned by ``POST /api/invoices/upload`` (202 Accepted)."""

    invoice_id: str
    status: str = Field(default="processing", description="Initial status after upload")
    created_at: datetime


# ── Single Invoice Status ─────────────────────────────────────────────────


class InvoiceStatusResponse(BaseModel):
    """Returned by ``GET /api/invoices/{invoice_id}`` (200 OK)."""

    invoice_id: str
    current_stage: str
    extracted_invoice: Optional[Dict[str, Any]] = None
    validation_result: Optional[Dict[str, Any]] = None
    approval_decision: Optional[Dict[str, Any]] = None
    payment_receipt: Optional[Dict[str, Any]] = None
    error_log: List[str] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ── Audit Trail ───────────────────────────────────────────────────────────


class AuditEntryOut(BaseModel):
    """Serialisable audit entry for API responses."""

    id: int
    timestamp: Optional[datetime] = None
    agent_name: str
    action: str
    result: str
    reasoning: Dict[str, Any] = Field(default_factory=dict)
    error_msg: Optional[str] = None
    duration_ms: int = 0


class AuditTrailResponse(BaseModel):
    """Returned by ``GET /api/invoices/{invoice_id}/audit`` (200 OK)."""

    invoice_id: str
    audit_entries: List[AuditEntryOut] = Field(default_factory=list)


# ── Invoice List ──────────────────────────────────────────────────────────


class InvoiceSummary(BaseModel):
    """Condensed invoice record for list views."""

    invoice_id: str
    vendor_name: Optional[str] = None
    total_amount: Optional[float] = None
    status: Optional[str] = None
    current_stage: str
    created_at: Optional[datetime] = None


class InvoiceListResponse(BaseModel):
    """Returned by ``GET /api/invoices`` (200 OK)."""

    total: int
    page: int
    limit: int
    invoices: List[InvoiceSummary] = Field(default_factory=list)


# ── Dashboard Stats ──────────────────────────────────────────────────────


class VendorStats(BaseModel):
    """Per-vendor aggregate stats."""

    count: int = 0
    approved: int = 0
    total: float = 0.0


class StatsResponse(BaseModel):
    """Returned by ``GET /api/stats`` (200 OK)."""

    total_invoices: int = 0
    approved: int = 0
    rejected: int = 0
    processing: int = 0
    approved_percent: float = 0.0
    rejected_percent: float = 0.0
    total_amount_approved: float = 0.0
    avg_processing_time_ms: float = 0.0
    by_vendor: Dict[str, VendorStats] = Field(default_factory=dict)
    by_approval_rule: Dict[str, int] = Field(default_factory=dict)


# ── Retry ─────────────────────────────────────────────────────────────────


class RetryResponse(BaseModel):
    """Returned by ``POST /api/invoices/{invoice_id}/retry`` (202 Accepted)."""

    invoice_id: str
    status: str = "processing"
    resumed_from_stage: Optional[str] = None
    retry_count: int = 0


# ── Error ─────────────────────────────────────────────────────────────────


class ErrorResponse(BaseModel):
    """Standard error envelope returned on 4xx / 5xx responses."""

    error: str
    detail: Optional[str] = None


# ── Override ──────────────────────────────────────────────────────────────


class OverrideRequest(BaseModel):
    """Payload for POST /api/invoices/{invoice_id}/override."""

    action: str = Field(..., description="Action: 'approve', 'reject', or 'reprocess'")
    reason: str = Field(..., description="Audit reason for the override")
    corrected_extracted_invoice: Optional[Dict[str, Any]] = None


class OverrideResponse(BaseModel):
    """Returned by POST /api/invoices/{invoice_id}/override."""

    invoice_id: str
    status: str
    action: str
