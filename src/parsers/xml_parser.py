"""
XML invoice parser for the Galatiq Invoice Processing System.

Handles the XML invoice structure observed in the sample data
(invoice_1014.xml):

.. code-block:: xml

    <invoice>
      <header>
        <invoice_number>INV-1014</invoice_number>
        <vendor>TechParts International</vendor>
        <date>2026-01-26</date>
        <due_date>2026-02-26</due_date>
        <currency>EUR</currency>
      </header>
      <line_items>
        <item>
          <name>WidgetA</name>
          <quantity>4</quantity>
          <unit_price>225.00</unit_price>
        </item>
        ...
      </line_items>
      <totals>
        <subtotal>3750.00</subtotal>
        <tax_rate>0.10</tax_rate>
        <tax_amount>375.00</tax_amount>
        <total>4125.00</total>
      </totals>
      <payment_terms>Net 30</payment_terms>
    </invoice>

Uses ``xml.etree.ElementTree`` (stdlib).  Never crashes — all exceptions
are caught, logged, and added to ``parse_errors``.
"""

import logging
import xml.etree.ElementTree as ET
from typing import Optional, List, Dict, Any

from src.parsers.base import BaseParser, ParsedInvoice

logger = logging.getLogger(__name__)


class XmlParser(BaseParser):
    """Parser for XML (.xml) invoice files."""

    def parse(self, file_path: str) -> ParsedInvoice:
        """Parse an XML invoice file.

        Args:
            file_path: Path to the ``.xml`` file.

        Returns:
            A :class:`ParsedInvoice` with extracted data and any errors.
        """
        result = ParsedInvoice()

        # Read raw text first (for LLM fallback)
        try:
            raw_text = self._read_file_text(file_path)
            result.raw_text = raw_text
        except Exception as exc:
            msg = f'Failed to read file {file_path}: {exc}'
            logger.error(msg)
            result.parse_errors.append(msg)
            return result

        # Parse XML tree
        try:
            tree = ET.parse(file_path)
            root = tree.getroot()
        except ET.ParseError as exc:
            msg = f'Invalid XML in {file_path}: {exc}'
            logger.warning(msg)
            result.parse_errors.append(msg)
            return result
        except Exception as exc:
            msg = f'Unexpected error parsing XML {file_path}: {exc}'
            logger.error(msg, exc_info=True)
            result.parse_errors.append(msg)
            return result

        try:
            self._extract(root, result)
        except Exception as exc:
            msg = f'Unexpected error extracting XML data: {exc}'
            logger.error(msg, exc_info=True)
            result.parse_errors.append(msg)

        return result

    # ------------------------------------------------------------------
    # Internal extraction
    # ------------------------------------------------------------------

    def _extract(self, root: ET.Element, result: ParsedInvoice) -> None:
        """Extract all fields from the XML element tree."""
        # Header section
        header = root.find('header')
        if header is not None:
            result.invoice_number = self._elem_text(header, 'invoice_number')
            result.vendor_name = self._elem_text(header, 'vendor', 'vendor_name')
            result.invoice_date = self._elem_text(header, 'date', 'invoice_date')
            result.due_date = self._elem_text(header, 'due_date')
            result.currency = self._elem_text(header, 'currency')
        else:
            # Try flat structure (no header wrapper)
            result.invoice_number = self._elem_text(root, 'invoice_number')
            result.vendor_name = self._elem_text(root, 'vendor', 'vendor_name')
            result.invoice_date = self._elem_text(root, 'date', 'invoice_date')
            result.due_date = self._elem_text(root, 'due_date')
            result.currency = self._elem_text(root, 'currency')

        # Line items
        result.line_items = self._extract_line_items(root, result)

        # Totals section
        totals = root.find('totals')
        if totals is not None:
            result.subtotal = self._safe_float(self._elem_text(totals, 'subtotal'))
            result.tax_amount = self._safe_float(self._elem_text(totals, 'tax_amount', 'tax'))
            result.total_amount = self._safe_float(self._elem_text(totals, 'total', 'total_amount'))
        else:
            # Try flat
            result.subtotal = self._safe_float(self._elem_text(root, 'subtotal'))
            result.tax_amount = self._safe_float(self._elem_text(root, 'tax_amount', 'tax'))
            result.total_amount = self._safe_float(self._elem_text(root, 'total', 'total_amount'))

        # Payment terms (can be at root level)
        result.payment_terms = self._elem_text(root, 'payment_terms')

        # Warnings
        if not result.invoice_number:
            result.parse_errors.append('Could not extract invoice number from XML')
        if not result.vendor_name:
            result.parse_errors.append('Could not extract vendor name from XML')

    def _extract_line_items(
        self, root: ET.Element, result: ParsedInvoice
    ) -> List[Dict[str, Any]]:
        """Extract line items from ``<line_items>`` or ``<items>`` section."""
        items: List[Dict[str, Any]] = []

        # Try <line_items> first, then <items>
        container = root.find('line_items')
        if container is None:
            container = root.find('items')
        if container is None:
            # Items might be direct children of root
            container = root

        for item_elem in container.findall('item'):
            item_name = self._elem_text(item_elem, 'name', 'item_name', 'description')
            qty = self._safe_int(self._elem_text(item_elem, 'quantity', 'qty'))
            price = self._safe_float(self._elem_text(item_elem, 'unit_price', 'price'))

            # Check for currency attribute on unit_price
            price_elem = item_elem.find('unit_price')
            if price_elem is None:
                price_elem = item_elem.find('price')

            items.append({
                'item_name': item_name or '',
                'quantity': qty,
                'unit_price': price,
            })

        if not items:
            result.parse_errors.append('No line items found in XML')

        return items

    @staticmethod
    def _elem_text(parent: ET.Element, *tag_names: str) -> Optional[str]:
        """Return text content of the first matching child element.

        Args:
            parent: Parent XML element to search.
            *tag_names: Tag names to try, in priority order.

        Returns:
            The text content stripped, or ``None`` if not found / empty.
        """
        for tag in tag_names:
            elem = parent.find(tag)
            if elem is not None and elem.text:
                return elem.text.strip()
        return None
