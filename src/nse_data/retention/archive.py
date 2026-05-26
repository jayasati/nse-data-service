"""Move PDF bytes to their final archive location.

Applies a retention decision: writes the PDF atomically (via
``storage.files.atomic_write_bytes``) when policy says to keep it, and is a
no-op for the 'discard' decision so the caller doesn't need to know whether
the file was kept.
"""

from __future__ import annotations

from pathlib import Path

from nse_data.retention.policy import RetentionDecision
from nse_data.storage import files


def write_pdf(decision: RetentionDecision, data: bytes) -> Path | None:
    """Apply a retention decision: write the PDF if policy says to.

    Returns the final path on disk, or None if the PDF wasn't kept.

    Raises OSError on filesystem errors — callers should catch and treat
    as a soft failure (write error doesn't poison the row, but the PDF
    can't be archived).
    """
    if not decision.will_write_file():
        return None

    target = decision.archive_path
    if target is None:
        # Shouldn't happen if will_write_file() returned True, but guard anyway
        return None

    return files.atomic_write_bytes(target, data)