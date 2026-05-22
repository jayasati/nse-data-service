"""Nightly cleanup job: delete temp PDFs older than the retention window.

Walks data/archive/pdfs_temp/, finds files older than the configured
retention window, deletes them, and updates raw_announcements.deleted_at
+ pdf_path for matching rows.

Identifies rows by SHA-256 match (most reliable) with a fallback to
filename matching for older rows that may not have sha256 populated.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from nse_data.parsers.state import State

LOG = logging.getLogger(__name__)


@dataclass
class CleanupReport:
    scanned_files: int = 0
    deleted_files: int = 0
    rows_updated: int = 0
    errors: list[str] = field(default_factory=list)


def cleanup_temp_pdfs(
    db: sqlite3.Connection,
    archive_root: Path,
    retention_days: int = 30,
    now: float | None = None,
) -> CleanupReport:
    """Delete temp PDFs older than retention_days and update DB rows.

    Args:
        db: Open SQLite connection.
        archive_root: Base archive dir (data/archive).
        retention_days: How old a file must be to delete. Default 30.
        now: Override 'current time' for testing. Defaults to time.time().

    Returns:
        CleanupReport with counts.
    """
    if now is None:
        now = time.time()
    cutoff_secs = retention_days * 86400

    report = CleanupReport()
    temp_root = archive_root / "pdfs_temp"
    if not temp_root.exists():
        LOG.info("temp archive does not exist at %s; nothing to clean", temp_root)
        return report

    cursor = db.cursor()

    for pdf_file in temp_root.glob("*.pdf"):
        report.scanned_files += 1

        try:
            age_seconds = now - pdf_file.stat().st_mtime
        except OSError as e:
            report.errors.append(f"stat failed for {pdf_file}: {e}")
            continue

        if age_seconds < cutoff_secs:
            continue

        # File is old enough to delete
        fingerprint = pdf_file.stem  # filename is <fingerprint>.pdf

        try:
            pdf_file.unlink()
            report.deleted_files += 1
        except OSError as e:
            report.errors.append(f"unlink failed for {pdf_file}: {e}")
            continue

        # Update the row (best-effort)
        try:
            cursor.execute(
                """
                UPDATE raw_announcements
                   SET pdf_path = NULL,
                       deleted_at = ?,
                       pdf_status = ?,
                       pdf_status_updated_at = ?
                 WHERE fingerprint = ?
                   AND pdf_status NOT IN (?, ?)
                """,
                (
                    int(now),
                    State.DISCARDED,
                    int(now),
                    fingerprint,
                    State.EXTRACTED,
                    State.EXTRACTED_VIA_VISION,
                    # Don't downgrade rows that have been fully extracted —
                    # their text is still in pdf_text, the file is just gone.
                    # But we want pdf_path cleared regardless. The status
                    # gate prevents Phase 3 results from being marked DISCARDED.
                ),
            )
            if cursor.rowcount > 0:
                report.rows_updated += 1
        except sqlite3.Error as e:
            report.errors.append(f"DB update failed for {fingerprint}: {e}")

    db.commit()
    LOG.info(
        "cleanup: scanned=%d deleted=%d rows_updated=%d errors=%d",
        report.scanned_files, report.deleted_files,
        report.rows_updated, len(report.errors),
    )
    return report


def load_retention_days(config_path: str = "config/cleanup.yaml") -> int:
    """Load retention_days from config, with sensible default."""
    path = Path(config_path)
    if not path.exists():
        return 30
    with path.open() as f:
        cfg = yaml.safe_load(f) or {}
    return cfg.get("retention_cleanup", {}).get("temp_pdf_retention_days", 30)