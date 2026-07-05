"""
Base parser module for the Galatiq Invoice Processing System.

Defines the abstract base class and shared data structures used by all
format-specific parsers (TXT, JSON, CSV, XML, PDF).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
import logging

logger = logging.getLogger(__name__)


@dataclass
class ParsedInvoice:
    """Structured representation of a parsed invoice.

    All fields are optional to allow partial extraction when parsing
    encounters errors or ambiguous data.  The ``parse_errors`` list
    captures any issues encountered during parsing so that downstream
    consumers (e.g. the LLM ingestion agent) can decide how to handle
    them.

    Attributes:
        invoice_number: The invoice identifier (e.g. "INV-1001").
        vendor_name: Name of the vendor / supplier.
        invoice_date: Date the invoice was issued (string, various formats).
        due_date: Payment due date (string, various formats).
        total_amount: Total amount on the invoice.
        line_items: List of line-item dicts, each containing at minimum
            ``item_name``, ``quantity``, and ``unit_price``.
        raw_text: The complete raw text of the source document.
        parse_errors: Human-readable descriptions of parsing issues.
        currency: Currency code if specified (e.g. "USD", "EUR").
        payment_terms: Payment terms string (e.g. "Net 30").
        subtotal: Subtotal before tax / shipping.
        tax_amount: Tax amount if present.
    """

    invoice_number: Optional[str] = None
    vendor_name: Optional[str] = None
    invoice_date: Optional[str] = None
    due_date: Optional[str] = None
    total_amount: Optional[float] = None
    line_items: List[Dict[str, Any]] = field(default_factory=list)
    raw_text: str = ''
    parse_errors: List[str] = field(default_factory=list)
    currency: Optional[str] = None
    payment_terms: Optional[str] = None
    subtotal: Optional[float] = None
    tax_amount: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a plain dictionary for serialisation."""
        return {
            'invoice_number': self.invoice_number,
            'vendor_name': self.vendor_name,
            'invoice_date': self.invoice_date,
            'due_date': self.due_date,
            'total_amount': self.total_amount,
            'line_items': self.line_items,
            'raw_text': self.raw_text,
            'parse_errors': self.parse_errors,
            'currency': self.currency,
            'payment_terms': self.payment_terms,
            'subtotal': self.subtotal,
            'tax_amount': self.tax_amount,
        }

    @property
    def has_errors(self) -> bool:
        """Return True if any parse errors were recorded."""
        return len(self.parse_errors) > 0

    @property
    def is_complete(self) -> bool:
        """Return True if the core fields were successfully extracted."""
        return all([
            self.invoice_number,
            self.vendor_name,
            self.total_amount is not None,
            len(self.line_items) > 0,
        ])


class BaseParser(ABC):
    """Abstract base class for invoice parsers.

    Every format-specific parser must implement ``parse`` which reads the
    file at *file_path* and returns a :class:`ParsedInvoice`.  Parsers
    MUST NOT raise — they should catch all exceptions internally, log
    them, and return partial results with ``parse_errors`` populated.
    """

    @abstractmethod
    def parse(self, file_path: str) -> ParsedInvoice:
        """Parse the invoice file and return structured data.

        Args:
            file_path: Absolute or relative path to the invoice file.

        Returns:
            A ``ParsedInvoice`` instance (possibly with partial data and
            errors recorded in ``parse_errors``).
        """
        ...

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        """Attempt to coerce *value* to a float, returning None on failure.

        Handles common formatting like ``$1,000.00`` or ``1000``.
        """
        if value is None:
            return None
        try:
            if isinstance(value, (int, float)):
                return float(value)
            cleaned = str(value).replace('$', '').replace(',', '').strip()
            if not cleaned:
                return None
            return float(cleaned)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _safe_int(value: Any) -> Optional[int]:
        """Attempt to coerce *value* to an int, returning None on failure."""
        if value is None:
            return None
        try:
            return int(float(str(value).strip()))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _read_file_text(file_path: str) -> str:
        """Read entire file as UTF-8 text, falling back to latin-1."""
        try:
            with open(file_path, 'r', encoding='utf-8') as fh:
                return fh.read()
        except UnicodeDecodeError:
            with open(file_path, 'r', encoding='latin-1') as fh:
                return fh.read()
