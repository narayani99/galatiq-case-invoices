import pytest
from datetime import datetime, timezone
from decimal import Decimal
from src.models.invoice import ExtractedInvoice, LineItem, ValidationResult
from src.agents.validation_agent import validate_invoice
from src.agents.approval_agent import approve_invoice

@pytest.mark.asyncio
async def test_validation_agent_pass():
    # Normal invoice withWidgetA (qty 10, stock 15) and WidgetB (qty 5, stock 10)
    invoice = ExtractedInvoice(
        invoice_id="INV-TEST-PASS",
        vendor_name="Widgets Inc.",
        invoice_date=datetime.now(timezone.utc),
        due_date=datetime.now(timezone.utc),
        total_amount=Decimal("5000.00"),
        line_items=[
            LineItem(item_name="WidgetA", quantity=10, unit_price=Decimal("250.00"), total=Decimal("2500.00")),
            LineItem(item_name="WidgetB", quantity=5, unit_price=Decimal("500.00"), total=Decimal("2500.00")),
        ],
        extracted_at=datetime.now(timezone.utc),
        raw_text="Test invoice text",
        extraction_method="parser",
        confidence_scores={},
        extraction_errors=[]
    )
    result = await validate_invoice(invoice)
    assert result.status == "pass"
    assert len(result.mismatches) == 0
    assert result.risk_score == Decimal("0.20") # Widgets Inc. risk score is 0.2

@pytest.mark.asyncio
async def test_validation_agent_flag():
    # Quantity exceeds stock: GadgetX (qty 20, stock 5)
    invoice = ExtractedInvoice(
        invoice_id="INV-TEST-FLAG",
        vendor_name="Gadgets Co.",
        invoice_date=datetime.now(timezone.utc),
        due_date=datetime.now(timezone.utc),
        total_amount=Decimal("15000.00"),
        line_items=[
            LineItem(item_name="GadgetX", quantity=20, unit_price=Decimal("750.00"), total=Decimal("15000.00")),
        ],
        extracted_at=datetime.now(timezone.utc),
        raw_text="Test invoice text",
        extraction_method="parser",
        confidence_scores={},
        extraction_errors=[]
    )
    result = await validate_invoice(invoice)
    assert result.status == "flag"
    assert len(result.mismatches) == 1
    assert result.mismatches[0].status == "mismatch"
    assert result.mismatches[0].requested_qty == 20
    assert result.mismatches[0].available_stock == 5

@pytest.mark.asyncio
async def test_validation_agent_reject():
    # Unknown item: NonExistentItem
    invoice = ExtractedInvoice(
        invoice_id="INV-TEST-REJECT",
        vendor_name="Unknown Vendor",
        invoice_date=datetime.now(timezone.utc),
        due_date=datetime.now(timezone.utc),
        total_amount=Decimal("1000.00"),
        line_items=[
            LineItem(item_name="NonExistentItem", quantity=1, unit_price=Decimal("1000.00"), total=Decimal("1000.00")),
        ],
        extracted_at=datetime.now(timezone.utc),
        raw_text="Test invoice text",
        extraction_method="parser",
        confidence_scores={},
        extraction_errors=[]
    )
    result = await validate_invoice(invoice)
    assert result.status == "reject"
    assert len(result.mismatches) == 1
    assert result.mismatches[0].status == "unknown"

@pytest.mark.asyncio
async def test_approval_agent_rules():
    # 1. Low value ($2000) and validation PASS -> auto_approve
    invoice = ExtractedInvoice(
        invoice_id="INV-TEST-LOW",
        vendor_name="Widgets Inc.",
        invoice_date=datetime.now(timezone.utc),
        due_date=datetime.now(timezone.utc),
        total_amount=Decimal("2000.00"),
        line_items=[],
        extracted_at=datetime.now(timezone.utc),
        raw_text="Test text",
        extraction_method="parser"
    )
    validation = ValidationResult(
        invoice_id="INV-TEST-LOW",
        status="pass",
        mismatches=[],
        risk_score=Decimal("0.2"),
        vendor_risk=Decimal("0.2"),
        reasoning="All checks passed",
        validated_at=datetime.now(timezone.utc)
    )
    decision = await approve_invoice(invoice, validation)
    assert decision.status == "approved"
    assert decision.rule_applied == "auto_approve_low_value"

    # 2. Validation REJECT -> rejected
    validation_reject = ValidationResult(
        invoice_id="INV-TEST-LOW",
        status="reject",
        mismatches=[],
        risk_score=Decimal("0.7"),
        vendor_risk=Decimal("0.7"),
        reasoning="Vendor risk or unknown items",
        validated_at=datetime.now(timezone.utc)
    )
    decision_reject = await approve_invoice(invoice, validation_reject)
    assert decision_reject.status == "rejected"
    assert decision_reject.rule_applied == "auto_reject_validation_failed"
