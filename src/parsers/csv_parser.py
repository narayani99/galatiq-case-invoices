"""
CSV invoice parser for the Galatiq Invoice Processing System.

Supports two distinct CSV layouts observed in the sample data:

1. **Key-value format** (invoice_1006.csv):
   ``field,value`` pairs where line items appear as repeated
   ``item`` / ``quantity`` / ``unit_price`` groups.

2. **Row-based format** (invoice_1007.csv, invoice_1015.csv):
   Header row with columns such as ``Invoice Number``, ``Vendor``,
   ``Item``, ``Qty``, ``Unit Price``, ``Line Total``.  Each item is a
   separate row; summary rows (Subtotal, Tax, Total) follow.

The parser auto-detects the layout by inspecting the first row.
"""

import csv
import io
import logging
import re
from typing import Optional, List, Dict, Any

from src.parsers.base import BaseParser, ParsedInvoice

logger = logging.getLogger(__name__)


class CsvParser(BaseParser):
    """Parser for CSV (.csv) invoice files."""

    def parse(self, file_path: str) -> ParsedInvoice:
        """Parse a CSV invoice file.

        Args:
            file_path: Path to the ``.csv`` file.

        Returns:
            A :class:`ParsedInvoice` with extracted data and any errors.
        """
        result = ParsedInvoice()

        try:
            raw_text = self._read_file_text(file_path)
            result.raw_text = raw_text
        except Exception as exc:
            msg = f'Failed to read file {file_path}: {exc}'
            logger.error(msg)
            result.parse_errors.append(msg)
            return result

        try:
            rows = list(csv.reader(io.StringIO(raw_text)))
        except Exception as exc:
            msg = f'CSV parsing failed for {file_path}: {exc}'
            logger.warning(msg)
            result.parse_errors.append(msg)
            return result

        if not rows:
            result.parse_errors.append('CSV file is empty')
            return result

        try:
            if self._is_key_value_format(rows):
                self._parse_key_value(rows, result)
            else:
                self._parse_row_based(rows, result)
        except Exception as exc:
            msg = f'Unexpected error during CSV extraction: {exc}'
            logger.error(msg, exc_info=True)
            result.parse_errors.append(msg)

        return result

    # ------------------------------------------------------------------
    # Format detection
    # ------------------------------------------------------------------

    @staticmethod
    def _is_key_value_format(rows: List[List[str]]) -> bool:
        """Detect the key-value CSV layout by checking the header row."""
        if not rows:
            return False
        header = [c.strip().lower() for c in rows[0]]
        # Key-value format has exactly two columns: "field", "value"
        return header == ['field', 'value']

    # ------------------------------------------------------------------
    # Key-value CSV parser
    # ------------------------------------------------------------------

    def _parse_key_value(self, rows: List[List[str]], result: ParsedInvoice) -> None:
        """Parse ``field,value`` CSV (e.g. invoice_1006.csv).

        Line items appear as repeated ``item``/``quantity``/``unit_price``
        groups in row order.
        """
        current_item: Dict[str, Any] = {}
        items: List[Dict[str, Any]] = []

        for row in rows[1:]:  # skip header
            if len(row) < 2:
                continue
            key = row[0].strip().lower()
            value = row[1].strip()

            if key == 'invoice_number':
                result.invoice_number = value
            elif key == 'vendor':
                result.vendor_name = value
            elif key == 'date':
                result.invoice_date = value
            elif key == 'due_date':
                result.due_date = value
            elif key == 'total':
                result.total_amount = self._safe_float(value)
            elif key == 'subtotal':
                result.subtotal = self._safe_float(value)
            elif key in ('tax', 'tax_amount'):
                result.tax_amount = self._safe_float(value)
            elif key == 'payment_terms':
                result.payment_terms = value
            elif key == 'currency':
                result.currency = value
            elif key == 'item':
                # Flush previous item if exists
                if current_item.get('item_name'):
                    items.append(current_item)
                current_item = {'item_name': value, 'quantity': None, 'unit_price': None}
            elif key == 'quantity':
                current_item['quantity'] = self._safe_int(value)
            elif key == 'unit_price':
                current_item['unit_price'] = self._safe_float(value)

        # Flush last item
        if current_item.get('item_name'):
            items.append(current_item)

        result.line_items = items

        # Warnings
        if not result.invoice_number:
            result.parse_errors.append('Could not extract invoice number from key-value CSV')
        if not result.vendor_name:
            result.parse_errors.append('Could not extract vendor name from key-value CSV')

    # ------------------------------------------------------------------
    # Row-based CSV parser
    # ------------------------------------------------------------------

    def _parse_row_based(self, rows: List[List[str]], result: ParsedInvoice) -> None:
        """Parse columnar CSV (e.g. invoice_1007.csv, invoice_1015.csv).

        Expects a header row mapping to known column names followed by
        one row per line item.  Summary rows (Subtotal/Tax/Total) are
        detected by having mostly-empty leading cells.
        """
        header = [c.strip().lower() for c in rows[0]]

        # Build column index lookup
        col_map = self._build_column_map(header)

        items: List[Dict[str, Any]] = []

        for row in rows[1:]:
            # Pad row to header length
            while len(row) < len(header):
                row.append('')

            # Detect summary rows (leading cells empty, last cell has value)
            leading_empty = all(cell.strip() == '' for cell in row[:4])
            if leading_empty:
                self._parse_summary_row(row, header, col_map, result)
                continue

            # Normal data row
            if col_map.get('invoice_number') is not None and not result.invoice_number:
                result.invoice_number = row[col_map['invoice_number']].strip() or None
            if col_map.get('vendor') is not None and not result.vendor_name:
                result.vendor_name = row[col_map['vendor']].strip() or None
            if col_map.get('date') is not None and not result.invoice_date:
                result.invoice_date = row[col_map['date']].strip() or None
            if col_map.get('due_date') is not None and not result.due_date:
                result.due_date = row[col_map['due_date']].strip() or None

            # Line item
            item_name = row[col_map['item']].strip() if col_map.get('item') is not None else ''
            if item_name:
                qty = self._safe_int(row[col_map['qty']]) if col_map.get('qty') is not None else None
                price = self._safe_float(row[col_map['unit_price']]) if col_map.get('unit_price') is not None else None
                items.append({
                    'item_name': item_name,
                    'quantity': qty,
                    'unit_price': price,
                })

        result.line_items = items

    def _parse_summary_row(
        self,
        row: List[str],
        header: List[str],
        col_map: Dict[str, int],
        result: ParsedInvoice,
    ) -> None:
        """Parse a summary row (Subtotal/Tax/Total) from the row-based format."""
        # Look for label:value in the last two columns
        for i in range(len(row) - 2, -1, -1):
            cell = row[i].strip().rstrip(':')
            if not cell:
                continue
            label = cell.lower()
            # Next column contains the value
            val_cell = row[i + 1].strip() if i + 1 < len(row) else ''
            amount = self._safe_float(val_cell)

            if 'total' in label and 'sub' not in label and 'tax' not in label:
                if amount is not None:
                    result.total_amount = amount
            elif 'subtotal' in label:
                if amount is not None:
                    result.subtotal = amount
            elif 'tax' in label:
                if amount is not None:
                    result.tax_amount = amount
            break  # only process first label found

    @staticmethod
    def _build_column_map(header: List[str]) -> Dict[str, Optional[int]]:
        """Map normalised column names to their index in the header."""
        col_map: Dict[str, Optional[int]] = {
            'invoice_number': None,
            'vendor': None,
            'date': None,
            'due_date': None,
            'item': None,
            'qty': None,
            'unit_price': None,
            'line_total': None,
        }

        aliases = {
            'invoice_number': ('invoice number', 'invoice_number', 'invoice no', 'inv #'),
            'vendor': ('vendor', 'vendor_name', 'supplier'),
            'date': ('date', 'invoice_date', 'invoice date'),
            'due_date': ('due date', 'due_date', 'payment_due_date'),
            'item': ('item', 'item_name', 'description', 'product'),
            'qty': ('qty', 'quantity', 'count'),
            'unit_price': ('unit price', 'unit_price', 'price', 'rate'),
            'line_total': ('line total', 'line_total', 'amount', 'total'),
        }

        for idx, col in enumerate(header):
            for field, names in aliases.items():
                if col in names:
                    col_map[field] = idx
                    break

        return col_map
