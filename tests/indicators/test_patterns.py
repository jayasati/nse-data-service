"""Unit tests for the Week-15 pattern detectors."""

from __future__ import annotations

from nse_data.indicators import patterns as pat


def test_inside_bar():
    assert pat.is_inside_bar(100, 95, 105, 90) is True       # range inside prior
    assert pat.is_inside_bar(110, 95, 105, 90) is False      # high breaks out
    assert pat.is_inside_bar(100, 95, None, 90) is False


def test_volume_dryup():
    assert pat.is_volume_dryup(40, 100) is True              # < 50%
    assert pat.is_volume_dryup(80, 100) is False
    assert pat.is_volume_dryup(40, 0) is False               # no baseline


def test_near():
    assert pat.near(100, 100.4) is True                      # within 0.5%
    assert pat.near(100, 101) is False
    assert pat.near(100, None) is False


def test_bullish_divergence():
    # price marks a fresh low; RSI does not (higher low) -> bullish divergence
    prices = [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
    rsis = [30, 31, 32, 33, 34, 35, 36, 37, 38, 40]
    assert pat.detect_divergence(prices, rsis) == "bullish_divergence"


def test_bearish_divergence():
    prices = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    rsis = [70, 69, 68, 67, 66, 65, 64, 63, 62, 60]
    assert pat.detect_divergence(prices, rsis) == "bearish_divergence"


def test_no_divergence_when_aligned():
    seq = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert pat.detect_divergence(seq, seq) is None           # both new highs
    assert pat.detect_divergence([1, 2], [1, 2]) is None     # too short
