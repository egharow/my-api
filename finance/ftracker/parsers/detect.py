from pathlib import Path

from . import amex, isracard, isracard_pdf, leumi_pdf
from .base import ParseError, ParsedFile


class Unrecognised(Exception):
    pass


def parse_file(path: Path) -> ParsedFile:
    """Pick the right parser from the file's content. Raises Unrecognised with a reason."""
    ext = path.suffix.lower()
    try:
        if ext == ".xlsx":
            rows = isracard.read_rows(path)
            if isracard.looks_like(rows):
                return isracard.parse_rows(rows)
            raise Unrecognised("Excel file is not a known card statement layout")
        if ext == ".xls":
            rows = amex.read_rows(path)
            if amex.looks_like(rows):
                return amex.parse_rows(rows)
            raise Unrecognised("Excel file is not a known card export layout")
        if ext == ".pdf":
            pages = isracard_pdf.read_pages(path)
            if isracard_pdf.looks_like(pages):
                return isracard_pdf.parse_pages(pages)
            pages = leumi_pdf.read_pages(path)
            if leumi_pdf.looks_like(pages):
                return leumi_pdf.parse_pages(pages)
            raise Unrecognised("PDF is not a known statement layout")
    except ParseError as exc:
        raise Unrecognised(f"looked like a known layout but could not be read: {exc}") from exc
    except Unrecognised:
        raise
    except Exception as exc:  # corrupt or unsupported file
        raise Unrecognised(f"could not open file: {exc}") from exc
    raise Unrecognised(f"unsupported file type {ext!r}")
