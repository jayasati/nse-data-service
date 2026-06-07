"""
PDF text extraction (FEATURE_CHECKLIST Phase 5, Week 16, task 16.4).

Takes PDF bytes, returns (text, error). pdfplumber is primary (handles the
text-based PDFs that are the majority of NSE filings); pymupdf (fitz) is the
fallback for edge cases / corrupted files. Scanned PDFs are detected, not OCR'd
in this phase: a multi-page PDF that yields < 100 characters is flagged
`ocr_required` and returns empty text.

    text, error = extract_text(pdf_bytes)
    # error is None on success, else 'ocr_required' / 'extract_failed'
"""

from __future__ import annotations

import io

import structlog

log = structlog.get_logger()

_MIN_CHARS = 100   # below this on a multi-page PDF ⇒ probably scanned


def _via_pdfplumber(data: bytes) -> tuple[str, int]:
    import pdfplumber
    parts, pages = [], 0
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = len(pdf.pages)
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n".join(parts).strip(), pages


def _via_pymupdf(data: bytes) -> tuple[str, int]:
    import fitz
    parts = []
    with fitz.open(stream=data, filetype="pdf") as doc:
        pages = doc.page_count
        for page in doc:
            parts.append(page.get_text() or "")
    return "\n".join(parts).strip(), pages


def extract_text(data: bytes) -> tuple[str, str | None]:
    """Return (text, error). error: None | 'ocr_required' | 'extract_failed'."""
    if not data:
        return "", "extract_failed"

    text, pages = "", 0
    try:
        text, pages = _via_pdfplumber(data)
    except Exception as e:
        log.warning("pdfplumber_failed", error=str(e))
        try:
            text, pages = _via_pymupdf(data)
        except Exception as e2:
            log.warning("pymupdf_failed", error=str(e2))
            return "", "extract_failed"

    # Scanned-PDF heuristic: multi-page but almost no extractable text.
    if pages > 1 and len(text) < _MIN_CHARS:
        return "", "ocr_required"
    return text, None
