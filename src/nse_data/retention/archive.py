"""Move PDF bytes to their final archive location.

Atomic writes — write to a .tmp file then rename. Guarantees no partial
PDFs sit on disk in archived/ if the process is killed mid-write.

The 'discard' decision is also handled here as a no-op; the caller doesn't
need to know whether the file was kept.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from nse_data.retention.policy import RetentionDecision

LOG = logging.getLogger(__name__)


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

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")

    try:
        tmp.write_bytes(data)
        # Atomic rename — POSIX guarantees this on the same filesystem
        os.replace(tmp, target)
    except OSError:
        # Clean up partial write
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                LOG.warning("failed to clean up partial write at %s", tmp)
        raise

    return target