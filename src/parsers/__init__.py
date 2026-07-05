"""
Parser factory for the Galatiq Invoice Processing System.

Provides:
  - ``get_parser(file_path)`` — returns the appropriate parser based on
    file extension, with content-sniffing fallback.
  - ``parse_invoice_file(file_path)`` — convenience function that parses
    the file and returns ``(parsed_dict | None, raw_text)``.

Supported formats: ``.txt``, ``.json``, ``.csv``, ``.xml``, ``.pdf``
"""

import logging
import os
from typing import Optional, Tuple, Dict, Any

from src.parsers.base import BaseParser, ParsedInvoice
from src.parsers.text_parser import TextParser
from src.parsers.json_parser import JsonParser
from src.parsers.csv_parser import CsvParser
from src.parsers.xml_parser import XmlParser
from src.parsers.pdf_parser import PdfParser

logger = logging.getLogger(__name__)

__all__ = [
    'get_parser',
    'parse_invoice_file',
    'BaseParser',
    'ParsedInvoice',
    'TextParser',
    'JsonParser',
    'CsvParser',
    'XmlParser',
    'PdfParser',
]

# Extension → parser class mapping
_EXTENSION_MAP: Dict[str, type] = {
    '.txt': TextParser,
    '.json': JsonParser,
    '.csv': CsvParser,
    '.xml': XmlParser,
    '.pdf': PdfParser,
}


def get_parser(file_path: str) -> BaseParser:
    """Return the appropriate parser for the given file.

    Detection strategy:
      1. Match by file extension (``.txt``, ``.json``, etc.).
      2. If the extension is unrecognised, attempt content sniffing.
      3. Default to :class:`TextParser` as a last resort.

    Args:
        file_path: Path to the invoice file.

    Returns:
        An instance of a :class:`BaseParser` subclass.
    """
    ext = os.path.splitext(file_path)[1].lower()

    parser_cls = _EXTENSION_MAP.get(ext)
    if parser_cls is not None:
        logger.debug('Selected %s for extension "%s"', parser_cls.__name__, ext)
        return parser_cls()

    # ------------------------------------------------------------------
    # Content-sniffing fallback for unknown / missing extensions
    # ------------------------------------------------------------------
    logger.info(
        'Unknown extension "%s" for file %s — attempting content sniffing',
        ext, file_path,
    )
    parser_cls = _sniff_format(file_path)
    logger.debug('Content sniffing selected %s', parser_cls.__name__)
    return parser_cls()


def parse_invoice_file(
    file_path: str,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Convenience wrapper: parse an invoice file and return results.

    Args:
        file_path: Path to the invoice file.

    Returns:
        A tuple ``(parsed_data, raw_text)`` where *parsed_data* is a
        dict representation of :class:`ParsedInvoice` (or ``None`` if
        parsing failed completely), and *raw_text* is the original file
        content for LLM fallback.
    """
    try:
        parser = get_parser(file_path)
        result = parser.parse(file_path)

        if result.parse_errors:
            logger.warning(
                'Parse warnings for %s: %s', file_path, result.parse_errors
            )

        return result.to_dict(), result.raw_text

    except Exception as exc:
        logger.error('Fatal error parsing %s: %s', file_path, exc, exc_info=True)
        # Still try to return raw text
        raw_text = ''
        try:
            with open(file_path, 'r', encoding='utf-8') as fh:
                raw_text = fh.read()
        except Exception:
            pass
        return None, raw_text


# ------------------------------------------------------------------
# Content sniffing
# ------------------------------------------------------------------

def _sniff_format(file_path: str) -> type:
    """Inspect file contents to determine the most likely format.

    Returns the parser *class* (not an instance).
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as fh:
            head = fh.read(1024)
    except Exception:
        return TextParser  # safe default

    stripped = head.lstrip()

    # JSON: starts with { or [
    if stripped.startswith('{') or stripped.startswith('['):
        return JsonParser

    # XML: starts with < (and likely <?xml or <invoice)
    if stripped.startswith('<'):
        return XmlParser

    # CSV: first line looks like comma-separated values with a header
    first_line = stripped.split('\n', 1)[0]
    if ',' in first_line and first_line.count(',') >= 1:
        # Heuristic: if the first line has 2+ commas, treat as CSV
        if first_line.count(',') >= 2:
            return CsvParser
        # 2-column key-value CSV also qualifies
        parts = first_line.split(',')
        if len(parts) == 2 and parts[0].strip().lower() in ('field', 'key'):
            return CsvParser

    # Default: text
    return TextParser
