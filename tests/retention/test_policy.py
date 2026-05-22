"""Tests for retention policy decisions."""

from __future__ import annotations

from pathlib import Path

import pytest

from nse_data.retention.policy import decide_retention


@pytest.fixture
def archive_root(tmp_path: Path) -> Path:
    return tmp_path / "archive"


def test_high_priority_archived_permanently(archive_root: Path):
    decision = decide_retention(
        priority="high",
        fingerprint="abc12345",
        broadcast_dt="21-May-2026 19:40:44",
        archive_root=archive_root,
    )
    assert decision.action == "archive_permanent"
    assert decision.archive_path is not None
    assert "pdfs" in str(decision.archive_path)
    assert "2026/05/21" in str(decision.archive_path)
    assert decision.archive_path.name == "abc12345.pdf"
    assert decision.will_write_file()


def test_medium_priority_temp_storage(archive_root: Path):
    decision = decide_retention(
        priority="medium",
        fingerprint="def67890",
        broadcast_dt="15-Apr-2026 10:00:00",
        archive_root=archive_root,
    )
    assert decision.action == "archive_temp_30d"
    assert "pdfs_temp" in str(decision.archive_path)
    assert decision.archive_path.name == "def67890.pdf"
    assert decision.will_write_file()


def test_low_priority_discarded(archive_root: Path):
    decision = decide_retention(
        priority="low",
        fingerprint="ghi13579",
        broadcast_dt="15-Apr-2026 10:00:00",
        archive_root=archive_root,
    )
    assert decision.action == "discard"
    assert decision.archive_path is None
    assert not decision.will_write_file()


def test_skip_priority_no_download(archive_root: Path):
    decision = decide_retention(
        priority="skip",
        fingerprint="jkl24680",
        broadcast_dt="15-Apr-2026 10:00:00",
        archive_root=archive_root,
    )
    assert decision.action == "do_not_download"
    assert decision.archive_path is None


def test_bad_broadcast_dt_uses_fallback(archive_root: Path):
    decision = decide_retention(
        priority="high",
        fingerprint="aaa",
        broadcast_dt="garbage-not-a-date",
        archive_root=archive_root,
    )
    assert decision.action == "archive_permanent"
    assert "unknown_date" in str(decision.archive_path)