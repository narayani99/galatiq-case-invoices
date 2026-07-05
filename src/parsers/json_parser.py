"""
JSON invoice parser for the Galatiq Invoice Processing System.

Handles the JSON invoice formats observed in the sample data:

  * Clean format        (invoice_1004.json): nested vendor, line_items with "item" key
  * Multi-item          (invoice_1013.json): 8+ line items with notes and volume discounts
  * Malformed           (invoice_1009.json): empty vendor name, null due_date, negative qty
  * Unknown items       (invoice_1016.json): items not in inventory (WidgetC)

Key design decisions:
  - Vendor name extracted from nested ``{"vendor": {"name": "..."}}`` or flat ``"vendor_name"``
  - Line-item key normalisation: ``item`` / ``item_name`` / ``name`` → ``item_name``
  - Graceful on ``json.JSONDecodeError`` — returns raw text for LLM fallback
"""

import json
import logging
from typing import Optional, Dict, Any, List

from src.parsers.base import BaseParser, ParsedInvoice

logger = logging.getLogger(__name__)


class JsonParser(BaseParser):
    """Parser for JSON (.json) invoice files."""

    def parse(self, file_path: str) -> ParsedInvoice:
        """Parse a JSON invoice file.

        Args:
            file_path: Path to the ``.json`` file.

        Returns:
            A :class:`ParsedInvoice` with extracted data and any errors.
        """
        result = ParsedInvoice()

        # Read raw text
        try:
            raw_text = self._read_file_text(file_path)
            result.raw_text = raw_text
        except Exception as exc:
            msg = f'Failed to read file {file_path}: {exc}'
            logger.error(msg)
            result.parse_errors.append(msg)
            return result

        # Decode JSON
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            msg = f'Invalid JSON in {file_path}: {exc}'
            logger.warning(msg)
            result.parse_errors.append(msg)
            return result

        if not isinstance(data, dict):
            result.parse_errors.append(f'Expected JSON object, got {type(data).__name__}')
            return result

        try:
            self._extract(data, result)
        except Exception as exc:
            msg = f'Unexpected error extracting from JSON: {exc}'
            logger.error(msg, exc_info=True)
            result.parse_errors.append(msg)

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract(self, data: Dict[str, Any], result: ParsedInvoice) -> None:
        """Extract all fields from the parsed JSON dict."""
        # Invoice number
        result.invoice_number = self._get_str(data, 'invoice_number', 'invoice_id', 'inv_number')

        # Vendor (nested or flat)
        result.vendor_name = self._extract_vendor(data)

        # Dates
        result.invoice_date = self._get_str(data, 'date', 'invoice_date')
        result.due_date = self._get_str(data, 'due_date', 'payment_due_date')

        # Amounts
        result.total_amount = self._safe_float(data.get('total', data.get('total_amount')))
        result.subtotal = self._safe_float(data.get('subtotal'))
        result.tax_amount = self._safe_float(data.get('tax_amount', data.get('tax')))

        # Currency & terms
        result.currency = self._get_str(data, 'currency')
        result.payment_terms = self._get_str(data, 'payment_terms')

        # Line items
        result.line_items = self._extract_line_items(data, result)

        # Warnings
        if not result.vendor_name:
            result.parse_errors.append('Vendor name is empty or missing')
        if result.due_date is None:
            result.parse_errors.append('Due date is null or missing')
        if result.total_amount is not None and result.total_amount < 0:
            result.parse_errors.append(f'Negative total amount: {result.total_amount}')

    def _extract_vendor(self, data: Dict[str, Any]) -> Optional[str]:
        """Extract vendor name from nested or flat structures."""
        vendor = data.get('vendor')
        if isinstance(vendor, dict):
            name = vendor.get('name', '')
            return name if name else None
        if isinstance(vendor, str) and vendor:
            return vendor

        # Flat key fallbacks
        for key in ('vendor_name', 'supplier', 'supplier_name'):
            val = data.get(key)
            if val:
                return str(val)

        return None

    def _extract_line_items(
        self, data: Dict[str, Any], result: ParsedInvoice
    ) -> List[Dict[str, Any]]:
        """Extract and normalise line items."""
        raw_items = data.get('line_items', data.get('items', []))
        if not isinstance(raw_items, list):
            result.parse_errors.append(f'line_items is not a list: {type(raw_items).__name__}')
            return []

        items: List[Dict[str, Any]] = []
        for idx, raw in enumerate(raw_items):
            if not isinstance(raw, dict):
                result.parse_errors.append(f'line_items[{idx}] is not a dict')
                continue

            # Normalise item name key
            item_name = (
                raw.get('item_name')
                or raw.get('item')
                or raw.get('name')
                or raw.get('description')
                or ''
            )

            qty = self._safe_int(raw.get('quantity', raw.get('qty')))
            unit_price = self._safe_float(raw.get('unit_price', raw.get('price')))

            # Flag suspicious values
            if qty is not None and qty < 0:
                result.parse_errors.append(
                    f'Negative quantity for item "{item_name}": {qty}'
                )

            items.append({
                'item_name': str(item_name),
                'quantity': qty,
                'unit_price': unit_price,
            })

        return items

    @staticmethod
    def _get_str(data: Dict[str, Any], *keys: str) -> Optional[str]:
        """Return the first non-empty string value for the given keys."""
        for key in keys:
            val = data.get(key)
            if val is not None:
                s = str(val).strip()
                if s:
                    return s
        return None
