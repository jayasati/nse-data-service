"""Tests for subject -> priority classification."""

from __future__ import annotations

import pytest

from nse_data.parsers.subject_classifier import (
    classify_subject,
    get_unknown_subjects,
    reset_unknown_tracking,
)


@pytest.fixture(autouse=True)
def _clear_unknown_tracking():
    reset_unknown_tracking()
    yield
    reset_unknown_tracking()


def test_known_high_subjects():
    assert classify_subject("Outcome of Board Meeting") == "high"
    assert classify_subject("Dividend") == "high"
    assert classify_subject("Acquisition") == "high"


def test_known_medium_subjects():
    assert classify_subject("Investor Presentation") == "medium"
    assert classify_subject("Press Release") == "medium"


def test_known_low_subjects():
    assert classify_subject("Copy of Newspaper Publication") == "low"


def test_known_skip_subjects():
    assert classify_subject("Trading Window") == "skip"
    assert classify_subject("Structural Digital Database") == "skip"


def test_unknown_subject_defaults_to_medium():
    assert classify_subject("Some Made Up Subject That Is Not Real") == "medium"


def test_unknown_subjects_get_tracked():
    classify_subject("Random Thing One")
    classify_subject("Random Thing Two")
    classify_subject("Random Thing One")   # duplicate, should still be tracked once
    assert "Random Thing One" in get_unknown_subjects()
    assert "Random Thing Two" in get_unknown_subjects()
    assert len(get_unknown_subjects()) == 2


def test_empty_subject_returns_default():
    assert classify_subject(None) == "medium"
    assert classify_subject("") == "medium"
    assert classify_subject("   ") == "medium"


def test_subject_with_whitespace_is_trimmed():
    assert classify_subject("  Outcome of Board Meeting  ") == "high"