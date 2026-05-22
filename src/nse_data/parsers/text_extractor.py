"""Extract plain text from a PDF using pymupdf.

Tolerant of corrupt or partial PDFs — extracts what it can, returns an
error message rather than raising. The job orchestrator decides what
status to assign based on whether any text came out.

Phase 2 uses a single strategy: pymupdf's default text extraction. Phase 4
will add layout-aware extraction for presentations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import fitz

LOG = logging.getLogger(__name__)


@dataclass
class TextExtractionResult:
    """Outcome of text extraction on one PDF."""

    success: bool
    text: str = ""
    page_count: int = 0
    text_length: int = 0
    error: Optional[str] = None


def extract_text(pdf_path: Path | str) -> TextExtractionResult:
    """Open a PDF and pull plain text from all pages.

    Returns an error result rather than raising; the orchestrator handles
    status transitions.
    """
    path = Path(pdf_path)
    if not path.exists():
        return TextExtractionResult(
            success=False, error=f"file_not_found: {path}",
        )

    try:
        doc = fitz.open(path)
    except Exception as e:
        return TextExtractionResult(
            success=False, error=f"pymupdf_open_failed: {e!r}",
        )

    parts: list[str] = []
    pages_with_errors = 0
    page_count = doc.page_count

    try:
        for i in range(page_count):
            try:
                page = doc[i]
                text = page.get_text()
                if text:
                    parts.append(text)
            except Exception as e:
                pages_with_errors += 1
                LOG.warning(
                    "page %d of %s extract failed: %r", i, path.name, e,
                )
    finally:
        doc.close()

    text = "\n".join(parts)

    if not text.strip():
        # Document opened but no text came out — likely scanned (caller
        # should already have classified it that way; this is a safety net).
        return TextExtractionResult(
            success=False,
            text="",
            page_count=page_count,
            text_length=0,
            error="no_text_layer",
        )

    return TextExtractionResult(
        success=True,
        text=text,
        page_count=page_count,
        text_length=len(text),
        error=f"{pages_with_errors}_pages_failed" if pages_with_errors else None,
    )