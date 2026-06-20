"""Tests for confidence calibration (W26)."""
from __future__ import annotations

import sqlite3

from nse_data.signals import calibration as cal


def test_reliability_curve_well_calibrated():
    pairs = [(0.75, True)] * 7 + [(0.75, False)] * 3       # 70% win in the 0.7-0.8 bucket
    curve = cal.reliability_curve(pairs)
    assert len(curve) == 1
    b = curve[0]
    assert b["n"] == 10 and b["actual_win"] == 0.7 and b["expected"] == 0.75
    assert abs(b["error"] + 0.05) < 1e-9                   # nearly calibrated
    assert cal.expected_calibration_error(pairs) == 0.05


def test_miscalibrated_bucket_flagged():
    pairs = [(0.85, True)] * 10 + [(0.85, False)] * 10     # 50% win on a 0.85-scored bucket
    bad = cal.miscalibrated_buckets(pairs, tol=0.10)
    assert len(bad) == 1 and bad[0]["error"] < -0.3        # systematically over-confident
    assert cal.expected_calibration_error(pairs) > 0.3


def test_no_data():
    assert cal.expected_calibration_error([]) is None
    assert cal.load_outcome_pairs(sqlite3.connect(":memory:")) == []   # no tables → empty
