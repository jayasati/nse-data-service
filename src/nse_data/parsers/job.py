"""The parser job — the orchestrator that processes one announcement at a time.

Reads pending rows from raw_announcements, runs each through the full pipeline:
  classify subject -> download PDF -> classify PDF -> extract text ->
  apply retention -> update row.

Designed for both backfill (process N rows now) and scheduled (process
new arrivals since last run).
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from nse_data.parsers.pdf_classifier import classify_pdf
from nse_data.parsers.pdf_downloader import download_pdf
from nse_data.parsers.state import State
from nse_data.parsers.subject_classifier import classify_subject
from nse_data.parsers.text_extractor import extract_text
from nse_data.retention.archive import write_pdf
from nse_data.retention.policy import decide_retention
from nse_data.session.manager import SessionManager
from nse_data.storage import files

LOG = logging.getLogger(__name__)


@dataclass
class JobReport:
    """Summary of one parser job run."""

    started_at: float
    finished_at: float = 0.0
    rows_processed: int = 0
    by_terminal_state: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def add_outcome(self, state: str) -> None:
        self.by_terminal_state[state] = self.by_terminal_state.get(state, 0) + 1

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at or time.time()) - self.started_at


def process_one_row(
    db: sqlite3.Connection,
    session: SessionManager,
    row: dict,
    archive_root: Path,
) -> str:
    """Run the full pipeline against one announcement row.

    Returns the terminal state assigned. Side-effect: updates the row in
    raw_announcements.

    Per-row failures are caught; this function doesn't raise on parse
    or download errors — those are reflected in the terminal state.
    """
    fingerprint = row["fingerprint"]
    subject = row.get("subject") or ""
    attachment_url = row.get("attachment_url") or ""
    broadcast_dt = row.get("broadcast_dt") or ""
    now = int(time.time())

    # ---- Step 1: classify subject -> priority ------------------------------
    priority = classify_subject(subject)

    if priority == "skip":
        _update_row(db, fingerprint, {
            "priority": "skip",
            "pdf_status": State.SKIPPED_LOW_PRIORITY,
            "pdf_status_updated_at": now,
            "retention_policy": "skip",
        })
        return State.SKIPPED_LOW_PRIORITY

    # Mark classified before any I/O so we can resume on crash
    _update_row(db, fingerprint, {
        "priority": priority,
        "pdf_status": State.CLASSIFIED,
        "pdf_status_updated_at": now,
    })

    if not attachment_url:
        _update_row(db, fingerprint, {
            "pdf_status": State.DOWNLOAD_FAILED,
            "pdf_status_updated_at": int(time.time()),
            "pdf_error": "no_attachment_url",
        })
        return State.DOWNLOAD_FAILED

    # ---- Step 2: download PDF ---------------------------------------------
    _update_row(db, fingerprint, {
        "pdf_status": State.DOWNLOADING,
        "pdf_download_attempts": (row.get("pdf_download_attempts") or 0) + 1,
        "pdf_status_updated_at": int(time.time()),
    })

    dl = download_pdf(session, attachment_url)

    if not dl.success:
        _update_row(db, fingerprint, {
            "pdf_status": State.DOWNLOAD_FAILED,
            "pdf_status_updated_at": int(time.time()),
            "pdf_error": dl.error,
            "pdf_size_bytes": dl.size_bytes or None,
        })
        return State.DOWNLOAD_FAILED

    # ---- Step 3: apply retention decision (where would the PDF live?) ----
    decision = decide_retention(
        priority=priority,
        fingerprint=fingerprint,
        broadcast_dt=broadcast_dt,
        archive_root=archive_root,
    )

    # ---- Step 4: write PDF to a temp working location ---------------------
    # We need the file on disk for classification + text extraction.
    # If retention says "discard", we use a temp work dir; otherwise
    # we write directly to the archive location.
    if decision.will_write_file():
        try:
            work_path = write_pdf(decision, dl.data)
        except OSError as e:
            _update_row(db, fingerprint, {
                "pdf_status": State.DOWNLOAD_FAILED,
                "pdf_status_updated_at": int(time.time()),
                "pdf_error": f"archive_write_failed: {e!r}",
            })
            return State.DOWNLOAD_FAILED
    else:
        # 'discard' path — write to scratch, process, delete
        work_path = files.scratch_path(archive_root, fingerprint)
        work_path.parent.mkdir(parents=True, exist_ok=True)
        work_path.write_bytes(dl.data)

    # ---- Step 5: classify PDF type ----------------------------------------
    try:
        classification = classify_pdf(work_path)
    except Exception as e:
        _cleanup_scratch(work_path, decision)
        _update_row(db, fingerprint, {
            "pdf_status": State.TEXT_EXTRACTION_FAILED,
            "pdf_status_updated_at": int(time.time()),
            "pdf_error": f"classify_failed: {e!r}",
            "pdf_sha256": dl.sha256,
            "pdf_size_bytes": dl.size_bytes,
        })
        return State.TEXT_EXTRACTION_FAILED

    pdf_type = classification.pdf_type

    # ---- Step 6: handle scanned PDFs (defer to OCR phase) -----------------
    if pdf_type == "scanned":
        # Keep the PDF if retention says so (it'll be processed in Phase 4
        # when OCR exists). Don't extract text — there isn't any.
        final_path = decision.archive_path if decision.will_write_file() else None
        _cleanup_scratch(work_path, decision)
        _update_row(db, fingerprint, {
            "pdf_status": State.OCR_REQUIRED,
            "pdf_status_updated_at": int(time.time()),
            "pdf_type": pdf_type,
            "pdf_classified_at": classification.classified_at,
            "pdf_size_bytes": dl.size_bytes,
            "pdf_page_count": classification.page_count,
            "pdf_sha256": dl.sha256,
            "pdf_path": str(final_path) if final_path else None,
            "retention_policy": decision.retention_policy_label,
        })
        return State.OCR_REQUIRED

    # ---- Step 7: extract text ---------------------------------------------
    extraction = extract_text(work_path)

    if not extraction.success:
        _cleanup_scratch(work_path, decision)
        _update_row(db, fingerprint, {
            "pdf_status": State.TEXT_EXTRACTION_FAILED,
            "pdf_status_updated_at": int(time.time()),
            "pdf_type": pdf_type,
            "pdf_classified_at": classification.classified_at,
            "pdf_size_bytes": dl.size_bytes,
            "pdf_page_count": classification.page_count,
            "pdf_sha256": dl.sha256,
            "pdf_path": (
                str(decision.archive_path)
                if decision.will_write_file() else None
            ),
            "pdf_error": extraction.error,
            "retention_policy": decision.retention_policy_label,
        })
        return State.TEXT_EXTRACTION_FAILED

    # ---- Step 8: clean up scratch if PDF is being discarded ---------------
    final_pdf_path = None
    if decision.will_write_file():
        final_pdf_path = str(decision.archive_path)
    else:
        # Discard: delete the scratch file, text is already extracted
        try:
            work_path.unlink()
        except OSError as e:
            LOG.warning("scratch unlink failed for %s: %s", work_path, e)

    # ---- Step 9: success — write final state ------------------------------
    _update_row(db, fingerprint, {
        "pdf_status": State.TEXT_EXTRACTED,
        "pdf_status_updated_at": int(time.time()),
        "pdf_type": pdf_type,
        "pdf_classified_at": classification.classified_at,
        "pdf_size_bytes": dl.size_bytes,
        "pdf_page_count": classification.page_count,
        "pdf_sha256": dl.sha256,
        "pdf_path": final_pdf_path,
        "pdf_text": extraction.text,
        "pdf_text_extracted_at": int(time.time()),
        "pdf_text_length": extraction.text_length,
        "retention_policy": decision.retention_policy_label,
        "pdf_error": extraction.error,  # may be set to "N_pages_failed" warning
    })
    return State.TEXT_EXTRACTED


def _cleanup_scratch(work_path: Path, decision) -> None:
    """If the PDF was in a scratch location (not archived), delete it."""
    if decision.will_write_file():
        return  # file is in its final archive location, keep it
    try:
        if work_path.exists():
            work_path.unlink()
    except OSError:
        pass


def _update_row(
    db: sqlite3.Connection, fingerprint: str, fields: dict
) -> None:
    """Update one row in raw_announcements with the given field values."""
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [fingerprint]
    db.execute(
        f"UPDATE raw_announcements SET {set_clause} WHERE fingerprint = ?",
        values,
    )
    db.commit()


def run_job(
    db: sqlite3.Connection,
    session: SessionManager,
    archive_root: Path,
    fingerprints: Optional[list[str]] = None,
    limit: Optional[int] = None,
) -> JobReport:
    """..."""
    report = JobReport(started_at=time.time())

    rows = _fetch_rows_to_process(db, fingerprints, limit)
    total = len(rows)
    LOG.info("parser job starting: %d rows to process", total)

    for idx, row in enumerate(rows, start=1):
        symbol = row.get("symbol", "?")
        subject = (row.get("subject") or "?")[:50]
        fp = row.get("fingerprint", "?")

        # Per-row start line. Flush immediately so it shows up before the
        # downloader call (which takes 2-5s and would otherwise buffer).
        print(
            f"[{idx:>4}/{total}] {symbol:<14} {subject:<52} ",
            end="", flush=True,
        )

        row_started = time.time()
        try:
            terminal = process_one_row(db, session, row, archive_root)
            report.add_outcome(terminal)
            report.rows_processed += 1
            elapsed = time.time() - row_started
            # Color-ish status indicator on the same line
            marker = _status_marker(terminal)
            print(f"{marker} {terminal:<22} ({elapsed:>4.1f}s)", flush=True)
        except Exception as e:
            err = f"unhandled error on {fp}: {e!r}"
            LOG.exception(err)
            report.errors.append(err)
            elapsed = time.time() - row_started
            print(f"!! ERROR {e!r:<30} ({elapsed:>4.1f}s)", flush=True)

    report.finished_at = time.time()
    LOG.info(
        "parser job done: %d processed in %.1fs; states=%s",
        report.rows_processed, report.duration_seconds,
        dict(report.by_terminal_state),
    )
    return report


def _status_marker(state: str) -> str:
    """One-char visual marker for terminal states."""
    happy_states = {"text_extracted", "skipped_low_priority"}
    soft_fail_states = {"ocr_required"}
    if state in happy_states:
        return "OK"
    if state in soft_fail_states:
        return "--"
    # Anything else is a failure
    return "XX"
def _fetch_rows_to_process(
    db: sqlite3.Connection,
    fingerprints: Optional[list[str]],
    limit: Optional[int],
) -> list[dict]:
    """Pull rows from raw_announcements that need processing."""
    db.row_factory = sqlite3.Row

    if fingerprints:
        placeholders = ",".join("?" * len(fingerprints))
        rows = db.execute(
            f"SELECT * FROM raw_announcements WHERE fingerprint IN ({placeholders})",
            fingerprints,
        ).fetchall()
    else:
        if limit is None:
            raise ValueError("Either fingerprints or limit must be provided")
        rows = db.execute(
            """
            SELECT * FROM raw_announcements
             WHERE pdf_status = ?
             ORDER BY broadcast_dt DESC
             LIMIT ?
            """,
            (State.PENDING, limit),
        ).fetchall()

    return [dict(r) for r in rows]