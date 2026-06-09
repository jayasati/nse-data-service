"""Signal detector — gates, rules, dedup, persistence (signals/detect.py)."""

from __future__ import annotations

from datetime import datetime

from nse_data.scheduler.market_hours import IST
from nse_data.signals import detect
from nse_data.signals.dedup import SignalDedup

from .conftest import (
    NOW, SESSION_START, FakeRedis,
    seed_52w_high, seed_bhavcopy, seed_intraday, seed_oi_spurt,
    seed_price_band, seed_quote, seed_universe,
)


def _seed_long_buildup(conn, symbol="ACME") -> None:
    """A clean, fully-firing long_buildup setup for `symbol`."""
    seed_universe(conn, symbol)
    seed_oi_spurt(conn, symbol, latest_oi=110, prev_oi=100)        # +10% OI
    seed_quote(conn, symbol, last_price=200.0, pct_change=2.0)     # +2% price
    seed_bhavcopy(conn, symbol, days=35, volume=75_000)           # >30 bars, avg5m=1000
    seed_intraday(conn, symbol, bars=15, volume=2_000,            # last 5m bucket >> 1.5×
                  end_ts=int(NOW.timestamp()))


def _run(conn, redis=None, dedup=None, now=NOW):
    return detect.run_detection_pass(conn, redis_client=redis, dedup=dedup, now=now)


# --------------------------------------------------------------- happy path

def test_long_buildup_fires_and_persists(signals_db):
    _seed_long_buildup(signals_db)
    report = _run(signals_db)

    assert report["fired"]["long_buildup"] == 1
    row = signals_db.execute(
        "SELECT symbol, signal_type, oi_change_pct, price_change_pct, "
        "volume_ratio, dispatched FROM signals"
    ).fetchone()
    assert row[0] == "ACME" and row[1] == "long_buildup"
    assert row[2] == 10.0   # (110-100)/100 * 100
    assert row[3] == 2.0
    assert row[4] >= detect.VOLUME_RATIO_MIN
    assert row[5] == 0   # undispatched


def test_signal_feature_snapshot_written(signals_db):
    _seed_long_buildup(signals_db)
    r = FakeRedis()
    r.hset("ind:ACME", mapping={
        "vwap": "150.0", "rsi_5m": "60.0", "trend_regime": "strong_uptrend",
        "momentum_state": "bullish", "atr_14_daily": "3.0", "vwap_slope": "0.5",
    })
    _run(signals_db, redis=r)

    feat = signals_db.execute(
        "SELECT sf.symbol, sf.vwap, sf.rsi_5m, sf.trend_regime, sf.volume_ratio "
        "FROM signal_features sf JOIN signals s ON s.id = sf.signal_id"
    ).fetchone()
    assert feat[0] == "ACME"
    assert feat[1] == 150.0 and feat[2] == 60.0
    assert feat[3] == "strong_uptrend"
    assert feat[4] >= detect.VOLUME_RATIO_MIN   # carried from the rule


def test_below_threshold_does_not_fire(signals_db):
    seed_universe(signals_db, "ACME")
    seed_oi_spurt(signals_db, "ACME", latest_oi=101, prev_oi=100)  # +1% OI < 3%
    seed_quote(signals_db, "ACME", last_price=200.0, pct_change=2.0)
    seed_bhavcopy(signals_db, "ACME", days=35, volume=75_000)
    seed_intraday(signals_db, "ACME", bars=15, volume=2_000, end_ts=int(NOW.timestamp()))
    assert _run(signals_db)["signals"] == 0


# --------------------------------------------------------------- dedup

def test_no_duplicate_within_pass_or_repeat(signals_db):
    _seed_long_buildup(signals_db)
    dedup = SignalDedup(None)
    assert _run(signals_db, dedup=dedup)["signals"] == 1
    # Second pass, same in-memory dedup -> blocked.
    assert _run(signals_db, dedup=dedup)["signals"] == 0
    assert signals_db.execute("SELECT COUNT(*) FROM signals").fetchone()[0] == 1


# --------------------------------------------------------------- breakout

def test_breakout_52wh_fires(signals_db):
    seed_universe(signals_db, "BRK")
    seed_52w_high(signals_db, "BRK", as_of=SESSION_START + 600)
    seed_bhavcopy(signals_db, "BRK", days=35, volume=75_000)
    seed_intraday(signals_db, "BRK", bars=15, volume=2_000, end_ts=int(NOW.timestamp()))
    # No OI/price feed -> long_buildup can't fire, isolating the breakout rule.
    report = _run(signals_db)
    assert report["fired"]["breakout_52wh"] == 1
    assert report["fired"]["long_buildup"] == 0


def test_breakout_ignores_prior_session_high(signals_db):
    seed_universe(signals_db, "BRK")
    seed_52w_high(signals_db, "BRK", as_of=SESSION_START - 86_400)  # yesterday
    seed_bhavcopy(signals_db, "BRK", days=35, volume=75_000)
    seed_intraday(signals_db, "BRK", bars=15, volume=2_000, end_ts=int(NOW.timestamp()))
    assert _run(signals_db)["fired"]["breakout_52wh"] == 0


# --------------------------------------------------------------- hard gates

def test_blacklisted_symbol_skipped(signals_db):
    _seed_long_buildup(signals_db)
    r = FakeRedis()
    r.sadd("blacklist:symbols", "ACME")
    report = _run(signals_db, redis=r)
    assert report["signals"] == 0 and report["gated"] == 1


def test_t2t_series_skipped(signals_db):
    _seed_long_buildup(signals_db)
    seed_price_band(signals_db, "ACME", series="BE")   # trade-to-trade
    report = _run(signals_db)
    assert report["signals"] == 0 and report["gated"] == 1


def test_newly_listed_skipped(signals_db):
    seed_universe(signals_db, "NEW")
    seed_oi_spurt(signals_db, "NEW", latest_oi=110, prev_oi=100)
    seed_quote(signals_db, "NEW", last_price=200.0, pct_change=2.0)
    seed_bhavcopy(signals_db, "NEW", days=10, volume=75_000)   # < 30 bars
    seed_intraday(signals_db, "NEW", bars=15, volume=2_000, end_ts=int(NOW.timestamp()))
    report = _run(signals_db)
    assert report["signals"] == 0 and report["gated"] == 1


def test_tight_price_band_skips_long(signals_db):
    _seed_long_buildup(signals_db)
    seed_price_band(signals_db, "ACME", series="EQ", band=2)   # ≤ 2% band
    assert _run(signals_db)["signals"] == 0


# --------------------------------------------------------------- time windows

def test_market_closed_is_noop(signals_db):
    _seed_long_buildup(signals_db)
    closed = datetime(2025, 6, 2, 16, 0, 0, tzinfo=IST)   # after 15:30
    assert _run(signals_db, now=closed) == {"skipped": "market_closed"}


def test_opening_window_skipped(signals_db):
    _seed_long_buildup(signals_db)
    opening = datetime(2025, 6, 2, 9, 20, 0, tzinfo=IST)
    # earnings reactions are still evaluated in the skip windows (event-driven)
    assert _run(signals_db, now=opening) == {"skipped": "opening_window", "earnings": 0}


def test_lunch_window_skipped(signals_db):
    _seed_long_buildup(signals_db)
    lunch = datetime(2025, 6, 2, 12, 0, 0, tzinfo=IST)
    assert _run(signals_db, now=lunch) == {"skipped": "lunch_window", "earnings": 0}
