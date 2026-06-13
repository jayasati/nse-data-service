"""End-to-end integration test for the parser job.

Builds an in-memory raw_announcements table, seeds it with synthetic rows
pointing at fixture PDFs we control, runs the parser job, and asserts
every row reached a terminal state with correct artifacts.

No live NSE calls.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import fitz
import pytest

from nse_data.parsers.job import run_job
from nse_data.parsers.state import State, TERMINAL_STATES


class StubSession:
    """SessionManager stand-in that serves bytes from a path map."""

    def __init__(self, url_to_path: dict[str, Path]):
        self.url_to_path = url_to_path

    def get_bytes(self, endpoint_name, url, referer=None):
        path = self.url_to_path.get(url)
        if path is None:
            return None
        return path.read_bytes()


def _make_pdf(path: Path, text: str = "Sample text") -> None:
    """Create a PDF with enough real text to clear the classifier's
    'scanned' threshold (avg_chars_per_page > 100).

    Uses insert_textbox with a wrap rect — insert_text at a single point
    doesn't wrap, so long strings render off the page edge and never
    appear in get_text() output.
    """
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # Repeat the input to ensure we have plenty of characters, then wrap
    # inside a generous rectangle that fits on the page.
    content = (text + " ") * 50
    page.insert_textbox(
        fitz.Rect(50, 50, 545, 800),  # leaves margins
        content,
        fontsize=10,
    )
    doc.save(path)
    doc.close()


def _make_scanned_pdf(path: Path) -> None:
    """No text layer."""
    doc = fitz.open()
    doc.new_page(width=595, height=842)
    doc.save(path)
    doc.close()


@pytest.fixture
def seeded_db(tmp_path: Path) -> sqlite3.Connection:
    """Build a fresh DB with the columns Phase 2 needs."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE raw_announcements (
            fingerprint TEXT PRIMARY KEY,
            symbol TEXT,
            subject TEXT,
            attachment_url TEXT,
            broadcast_dt TEXT,
            priority TEXT,
            pdf_path TEXT,
            pdf_text TEXT,
            pdf_status TEXT DEFAULT 'pending',
            retention_policy TEXT,
            deleted_at INTEGER,
            pdf_type TEXT,
            pdf_classified_at INTEGER,
            pdf_size_bytes INTEGER,
            pdf_page_count INTEGER,
            pdf_status_updated_at INTEGER,
            pdf_download_attempts INTEGER DEFAULT 0,
            pdf_error TEXT,
            pdf_sha256 TEXT,
            pdf_text_extracted_at INTEGER,
            pdf_text_length INTEGER
        )
    """)
    conn.commit()
    return conn


def _seed_row(db, fp, subject, url, broadcast_dt="20-May-2026 10:00:00"):
    db.execute(
        """INSERT INTO raw_announcements
           (fingerprint, symbol, subject, attachment_url, broadcast_dt)
           VALUES (?, 'TEST', ?, ?, ?)""",
        (fp, subject, url, broadcast_dt),
    )
    db.commit()


def test_high_priority_pdf_archived_permanently(tmp_path: Path, seeded_db):
    archive_root = tmp_path / "archive"
    pdf = tmp_path / "source.pdf"
    _make_pdf(pdf, "Quarterly results.")

    _seed_row(seeded_db, "fp_high", "Outcome of Board Meeting",
              "http://example.com/a.pdf")

    session = StubSession({"http://example.com/a.pdf": pdf})

    report = run_job(seeded_db, session, archive_root, limit=10)

    assert report.rows_processed == 1
    assert report.by_terminal_state.get(State.TEXT_EXTRACTED) == 1

    row = seeded_db.execute(
        "SELECT * FROM raw_announcements WHERE fingerprint='fp_high'"
    ).fetchone()
    cols = [c[0] for c in seeded_db.execute(
        "SELECT * FROM raw_announcements LIMIT 1"
    ).description]
    d = dict(zip(cols, row))

    assert d["pdf_status"] == State.TEXT_EXTRACTED
    assert d["priority"] == "high"
    assert d["pdf_path"] is not None
    assert "pdfs" in d["pdf_path"] and "pdfs_temp" not in d["pdf_path"]
    assert d["pdf_text"] and "Quarterly results" in d["pdf_text"]
    assert d["pdf_sha256"] is not None
    assert Path(d["pdf_path"]).exists()


def test_skip_subject_no_download(tmp_path: Path, seeded_db):
    archive_root = tmp_path / "archive"
    _seed_row(seeded_db, "fp_skip", "Trading Window",
              "http://example.com/skip.pdf")

    session = StubSession({})  # would fail if called

    report = run_job(seeded_db, session, archive_root, limit=10)

    assert report.by_terminal_state.get(State.SKIPPED_LOW_PRIORITY) == 1

    row = seeded_db.execute(
        "SELECT priority, pdf_status, pdf_path FROM raw_announcements "
        "WHERE fingerprint='fp_skip'"
    ).fetchone()
    assert row[0] == "skip"
    assert row[1] == State.SKIPPED_LOW_PRIORITY
    assert row[2] is None


def test_low_priority_text_kept_pdf_discarded(tmp_path: Path, seeded_db):
    archive_root = tmp_path / "archive"
    pdf = tmp_path / "low.pdf"
    _make_pdf(pdf, "Newspaper publication.")

    _seed_row(seeded_db, "fp_low", "Copy of Newspaper Publication",
              "http://example.com/low.pdf")

    session = StubSession({"http://example.com/low.pdf": pdf})
    run_job(seeded_db, session, archive_root, limit=10)

    row = seeded_db.execute(
        "SELECT priority, pdf_status, pdf_path, pdf_text FROM raw_announcements "
        "WHERE fingerprint='fp_low'"
    ).fetchone()
    assert row[0] == "low"
    assert row[1] == State.TEXT_EXTRACTED
    assert row[2] is None  # PDF discarded
    assert "Newspaper publication" in row[3]


def test_scanned_pdf_goes_to_ocr_required(tmp_path: Path, seeded_db):
    archive_root = tmp_path / "archive"
    pdf = tmp_path / "scanned.pdf"
    _make_scanned_pdf(pdf)

    _seed_row(seeded_db, "fp_scan", "Outcome of Board Meeting",
              "http://example.com/scan.pdf")

    session = StubSession({"http://example.com/scan.pdf": pdf})
    run_job(seeded_db, session, archive_root, limit=10)

    row = seeded_db.execute(
        "SELECT pdf_status, pdf_type FROM raw_announcements "
        "WHERE fingerprint='fp_scan'"
    ).fetchone()
    assert row[0] == State.OCR_REQUIRED
    assert row[1] == "scanned"


def test_download_failure(tmp_path: Path, seeded_db):
    archive_root = tmp_path / "archive"

    _seed_row(seeded_db, "fp_fail", "Outcome of Board Meeting",
              "http://example.com/missing.pdf")

    session = StubSession({})  # returns None for any URL

    run_job(seeded_db, session, archive_root, limit=10)

    row = seeded_db.execute(
        "SELECT pdf_status, pdf_error FROM raw_announcements "
        "WHERE fingerprint='fp_fail'"
    ).fetchone()
    assert row[0] == State.DOWNLOAD_FAILED
    assert "empty" in row[1]


def test_every_row_reaches_terminal_state(tmp_path: Path, seeded_db):
    archive_root = tmp_path / "archive"
    pdf = tmp_path / "ok.pdf"
    _make_pdf(pdf, "Real content here.")

    # Mix of subjects exercising different paths
    seeds = [
        ("a", "Outcome of Board Meeting", "http://example.com/ok.pdf"),
        ("b", "Trading Window", "http://example.com/skip.pdf"),
        ("c", "Investor Presentation", "http://example.com/ok.pdf"),
        ("d", "Copy of Newspaper Publication", "http://example.com/ok.pdf"),
        ("e", "Outcome of Board Meeting", "http://example.com/missing.pdf"),
    ]
    for fp, subj, url in seeds:
        _seed_row(seeded_db, fp, subj, url)

    session = StubSession({"http://example.com/ok.pdf": pdf})

    report = run_job(seeded_db, session, archive_root, limit=10)

    assert report.rows_processed == 5

    rows = seeded_db.execute(
        "SELECT fingerprint, pdf_status FROM raw_announcements"
    ).fetchall()
    for fp, status in rows:
        assert status in TERMINAL_STATES, f"{fp} stuck at {status}"

def _row(db, fp: str) -> dict:
    cur = db.execute("SELECT * FROM raw_announcements WHERE fingerprint = ?", (fp,))
    return {d[0]: v for d, v in zip(cur.description, cur.fetchone())}


def test_off_universe_symbol_skipped_no_download(tmp_path: Path, seeded_db):
    """A symbol outside the focus universe is parked at SKIPPED_OFF_UNIVERSE
    with no PDF download/LLM. `universe` is injected, so the other tests (which
    pass no universe) stay ungated."""
    from nse_data.parsers.job import process_one_row

    _seed_row(seeded_db, "fp_off", "Outcome of Board Meeting",
              "http://example/never-fetched.pdf")   # symbol='TEST', not in universe
    terminal = process_one_row(
        seeded_db, StubSession({}), _row(seeded_db, "fp_off"), tmp_path / "archive",
        universe=frozenset({"HDFCBANK", "RELIANCE"}))
    assert terminal == State.SKIPPED_OFF_UNIVERSE


def test_in_universe_symbol_not_gated(tmp_path: Path, seeded_db):
    """A symbol IN the universe bypasses the gate and follows the normal path."""
    from nse_data.parsers.job import process_one_row

    seeded_db.execute(
        "INSERT INTO raw_announcements (fingerprint, symbol, subject, attachment_url, broadcast_dt) "
        "VALUES ('fp_in', 'RELIANCE', 'Trading Window', 'http://x/none.pdf', '20-May-2026 10:00:00')")
    seeded_db.commit()
    terminal = process_one_row(
        seeded_db, StubSession({}), _row(seeded_db, "fp_in"), tmp_path / "archive",
        universe=frozenset({"RELIANCE"}))
    # 'Trading Window' is skip-priority → gate passed, classified as skip (not off-universe).
    assert terminal == State.SKIPPED_LOW_PRIORITY
