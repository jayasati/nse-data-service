"""Tests for retention policy decisions."""

from __future__ import annotations

from pathlib import Path

import pytest

from nse_data.retention.policy import decide_retention


@pytest.fixture
def archive_root(tmp_path: Path) -> Path:
    return tmp_path / "archive"


# 2026-06-18: retention config set to NEVER persist PDFs — every parsed bucket
# discards the file after text extraction. (The archive_permanent / archive_temp_30d
# branches remain in policy.py and are config-reversible, but no longer used.)
def test_high_priority_discarded(archive_root: Path):
    decision = decide_retention(
        priority="high",
        fingerprint="abc12345",
        broadcast_dt="21-May-2026 19:40:44",
        archive_root=archive_root,
    )
    assert decision.action == "discard"
    assert decision.archive_path is None
    assert not decision.will_write_file()


def test_medium_priority_discarded(archive_root: Path):
    decision = decide_retention(
        priority="medium",
        fingerprint="def67890",
        broadcast_dt="15-Apr-2026 10:00:00",
        archive_root=archive_root,
    )
    assert decision.action == "discard"
    assert decision.archive_path is None
    assert not decision.will_write_file()


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


def test_bad_broadcast_dt_still_discards(archive_root: Path):
    # with discard policy the date is irrelevant — no archive path is built
    decision = decide_retention(
        priority="high",
        fingerprint="aaa",
        broadcast_dt="garbage-not-a-date",
        archive_root=archive_root,
    )
    assert decision.action == "discard"
    assert decision.archive_path is None