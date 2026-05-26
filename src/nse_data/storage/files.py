"""PDF archive file-layout helpers — the single source of truth for *where*
a PDF lives on disk and *how* its path is derived from a fingerprint + date.

Retention policy (``retention/policy.py``) decides *whether* to keep a PDF and
under which tier; this module decides the actual on-disk path and owns the
atomic-write mechanic. Keeping the layout here means the date-bucketing scheme,
the temp/scratch subdir names, and the write-then-rename dance live in one
place instead of being re-derived in policy.py, archive.py, cleanup.py and the
parser job.

Three-tier layout under ``<archive_root>/``:

    pdfs/<YYYY>/<MM>/<DD>/<fingerprint>.pdf   high   — kept forever, date-bucketed
    pdfs_temp/<fingerprint>.pdf               medium — flat, cleaned after 30d
    scratch/<fingerprint>.pdf                 low    — parsed then deleted

The subdir names are also configured in ``config/retention.yaml`` (the policy
layer reads them from there); the constants below are the canonical reference
and the default used by helpers that don't take a config-supplied subdir.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

LOG = logging.getLogger(__name__)

# Canonical tier subdir names. Mirror config/retention.yaml's archive_subdir.
PERMANENT_SUBDIR = "pdfs"
TEMP_SUBDIR = "pdfs_temp"
SCRATCH_SUBDIR = "scratch"

# Path component used when a broadcast_dt can't be parsed into a date. Keeps
# unbucketable files out of the dated tree rather than guessing a date.
UNKNOWN_DATE_DIR = "unknown_date"


def pdf_filename(fingerprint: str) -> str:
    """The on-disk filename for a PDF, keyed by announcement fingerprint."""
    return f"{fingerprint}.pdf"


def fingerprint_from_path(path: Path) -> str:
    """Inverse of :func:`pdf_filename` — recover the fingerprint from a path.

    Filenames are ``<fingerprint>.pdf``; cleanup matches DB rows on the stem.
    """
    return path.stem


def date_subpath(broadcast_dt: str) -> Path:
    """Turn an NSE ``broadcast_dt`` into a ``YYYY/MM/DD`` subpath.

    ``broadcast_dt`` looks like ``'21-May-2026 19:40:44'`` — only the date part
    matters. Unparseable values fall back to :data:`UNKNOWN_DATE_DIR` so a bad
    timestamp never raises mid-archive; the file is still kept, just not dated.
    """
    try:
        dt = datetime.strptime(broadcast_dt.split()[0], "%d-%b-%Y")
        return Path(dt.strftime("%Y")) / dt.strftime("%m") / dt.strftime("%d")
    except (ValueError, IndexError, AttributeError):
        return Path(UNKNOWN_DATE_DIR)


def permanent_path(
    archive_root: Path,
    fingerprint: str,
    broadcast_dt: str,
    subdir: str = PERMANENT_SUBDIR,
) -> Path:
    """Date-bucketed path for a forever-kept (high-priority) PDF."""
    return (
        archive_root
        / subdir
        / date_subpath(broadcast_dt)
        / pdf_filename(fingerprint)
    )


def temp_path(
    archive_root: Path,
    fingerprint: str,
    subdir: str = TEMP_SUBDIR,
) -> Path:
    """Flat path for a time-limited (medium-priority) PDF. Cleaned by cleanup.py."""
    return archive_root / subdir / pdf_filename(fingerprint)


def scratch_path(archive_root: Path, fingerprint: str) -> Path:
    """Working path for a discard-tier (low-priority) PDF.

    The file is written here only so classification + text extraction can read
    it off disk; the caller deletes it once the text is persisted.
    """
    return archive_root / SCRATCH_SUBDIR / pdf_filename(fingerprint)


def temp_root(archive_root: Path) -> Path:
    """The directory cleanup.py walks for expired medium-priority PDFs."""
    return archive_root / TEMP_SUBDIR


def atomic_write_bytes(target: Path, data: bytes) -> Path:
    """Write ``data`` to ``target`` atomically: write a ``.tmp`` then rename.

    Guarantees no partial PDF is ever observable at ``target`` — a reader sees
    either the old file or the complete new one, never a half-written blob.
    Creates the parent directory if needed.

    Raises OSError on filesystem errors (after cleaning up the temp file);
    callers treat this as a soft failure — the row isn't poisoned, the PDF
    just isn't archived.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        tmp.write_bytes(data)
        # os.replace is atomic within a filesystem (POSIX rename guarantee).
        os.replace(tmp, target)
    except OSError:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                LOG.warning("failed to clean up partial write at %s", tmp)
        raise
    return target
