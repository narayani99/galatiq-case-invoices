"""Inventory and vendor lookup tools.

Provides database-backed queries for inventory stock checks and vendor
risk/blacklist lookups. Used by the Validation Agent.
"""

from __future__ import annotations

from src.config import get_logger
from src.database import get_connection

logger = get_logger("tools.inventory")


def check_inventory(item_name: str, quantity: int) -> dict:
    """Query the inventory table for an item and compare against requested qty.

    Args:
        item_name: Name of the inventory item to look up.
        quantity: Requested quantity from the invoice line item.

    Returns:
        dict with keys:
            item_name, requested_qty, available_stock, found, status
        where status is one of:
            'ok'       – item exists and stock >= requested qty
            'mismatch' – item exists but stock < requested qty
            'out_of_stock' – item exists but stock is 0
            'unknown'  – item not found in inventory
    """
    try:
        with get_connection() as conn:
            cursor = conn.execute(
                "SELECT stock FROM inventory WHERE item_name = ?",
                (item_name,),
            )
            row = cursor.fetchone()

        if row is None:
            logger.info("Item not found in inventory: %s", item_name)
            return {
                "item_name": item_name,
                "requested_qty": quantity,
                "available_stock": 0,
                "found": False,
                "status": "unknown",
            }

        available_stock = row[0]

        if available_stock == 0:
            status = "out_of_stock"
        elif quantity > available_stock:
            status = "mismatch"
        else:
            status = "ok"

        logger.info(
            "Inventory check: %s — requested=%d, available=%d, status=%s",
            item_name,
            quantity,
            available_stock,
            status,
        )
        return {
            "item_name": item_name,
            "requested_qty": quantity,
            "available_stock": available_stock,
            "found": True,
            "status": status,
        }

    except Exception as exc:
        logger.error("Inventory check failed for %s: %s", item_name, exc)
        return {
            "item_name": item_name,
            "requested_qty": quantity,
            "available_stock": 0,
            "found": False,
            "status": "unknown",
        }


def query_vendor(vendor_name: str) -> dict:
    """Query the vendors table for risk score and blacklist status.

    Args:
        vendor_name: Name of the vendor to look up.

    Returns:
        dict with keys:
            vendor_name, risk_score, is_blacklisted, payment_method, found
        If the vendor is not found, returns found=False with a default
        risk_score of 0.5.
    """
    try:
        with get_connection() as conn:
            cursor = conn.execute(
                "SELECT risk_score, is_blacklisted, payment_method "
                "FROM vendors WHERE vendor_name = ?",
                (vendor_name,),
            )
            row = cursor.fetchone()

        if row is None:
            logger.info("Vendor not found: %s (using defaults)", vendor_name)
            return {
                "vendor_name": vendor_name,
                "risk_score": 0.5,
                "is_blacklisted": False,
                "payment_method": "unknown",
                "found": False,
            }

        logger.info(
            "Vendor lookup: %s — risk=%.2f, blacklisted=%s",
            vendor_name,
            row[0],
            bool(row[1]),
        )
        return {
            "vendor_name": vendor_name,
            "risk_score": float(row[0]),
            "is_blacklisted": bool(row[1]),
            "payment_method": row[2] or "unknown",
            "found": True,
        }

    except Exception as exc:
        logger.error("Vendor query failed for %s: %s", vendor_name, exc)
        return {
            "vendor_name": vendor_name,
            "risk_score": 0.5,
            "is_blacklisted": False,
            "payment_method": "unknown",
            "found": False,
        }
