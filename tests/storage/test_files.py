"""Tests for the PDF archive file-layout helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from nse_data.storage import files


# ---- filename round-trip --------------------------------------------------

def test_pdf_filename():
    assert files.pdf_filename("abc123") == "abc123.pdf"


def test_fingerprint_round_trip(tmp_path: Path):
    fp = "deadbeef00"
    path = tmp_path / files.pdf_filename(fp)
    assert files.fingerprint_from_path(path) == fp


# ---- date bucketing -------------------------------------------------------

def test_date_subpath_parses_nse_format():
    assert files.date_subpath("21-May-2026 19:40:44") == Path("2026") / "05" / "21"


def test_date_subpath_date_only():
    assert files.date_subpath("01-Jan-2026") == Path("2026") / "01" / "01"


@pytest.mark.parametrize("bad", ["", "not-a-date", "2026-05-21", None])
def test_date_subpath_bad_input_falls_back(bad):
    assert files.date_subpath(bad) == Path(files.UNKNOWN_DATE_DIR)


# ---- tier path builders ---------------------------------------------------

def test_permanent_path_is_date_bucketed(tmp_path: Path):
    p = files.permanent_path(tmp_path, "fp1", "21-May-2026 19:40:44")
    assert p == tmp_path / files.PERMANENT_SUBDIR / "2026" / "05" / "21" / "fp1.pdf"


def test_permanent_path_respects_config_subdir(tmp_path: Path):
    p = files.permanent_path(tmp_path, "fp1", "21-May-2026", subdir="custom")
    assert p == tmp_path / "custom" / "2026" / "05" / "21" / "fp1.pdf"


def test_temp_path_is_flat(tmp_path: Path):
    p = files.temp_path(tmp_path, "fp2")
    assert p == tmp_path / files.TEMP_SUBDIR / "fp2.pdf"


def test_scratch_path(tmp_path: Path):
    p = files.scratch_path(tmp_path, "fp3")
    assert p == tmp_path / files.SCRATCH_SUBDIR / "fp3.pdf"


def test_temp_root(tmp_path: Path):
    assert files.temp_root(tmp_path) == tmp_path / files.TEMP_SUBDIR


# ---- atomic write ---------------------------------------------------------

def test_atomic_write_creates_parents_and_file(tmp_path: Path):
    target = tmp_path / "deep" / "nested" / "x.pdf"
    returned = files.atomic_write_bytes(target, b"%PDF-1.4 data")
    assert returned == target
    assert target.read_bytes() == b"%PDF-1.4 data"


def test_atomic_write_leaves_no_tmp_file(tmp_path: Path):
    target = tmp_path / "x.pdf"
    files.atomic_write_bytes(target, b"data")
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


def test_atomic_write_overwrites_existing(tmp_path: Path):
    target = tmp_path / "x.pdf"
    files.atomic_write_bytes(target, b"old")
    files.atomic_write_bytes(target, b"new")
    assert target.read_bytes() == b"new"


def test_atomic_write_cleans_up_partial_on_failure(tmp_path: Path, monkeypatch):
    target = tmp_path / "x.pdf"

    # Simulate the rename failing after the .tmp is written.
    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(files.os, "replace", boom)

    with pytest.raises(OSError):
        files.atomic_write_bytes(target, b"data")

    # No .tmp should survive, and the target should not exist.
    assert list(tmp_path.glob("*.tmp")) == []
    assert not target.exists()
