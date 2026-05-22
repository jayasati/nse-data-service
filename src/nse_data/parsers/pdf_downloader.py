"""Download a PDF from NSE given an attachment URL.

Uses Layer 1's SessionManager. Robust against the NSE-isms we know about:
  - HTML error pages served with .pdf extension
  - Empty bodies for missing/deleted attachments
  - Oversized PDFs (banking annual reports can be 100MB+)
  - Redirects to login or interstitial pages

Returns (data, sha256, error). On failure, data is None and error explains.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Optional

from nse_data.session.manager import SessionManager

LOG = logging.getLogger(__name__)

MAX_PDF_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB hard cap
PDF_MAGIC_BYTES = b"%PDF"
NSE_REFERER = (
    "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
)


@dataclass
class DownloadResult:
    """Outcome of one PDF download attempt."""

    success: bool
    data: Optional[bytes] = None
    sha256: Optional[str] = None
    size_bytes: int = 0
    error: Optional[str] = None


def download_pdf(
    session: SessionManager,
    url: str,
    endpoint_name: str = "layer3_pdf_download",
) -> DownloadResult:
    """Fetch a PDF and validate it.

    Args:
        session: The shared SessionManager instance.
        url: Absolute URL to the PDF (typically nsearchives.nseindia.com).
        endpoint_name: For circuit breaker accounting. All Layer 3 downloads
                       can share the same name since they go to one host.

    Returns:
        DownloadResult with success/failure and reason.
    """
    if not url or not url.strip():
        return DownloadResult(success=False, error="empty_url")

    try:
        data = session.get_bytes(
            endpoint_name=endpoint_name,
            url=url,
            referer=NSE_REFERER,
        )
    except Exception as e:
        # SessionManager already handles retries; if it raised, we're done.
        return DownloadResult(success=False, error=f"fetch_failed: {e!r}")

    if data is None or len(data) == 0:
        return DownloadResult(success=False, error="empty_response")

    size = len(data)
    if size > MAX_PDF_SIZE_BYTES:
        return DownloadResult(
            success=False,
            error=f"too_large: {size / 1024 / 1024:.1f}MB",
            size_bytes=size,
        )

    if not data.startswith(PDF_MAGIC_BYTES):
        # NSE sometimes serves HTML error pages with .pdf extension
        head = data[:200].decode("latin-1", errors="replace")
        return DownloadResult(
            success=False,
            error=f"not_a_pdf: starts with {head[:50]!r}",
            size_bytes=size,
        )

    sha256 = hashlib.sha256(data).hexdigest()

    return DownloadResult(
        success=True,
        data=data,
        sha256=sha256,
        size_bytes=size,
    )