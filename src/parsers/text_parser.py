"""
Text invoice parser for the Galatiq Invoice Processing System.

Handles a wide variety of plain-text invoice formats observed in the
sample data set:

  * Clean format       (invoice_1001.txt): standard labels and values
  * Typo format        (invoice_1002.txt): misspelled labels (INVOCE, Vndr, Itms)
  * Fraudulent format  (invoice_1003.txt): unusual amounts / urgency
  * Email-embedded     (invoice_1008.txt): invoice buried inside email headers
  * Tabular            (invoice_1010/1011.txt): column-aligned line items
  * OCR-like           (invoice_1012.txt): spaced header, OCR artefacts (2O26 → 2026)

Strategy:
  1. Try multiple regex patterns for each field (vendor, invoice #, dates, etc.)
  2. Parse line items with several known formats
  3. Always return *something* (partial data + raw_text for LLM fallback)
"""

import re
import logging
from typing import Optional, List, Dict, Any

from src.parsers.base import BaseParser, ParsedInvoice

logger = logging.getLogger(__name__)


class TextParser(BaseParser):
    """Parser for plain-text (.txt) invoice files."""

    # ------------------------------------------------------------------
    # Vendor patterns (ordered from most specific to broadest)
    # ------------------------------------------------------------------
    _VENDOR_PATTERNS = [
        # "Vendor: Foo Bar"
        re.compile(r'(?:Vendor|Vndr|Supplier|FROM)\s*:\s*(.+)', re.IGNORECASE),
        # "Bill From: Foo Bar"
        re.compile(r'Bill\s*From\s*:\s*(.+)', re.IGNORECASE),
    ]

    # ------------------------------------------------------------------
    # Invoice number patterns
    # ------------------------------------------------------------------
    _INVOICE_NUM_PATTERNS = [
        # "Invoice Number: INV-1001" / "Inv #: 1002" / "INV NO:  INV 1012"
        re.compile(
            r'(?:Invoice\s*(?:Number|No|#)|INVOCE\s*(?:Number|No|#)?|Inv\s*(?:#|No)|INV\s*NO)\s*[:\s]\s*(.+)',
            re.IGNORECASE,
        ),
        # "Invoice: INV-1008"
        re.compile(r'Invoice\s*:\s*(INV[\-\s]?\d+)', re.IGNORECASE),
        # "INVOICE #INV-1010" — header with embedded number
        re.compile(r'INVOCE?\s*#\s*(INV[\-\s]?\d+)', re.IGNORECASE),
    ]

    # ------------------------------------------------------------------
    # Date patterns
    # ------------------------------------------------------------------
    _DATE_PATTERNS = [
        # "Date: 2026-01-15"
        re.compile(r'(?:^|\n)\s*(?:Date|Dt)\s*:\s*(.+)', re.IGNORECASE),
        # "DATE:   26-Jan-2O26" (OCR artefact handled post-capture)
        re.compile(r'DATE\s*:\s*(.+)', re.IGNORECASE),
    ]

    _DUE_DATE_PATTERNS = [
        re.compile(r'(?:Due\s*Date|Due\s*Dt|Due)\s*:\s*(.+)', re.IGNORECASE),
        re.compile(r'DUE\s*:\s*(.+)', re.IGNORECASE),
    ]

    # ------------------------------------------------------------------
    # Total amount patterns
    # ------------------------------------------------------------------
    _TOTAL_PATTERNS = [
        # "Total Amount: $5,000.00" / "Total: $9,900.00" / "TOTAL: $9,975.00"
        re.compile(
            r'(?:Total\s*Amount|TOTAL|Total)\s*:\s*\$?([\d,]+\.?\d*)',
            re.IGNORECASE,
        ),
        # "Amt: $15,000.00"
        re.compile(r'Amt\s*:\s*\$?([\d,]+\.?\d*)', re.IGNORECASE),
    ]

    # ------------------------------------------------------------------
    # Payment terms
    # ------------------------------------------------------------------
    _PAYMENT_TERMS_PATTERNS = [
        re.compile(
            r'(?:Payment\s*Terms|Pymnt\s*Terms|Terms)\s*:\s*(.+)',
            re.IGNORECASE,
        ),
    ]

    # ------------------------------------------------------------------
    # Line-item patterns (multiple formats)
    # ------------------------------------------------------------------
    # Format 1 (clean):   "WidgetA    qty: 10    unit price: $250.00"
    _ITEM_CLEAN = re.compile(
        r'^\s*(\S+(?:\s+\([^)]+\))?)\s+'       # item name (optionally with parenthetical like "WidgetA (rush order)")
        r'qty\s*:\s*(\d+)\s+'                   # qty: 10
        r'unit\s*price\s*:\s*\$?([\d,.]+)',      # unit price: $250.00
        re.IGNORECASE,
    )

    # Format 2 (typo):    "GadgetX  qty 20   @ $750 ea"
    _ITEM_TYPO = re.compile(
        r'^\s*(\S+)\s+'
        r'qty\s+(\d+)\s+'
        r'@\s*\$?([\d,.]+)\s*(?:ea|each)?',
        re.IGNORECASE,
    )

    # Format 3 (email):   "- SuperGizmo       x12     $400.00 each"
    _ITEM_EMAIL = re.compile(
        r'^\s*-\s*(\S+)\s+'
        r'x(\d+)\s+'
        r'\$?([\d,.]+)\s*(?:ea|each)?',
        re.IGNORECASE,
    )

    # Format 4 (tabular): "WidgetA                     8      $250.00     $2,000.00"
    #                      "Widget A       12    $250     $3,000.00"
    _ITEM_TABLE = re.compile(
        r'^\s{0,4}'                                # optional leading whitespace
        r'((?:Widget|Gadget|[A-Z][a-z]+)\s*[A-Za-z0-9]*'  # item name start
        r'(?:\s+\([^)]+\))?)\s+'                   # optional parenthetical
        r'(\d+)\s+'                                # quantity
        r'\$?([\d,.]+)\s+'                         # unit price
        r'\$?[\d,.]+',                             # line total (ignored)
        re.IGNORECASE,
    )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self, file_path: str) -> ParsedInvoice:
        """Parse a plain-text invoice file.

        Args:
            file_path: Path to the ``.txt`` file.

        Returns:
            A :class:`ParsedInvoice` with as many fields filled as possible.
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
            self._extract_fields(raw_text, result)
        except Exception as exc:
            msg = f'Unexpected error during text parsing: {exc}'
            logger.error(msg, exc_info=True)
            result.parse_errors.append(msg)

        return result

    # ------------------------------------------------------------------
    # Internal extraction helpers
    # ------------------------------------------------------------------

    def _extract_fields(self, text: str, result: ParsedInvoice) -> None:
        """Populate *result* by running regex extraction on *text*."""
        # Fix common OCR artefacts before matching (e.g. '2O26' → '2026')
        cleaned = self._fix_ocr_artefacts(text)

        result.vendor_name = self._first_match(self._VENDOR_PATTERNS, cleaned)
        result.invoice_number = self._extract_invoice_number(cleaned)
        result.invoice_date = self._extract_date(cleaned)
        result.due_date = self._extract_due_date(cleaned)
        result.total_amount = self._extract_total(cleaned)
        result.payment_terms = self._first_match(self._PAYMENT_TERMS_PATTERNS, cleaned)
        result.line_items = self._extract_line_items(cleaned)

        # Record warnings for missing critical fields
        if not result.vendor_name:
            result.parse_errors.append('Could not extract vendor name')
        if not result.invoice_number:
            result.parse_errors.append('Could not extract invoice number')
        if result.total_amount is None:
            result.parse_errors.append('Could not extract total amount')
        if not result.line_items:
            result.parse_errors.append('Could not extract any line items')

    # ------------------------------------------------------------------
    # Specific field extractors
    # ------------------------------------------------------------------

    def _extract_invoice_number(self, text: str) -> Optional[str]:
        """Try to extract invoice number with normalisation."""
        # First try structured patterns
        for pat in self._INVOICE_NUM_PATTERNS:
            m = pat.search(text)
            if m:
                raw = m.group(1).strip()
                # Normalise "1002" → "INV-1002", "INV 1012" → "INV-1012"
                return self._normalise_invoice_number(raw)

        # Fallback: look for INV-XXXX or #INV-XXXX anywhere
        m = re.search(r'(INV[\-\s]?\d+)', text, re.IGNORECASE)
        if m:
            return self._normalise_invoice_number(m.group(1).strip())

        return None

    @staticmethod
    def _normalise_invoice_number(raw: str) -> str:
        """Normalise various invoice number formats to 'INV-XXXX'."""
        raw = raw.strip().rstrip('.')
        # Remove leading hash
        raw = raw.lstrip('#').strip()
        # Already well-formed?
        if re.match(r'^INV-\d+$', raw, re.IGNORECASE):
            return raw.upper()
        # "INV 1012" → "INV-1012"
        m = re.match(r'^INV\s+(\d+)$', raw, re.IGNORECASE)
        if m:
            return f'INV-{m.group(1)}'
        # Bare number "1002" → "INV-1002"
        if raw.isdigit():
            return f'INV-{raw}'
        return raw

    def _extract_date(self, text: str) -> Optional[str]:
        """Extract invoice date, avoiding the *due* date line."""
        for pat in self._DATE_PATTERNS:
            for m in pat.finditer(text):
                line = m.group(0)
                # Skip if this is actually a due-date line
                if re.search(r'due', line, re.IGNORECASE):
                    continue
                return m.group(1).strip()
        return None

    def _extract_due_date(self, text: str) -> Optional[str]:
        """Extract due date."""
        return self._first_match(self._DUE_DATE_PATTERNS, text)

    def _extract_total(self, text: str) -> Optional[float]:
        """Extract total amount, preferring "Total Amount" over bare "Total"."""
        for pat in self._TOTAL_PATTERNS:
            m = pat.search(text)
            if m:
                return self._safe_float(m.group(1))
        return None

    def _extract_line_items(self, text: str) -> List[Dict[str, Any]]:
        """Extract line items using multiple format strategies."""
        items: List[Dict[str, Any]] = []

        for line in text.splitlines():
            item = self._try_parse_item_line(line)
            if item:
                items.append(item)

        return items

    def _try_parse_item_line(self, line: str) -> Optional[Dict[str, Any]]:
        """Attempt to parse a single line as a line item."""
        # Strategy 1: clean format — "WidgetA    qty: 10    unit price: $250.00"
        m = self._ITEM_CLEAN.search(line)
        if m:
            return self._build_item(m.group(1), m.group(2), m.group(3))

        # Strategy 2: typo format — "GadgetX  qty 20   @ $750 ea"
        m = self._ITEM_TYPO.search(line)
        if m:
            return self._build_item(m.group(1), m.group(2), m.group(3))

        # Strategy 3: email format — "- SuperGizmo       x12     $400.00 each"
        m = self._ITEM_EMAIL.search(line)
        if m:
            return self._build_item(m.group(1), m.group(2), m.group(3))

        # Strategy 4: tabular format — "WidgetA  8  $250.00  $2,000.00"
        m = self._ITEM_TABLE.search(line)
        if m:
            return self._build_item(m.group(1), m.group(2), m.group(3))

        return None

    def _build_item(
        self,
        name: str,
        qty_str: str,
        price_str: str,
    ) -> Dict[str, Any]:
        """Build a normalised line-item dictionary."""
        item_name = name.strip()
        # Normalise common name variations: "Widget A" → "WidgetA", "Gadget X" → "GadgetX"
        item_name = re.sub(r'^(Widget|Gadget)\s+([A-Za-z0-9])$', r'\1\2', item_name)
        # But preserve parenthetical suffixes like "(rush order)"
        item_name = re.sub(r'^(Widget|Gadget)\s+([A-Za-z0-9])\s+', r'\1\2 ', item_name)

        qty = self._safe_int(qty_str)
        price = self._safe_float(price_str)

        return {
            'item_name': item_name,
            'quantity': qty,
            'unit_price': price,
        }

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _first_match(patterns: list, text: str) -> Optional[str]:
        """Return the first group(1) from the first matching pattern."""
        for pat in patterns:
            m = pat.search(text)
            if m:
                return m.group(1).strip()
        return None

    @staticmethod
    def _fix_ocr_artefacts(text: str) -> str:
        """Fix common OCR mistakes in dates and amounts.

        Examples:
            ``2O26`` → ``2026``   (capital-O instead of zero)
            ``$3,500.O0`` → ``$3,500.00``
        """
        # Replace capital-O that is surrounded by digits (likely OCR zero)
        fixed = re.sub(r'(?<=\d)O(?=\d)', '0', text)
        return fixed
