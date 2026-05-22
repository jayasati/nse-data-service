"""Tests for the nightly retention cleanup job."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest

from nse_data.retention.cleanup import cleanup_temp_pdfs
from nse_data.parsers.state import State


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    """Create a minimal raw_announcements table for testing."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE raw_announcements (
            fingerprint TEXT PRIMARY KEY,
            pdf_path TEXT,
            pdf_status TEXT,
            pdf_status_updated_at INTEGER,
            deleted_at INTEGER
        )
    """)
    conn.commit()
    return conn


def _make_old_pdf(path: Path, age_days: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF old")
    age_seconds = age_days * 86400
    old_time = time.time() - age_seconds
    os.utime(path, (old_time, old_time))


def test_deletes_files_older_than_retention(tmp_path: Path, db):
    temp_dir = tmp_path / "pdfs_temp"
    old_pdf = temp_dir / "old_fp.pdf"
    new_pdf = temp_dir / "new_fp.pdf"
    _make_old_pdf(old_pdf, age_days=45)
    _make_old_pdf(new_pdf, age_days=5)

    db.execute(
        "INSERT INTO raw_announcements (fingerprint, pdf_path, pdf_status) "
        "VALUES (?, ?, ?)",
        ("old_fp", str(old_pdf), State.TEXT_EXTRACTED),
    )
    db.execute(
        "INSERT INTO raw_announcements (fingerprint, pdf_path, pdf_status) "
        "VALUES (?, ?, ?)",
        ("new_fp", str(new_pdf), State.TEXT_EXTRACTED),
    )
    db.commit()

    report = cleanup_temp_pdfs(db, archive_root=tmp_path, retention_days=30)

    assert report.scanned_files == 2
    assert report.deleted_files == 1
    assert report.rows_updated == 1
    assert not old_pdf.exists()
    assert new_pdf.exists()


def test_updates_db_row_when_deleting(tmp_path: Path, db):
    temp_dir = tmp_path / "pdfs_temp"
    old_pdf = temp_dir / "fp1.pdf"
    _make_old_pdf(old_pdf, age_days=45)

    db.execute(
        "INSERT INTO raw_announcements (fingerprint, pdf_path, pdf_status) "
        "VALUES (?, ?, ?)",
        ("fp1", str(old_pdf), State.TEXT_EXTRACTED),
    )
    db.commit()

    cleanup_temp_pdfs(db, archive_root=tmp_path, retention_days=30)

    row = db.execute(
        "SELECT pdf_path, pdf_status, deleted_at FROM raw_announcements "
        "WHERE fingerprint='fp1'"
    ).fetchone()
    assert row[0] is None  # pdf_path cleared
    assert row[1] == State.DISCARDED
    assert row[2] is not None  # deleted_at set


def test_does_not_demote_extracted_rows(tmp_path: Path, db):
    """A fully-extracted row should keep its EXTRACTED status."""
    temp_dir = tmp_path / "pdfs_temp"
    old_pdf = temp_dir / "fp1.pdf"
    _make_old_pdf(old_pdf, age_days=45)

    db.execute(
        "INSERT INTO raw_announcements (fingerprint, pdf_path, pdf_status) "
        "VALUES (?, ?, ?)",
        ("fp1", str(old_pdf), State.EXTRACTED),
    )
    db.commit()

    cleanup_temp_pdfs(db, archive_root=tmp_path, retention_days=30)

    row = db.execute(
        "SELECT pdf_status FROM raw_announcements WHERE fingerprint='fp1'"
    ).fetchone()
    # Status was EXTRACTED — the WHERE clause excludes these from being
    # demoted to DISCARDED
    assert row[0] == State.EXTRACTED


def test_handles_missing_temp_dir(tmp_path: Path, db):
    report = cleanup_temp_pdfs(db, archive_root=tmp_path, retention_days=30)
    assert report.scanned_files == 0
    assert report.deleted_files == 0