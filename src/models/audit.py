"""
Pydantic model for audit log entries.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from pydantic import BaseModel, Field


class AuditEntry(BaseModel):
    """A single row from the ``audit_log`` table."""

    id: int
    invoice_id: str
    timestamp: datetime
    agent_name: str = Field(
        description='Agent that produced the entry: "ingestion", "validation", "approval", "payment"'
    )
    action: str = Field(
        description='Action performed: "extract", "validate", "approve", "pay"'
    )
    result: str = Field(
        description='Outcome: "success", "failure", "pass", "flag", "reject"'
    )
    reasoning: Dict = Field(
        default_factory=dict,
        description="Structured reasoning / decision factors (JSON)",
    )
    error_msg: Optional[str] = Field(
        default=None, description="Error message if the action failed"
    )
    duration_ms: int = Field(
        default=0, description="Processing time in milliseconds"
    )
