import pytest
from decimal import Decimal
from src.parsers import parse_invoice_file
from src.tools.inventory_tools import check_inventory, query_vendor

def test_text_parser():
    data, raw = parse_invoice_file("data/invoices/invoice_1001.txt")
    assert data["invoice_number"] == "INV-1001"
    assert data["vendor_name"] == "Widgets Inc."
    assert data["total_amount"] == 5000.00
    assert len(data["line_items"]) == 2
    assert data["line_items"][0]["item_name"] == "WidgetA"
    assert data["line_items"][0]["quantity"] == 10

def test_json_parser():
    data, raw = parse_invoice_file("data/invoices/invoice_1004.json")
    assert data["invoice_number"] == "INV-1004"
    assert data["vendor_name"] == "Precision Parts Ltd."
    assert data["total_amount"] == 1890.00
    assert len(data["line_items"]) == 2

def test_csv_parser():
    data, raw = parse_invoice_file("data/invoices/invoice_1006.csv")
    assert data["invoice_number"] == "INV-1006"
    assert data["vendor_name"] == "Acme Industrial Supplies"
    assert data["total_amount"] == 2750.00

def test_xml_parser():
    data, raw = parse_invoice_file("data/invoices/invoice_1014.xml")
    assert data["invoice_number"] == "INV-1014"
    assert data["vendor_name"] == "TechParts International"
    assert data["total_amount"] == 4125.00

def test_inventory_check():
    # WidgetA stock is 15 in seed database
    res = check_inventory("WidgetA", 10)
    assert res["status"] == "ok"
    assert res["available_stock"] == 15

    # WidgetB stock is 10 in seed database, request 20 -> mismatch
    res_mismatch = check_inventory("WidgetB", 20)
    assert res_mismatch["status"] == "mismatch"
    assert res_mismatch["available_stock"] == 10

    # FakeItem stock is 0
    res_out = check_inventory("FakeItem", 1)
    assert res_out["status"] == "out_of_stock"

    # Unknown item not in inventory
    res_unk = check_inventory("NonExistentItem", 1)
    assert res_unk["status"] == "unknown"

def test_vendor_query():
    res = query_vendor("Widgets Inc.")
    assert res["found"] is True
    assert res["risk_score"] == 0.2

    res_unk = query_vendor("Unknown Random Vendor")
    assert res_unk["found"] is False
    assert res_unk["risk_score"] == 0.5
