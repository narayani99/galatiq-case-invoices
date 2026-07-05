"""
PDF invoice parser for the Galatiq Invoice Processing System.

Strategy:
  1. Use ``pdfplumber`` to extract text from PDF pages.
  2. Delegate the extracted text to :class:`TextParser` for structured
     field extraction.
  3. If ``pdfplumber`` is not installed, log a warning and return an
     empty :class:`ParsedInvoice` with ``raw_text`` set to empty string
     and a descriptive error.

This ensures the system degrades gracefully when running without the
optional ``pdfplumber`` dependency.
"""

import logging
from typing import Optional

from src.parsers.base import BaseParser, ParsedInvoice

logger = logging.getLogger(__name__)

# Attempt import; None signals that the dep is missing.
try:
    import pdfplumber  # type: ignore[import-untyped]
except ImportError:
    pdfplumber = None  # type: ignore[assignment]


class PdfParser(BaseParser):
    """Parser for PDF (.pdf) invoice files.

    Uses ``pdfplumber`` for text extraction and then delegates to
    :class:`~src.parsers.text_parser.TextParser` for structured parsing.
    """

    def parse(self, file_path: str) -> ParsedInvoice:
        """Parse a PDF invoice file.

        Args:
            file_path: Path to the ``.pdf`` file.

        Returns:
            A :class:`ParsedInvoice` with extracted data and any errors.
        """
        result = ParsedInvoice()

        if pdfplumber is None:
            msg = (
                'pdfplumber is not installed — PDF extraction unavailable. '
                'Install with: pip install pdfplumber'
            )
            logger.warning(msg)
            result.parse_errors.append(msg)
            return result

        # Extract text from PDF
        raw_text = self._extract_pdf_text(file_path, result)
        if raw_text is None:
            return result

        result.raw_text = raw_text

        if not raw_text.strip():
            result.parse_errors.append('PDF text extraction returned empty string')
            return result

        # Delegate to TextParser for field extraction
        try:
            from src.parsers.text_parser import TextParser
            text_parser = TextParser()
            text_result = text_parser.parse.__wrapped__(text_parser, file_path) if hasattr(text_parser.parse, '__wrapped__') else None  # noqa: E501

            # We need to run the text parser on the *extracted text*, not the file.
            # Since TextParser.parse reads the file, we use the internal helper.
            text_result = ParsedInvoice(raw_text=raw_text)
            text_parser._extract_fields(raw_text, text_result)

            # Copy extracted fields into our result
            result.invoice_number = text_result.invoice_number
            result.vendor_name = text_result.vendor_name
            result.invoice_date = text_result.invoice_date
            result.due_date = text_result.due_date
            result.total_amount = text_result.total_amount
            result.line_items = text_result.line_items
            result.payment_terms = text_result.payment_terms
            result.subtotal = text_result.subtotal
            result.tax_amount = text_result.tax_amount
            result.parse_errors.extend(text_result.parse_errors)

        except Exception as exc:
            msg = f'Text extraction from PDF succeeded but structured parsing failed: {exc}'
            logger.error(msg, exc_info=True)
            result.parse_errors.append(msg)

        return result

    # ------------------------------------------------------------------
    # PDF text extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_pdf_text(file_path: str, result: ParsedInvoice) -> Optional[str]:
        """Extract text from all pages of a PDF.

        Returns:
            Concatenated text from all pages, or ``None`` on failure.
        """
        try:
            pages_text = []
            with pdfplumber.open(file_path) as pdf:
                for page_num, page in enumerate(pdf.pages, start=1):
                    try:
                        text = page.extract_text() or ''
                        pages_text.append(text)
                    except Exception as exc:
                        msg = f'Failed to extract text from page {page_num}: {exc}'
                        logger.warning(msg)
                        result.parse_errors.append(msg)

            return '\n'.join(pages_text)

        except Exception as exc:
            msg = f'Failed to open PDF {file_path}: {exc}'
            logger.error(msg)
            result.parse_errors.append(msg)
            return None
