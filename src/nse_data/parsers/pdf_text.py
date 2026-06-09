"""PDF text extraction (Week 17 vision-first rewrite).

Two jobs:
  * ``extract_text(bytes) -> (text, error)`` — the whole-document text layer,
    used by labeling scripts and the units/anchor pre-pass. pdfplumber is
    primary, pymupdf (fitz) the fallback. A multi-page PDF yielding < 100 chars
    is treated as scanned and flagged ``ocr_required`` (the vision path handles
    those from page images instead).
  * ``page_texts(bytes) -> list[str]`` — text per page, so the orchestrator can
    *locate* the P&L page cheaply (by anchor labels) and render only those pages
    for the vision model.

    text, error = extract_text(pdf_bytes)   # error: None | 'ocr_required' | 'extract_failed'
    pages = page_texts(pdf_bytes)           # ["page 1 text", "page 2 text", ...]
"""
from __future__ import annotations

import io

import structlog

log = structlog.get_logger()

_MIN_CHARS = 100   # below this on a multi-page PDF ⇒ probably scanned


def _pages_via_pdfplumber(data: bytes) -> list[str]:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return [(page.extract_text() or "") for page in pdf.pages]


def _pages_via_pymupdf(data: bytes) -> list[str]:
    import fitz

    with fitz.open(stream=data, filetype="pdf") as doc:
        return [(page.get_text() or "") for page in doc]


def page_texts(data: bytes) -> list[str]:
    """Per-page text. Empty list on failure. pdfplumber primary, fitz fallback."""
    if not data:
        return []
    try:
        return _pages_via_pdfplumber(data)
    except Exception as e:  # noqa: BLE001
        log.warning("pdfplumber_pages_failed", error=str(e))
    try:
        return _pages_via_pymupdf(data)
    except Exception as e:  # noqa: BLE001
        log.warning("pymupdf_pages_failed", error=str(e))
        return []


def extract_text(data: bytes) -> tuple[str, str | None]:
    """Return (text, error). error: None | 'ocr_required' | 'extract_failed'."""
    if not data:
        return "", "extract_failed"

    pages = page_texts(data)
    if not pages:
        return "", "extract_failed"

    text = "\n".join(pages).strip()
    # Scanned-PDF heuristic: multi-page but almost no extractable text.
    if len(pages) > 1 and len(text) < _MIN_CHARS:
        return "", "ocr_required"
    return text, None
