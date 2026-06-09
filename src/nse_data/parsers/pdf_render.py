"""Render PDF pages to PNG images for the vision extractor (Week 17).

The vision-first financial extractor reads the P&L straight from page images,
so it needs the PDF rasterized. PyMuPDF (``fitz``) does the rendering. We render
selected pages (the ones the text pre-pass flagged as containing the P&L) rather
than the whole document, to keep image-token cost down.

    pngs = render_pages(pdf_bytes, page_indices=[2, 3])   # specific pages
    pngs = render_pages(pdf_bytes)                         # first MAX_PAGES pages
"""
from __future__ import annotations

import structlog

log = structlog.get_logger()

# Legible numbers without huge images. 144 DPI (2x) is enough for gpt-4o to read
# typical NSE result-table digits.
RENDER_DPI = 144
# Hard cap so a pathological image-only annual report can't blow up the token
# bill or latency.
MAX_PAGES = 20


def render_pages(
    data: bytes,
    page_indices: list[int] | None = None,
    *,
    dpi: int = RENDER_DPI,
    max_pages: int = MAX_PAGES,
) -> list[bytes]:
    """Render pages to PNG bytes.

    ``page_indices`` selects specific 0-based pages (clamped to the document and
    de-duplicated, order preserved). When ``None``, renders the first
    ``max_pages`` pages. Returns ``[]`` on any failure (missing fitz, corrupt
    PDF) so callers degrade gracefully.
    """
    try:
        import fitz
    except Exception as e:  # noqa: BLE001
        log.warning("render_no_fitz", error=str(e))
        return []

    out: list[bytes] = []
    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            n = doc.page_count
            if page_indices is None:
                indices = list(range(min(n, max_pages)))
            else:
                seen: set[int] = set()
                indices = []
                for i in page_indices:
                    if 0 <= i < n and i not in seen:
                        seen.add(i)
                        indices.append(i)
                    if len(indices) >= max_pages:
                        break
            zoom = dpi / 72.0
            mat = fitz.Matrix(zoom, zoom)
            for i in indices:
                pix = doc[i].get_pixmap(matrix=mat)
                out.append(pix.tobytes("png"))
    except Exception as e:  # noqa: BLE001
        log.warning("render_failed", error=str(e))
        return []
    return out
