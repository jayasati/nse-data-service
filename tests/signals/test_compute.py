"""Signal metric functions (signals/compute.py)."""

from __future__ import annotations

import pytest

from nse_data.indicators.intraday_ohlcv import read_intraday_5m
from nse_data.signals import compute

from .conftest import (
    NOW, SESSION_START, seed_bhavcopy, seed_intraday, seed_oi_spurt, seed_quote,
)


def test_compute_oi_change_percent_and_legs(signals_db):
    seed_oi_spurt(signals_db, "ACME", latest_oi=120, prev_oi=100)
    pct, prev, curr = compute.compute_oi_change(signals_db, "ACME")
    assert pct == pytest.approx(20.0)
    assert (prev, curr) == (100, 120)


def test_compute_oi_change_uses_latest_snapshot(signals_db):
    seed_oi_spurt(signals_db, "ACME", latest_oi=110, prev_oi=100, as_of=SESSION_START)
    seed_oi_spurt(signals_db, "ACME", latest_oi=200, prev_oi=100, as_of=SESSION_START + 300)
    pct, _, curr = compute.compute_oi_change(signals_db, "ACME")
    assert curr == 200 and pct == pytest.approx(100.0)


def test_compute_oi_change_missing_or_zero_prev(signals_db):
    assert compute.compute_oi_change(signals_db, "NOPE") == (None, None, None)
    seed_oi_spurt(signals_db, "ZERO", latest_oi=5, prev_oi=0)
    pct, prev, curr = compute.compute_oi_change(signals_db, "ZERO")
    assert pct is None and prev == 0 and curr == 5


def test_compute_price_change(signals_db):
    seed_quote(signals_db, "ACME", last_price=464.8, pct_change=1.45)
    assert compute.compute_price_change(signals_db, "ACME") == (1.45, 464.8)
    assert compute.compute_price_change(signals_db, "NOPE") == (None, None)


def test_compute_volume_ratio(signals_db):
    # 20 daily bars @ 75_000 -> avg daily 75_000 -> avg 5m baseline = 1_000.
    seed_bhavcopy(signals_db, "ACME", days=20, volume=75_000)
    seed_intraday(signals_db, "ACME", bars=15, volume=400, end_ts=int(NOW.timestamp()))

    # Anchor to what the resampler actually produces (bucket alignment aside),
    # then check the BARS_PER_SESSION baseline division is applied.
    bars = read_intraday_5m(signals_db, "ACME", since_ts=SESSION_START)
    last_bucket_vol = float(bars["volume"].iloc[-1])
    expected = last_bucket_vol / (75_000 / compute.BARS_PER_SESSION)

    ratio = compute.compute_volume_ratio(signals_db, "ACME", now=NOW)
    assert ratio == pytest.approx(expected)
    assert ratio == pytest.approx(last_bucket_vol / 1_000.0)


def test_compute_volume_ratio_missing_inputs(signals_db):
    # No intraday -> None even with daily history.
    seed_bhavcopy(signals_db, "ACME", days=20, volume=75_000)
    assert compute.compute_volume_ratio(signals_db, "ACME", now=NOW) is None

    # No daily history -> None even with an intraday bar.
    seed_intraday(signals_db, "NOHIST", bars=5, volume=300, end_ts=int(NOW.timestamp()))
    assert compute.compute_volume_ratio(signals_db, "NOHIST", now=NOW) is None
