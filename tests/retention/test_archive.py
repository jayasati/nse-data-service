"""Tests for atomic PDF archive writes."""

from __future__ import annotations

from pathlib import Path

import pytest

from nse_data.retention.archive import write_pdf
from nse_data.retention.policy import RetentionDecision


def test_permanent_archive_writes_to_target(tmp_path: Path):
    target = tmp_path / "pdfs" / "2026" / "05" / "21" / "fp1.pdf"
    decision = RetentionDecision(
        action="archive_permanent",
        archive_path=target,
        retention_policy_label="high_forever",
    )
    result_path = write_pdf(decision, b"%PDF-1.4 content")
    assert result_path == target
    assert target.exists()
    assert target.read_bytes() == b"%PDF-1.4 content"


def test_temp_archive_writes_to_temp_dir(tmp_path: Path):
    target = tmp_path / "pdfs_temp" / "fp2.pdf"
    decision = RetentionDecision(
        action="archive_temp_30d",
        archive_path=target,
        retention_policy_label="medium_30d",
    )
    write_pdf(decision, b"%PDF data")
    assert target.exists()


def test_discard_writes_nothing(tmp_path: Path):
    decision = RetentionDecision(
        action="discard",
        archive_path=None,
        retention_policy_label="low_textonly",
    )
    result = write_pdf(decision, b"%PDF data")
    assert result is None
    # Nothing on disk in tmp_path beyond what was there
    assert not list(tmp_path.glob("**/*.pdf"))


def test_do_not_download_writes_nothing(tmp_path: Path):
    decision = RetentionDecision(
        action="do_not_download",
        archive_path=None,
        retention_policy_label="skip",
    )
    result = write_pdf(decision, b"unused")
    assert result is None


def test_atomic_write_no_tmp_leftover(tmp_path: Path):
    target = tmp_path / "pdfs" / "x.pdf"
    decision = RetentionDecision(
        action="archive_permanent",
        archive_path=target,
        retention_policy_label="high_forever",
    )
    write_pdf(decision, b"%PDF")
    # No .tmp files should remain
    tmp_files = list(tmp_path.glob("**/*.tmp"))
    assert tmp_files == []


def test_overwrites_existing_file(tmp_path: Path):
    target = tmp_path / "pdfs" / "x.pdf"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old content")

    decision = RetentionDecision(
        action="archive_permanent",
        archive_path=target,
        retention_policy_label="high_forever",
    )
    write_pdf(decision, b"%PDF new content")
    assert target.read_bytes() == b"%PDF new content"