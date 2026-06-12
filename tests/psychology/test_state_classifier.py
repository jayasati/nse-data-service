"""Week 19.2/19.3: psychological state classifier."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from nse_data.psychology import state_classifier as sc
from nse_data.scheduler.market_hours import IST
from nse_data.storage import db as dbmod

NOW = dt.datetime(2025, 6, 2, 10, 0, tzinfo=IST)


# ============================================================================
# Pure measurement helpers
# ============================================================================

def test_consecutive_moves():
    assert sc.consecutive_moves([1, 2, 3, 4]) == (3, 0)
    assert sc.consecutive_moves([4, 3, 2, 1]) == (0, 3)
    assert sc.consecutive_moves([1, 2, 3, 2]) == (0, 1)
    assert sc.consecutive_moves([1, 1]) == (0, 0)          # unchanged ends streaks
    assert sc.consecutive_moves([5]) == (0, 0)
    assert sc.consecutive_moves([]) == (0, 0)


def test_volume_rising():
    assert sc.volume_rising([10, 20, 30, 40]) is True
    assert sc.volume_rising([10, 40, 30, 40]) is False
    assert sc.volume_rising([10, 20]) is False             # too short


def test_run_pct():
    closes = [100, 101, 102, 103, 104, 110]
    assert sc.run_pct(closes, 5) == pytest.approx(10.0)
    assert sc.run_pct(closes, 10) is None


def _bars(points, start_ts):
    """5-min frame from [(open, high, low, close)] tuples."""
    idx = [start_ts + i * 300 for i in range(len(points))]
    return pd.DataFrame(
        [{"open": o, "high": h, "low": lo, "close": c, "volume": 1000}
         for o, h, lo, c in points],
        index=idx,
    )


def test_spike_and_fade_detected():
    now_ts = 1_000_000
    # Pop from 100 to 103 (+3%), faded back to 100.6 (≥60% retrace).
    bars = _bars([(100, 100.5, 99.8, 100.2), (100.2, 103, 100, 102.8),
                  (102.8, 102.9, 100.5, 100.6)], now_ts - 1500)
    assert sc.detect_spike_and_fade(bars, now_ts) is True


def test_spike_without_fade_is_not_sell_news():
    now_ts = 1_000_000
    bars = _bars([(100, 100.5, 99.8, 100.2), (100.2, 103, 100, 102.8),
                  (102.8, 103.2, 102.5, 103.0)], now_ts - 1500)   # holding the pop
    assert sc.detect_spike_and_fade(bars, now_ts) is False


# ============================================================================
# Pure classifier — one test per state + precedence
# ============================================================================

def _m(**kw):
    base = {
        "consecutive_up_days": 0, "consecutive_down_days": 0,
        "volume_rising_daily": False, "rsi_5m": 55.0, "price_vs_vwap_pct": 0.0,
        "pre_event_run_5d": 0.0, "pre_event_run_10d": 0.0, "days_to_event": None,
        "iv_vs_avg": None, "event_arrived_today": False, "spike_and_fade": False,
        "delivery_rising": False, "ret_5d": 0.0, "price_rising_today": False,
        "today_vol_vs_down_avg": None,
    }
    base.update(kw)
    return base


def test_neutral_default():
    assert sc.classify_psych_state(_m()) == "NEUTRAL_TRENDING"


def test_fomo_euphoria():
    m = _m(consecutive_up_days=6, volume_rising_daily=True,
           rsi_5m=80.0, price_vs_vwap_pct=3.5)
    assert sc.classify_psych_state(m) == "FOMO_EUPHORIA"
    # any missing leg → not FOMO
    assert sc.classify_psych_state(_m(**{**m, "rsi_5m": 70.0})) == "NEUTRAL_TRENDING"
    assert sc.classify_psych_state(_m(**{**m, "consecutive_up_days": 5})) == "NEUTRAL_TRENDING"


def test_buy_rumor():
    m = _m(pre_event_run_10d=9.0, days_to_event=4, iv_vs_avg=1.5)
    assert sc.classify_psych_state(m) == "BUY_RUMOR"
    # missing IV data doesn't veto; LOW IV does
    assert sc.classify_psych_state(_m(**{**m, "iv_vs_avg": None})) == "BUY_RUMOR"
    assert sc.classify_psych_state(_m(**{**m, "iv_vs_avg": 1.0})) == "NEUTRAL_TRENDING"
    assert sc.classify_psych_state(_m(**{**m, "days_to_event": 6})) == "NEUTRAL_TRENDING"


def test_sell_news():
    m = _m(event_arrived_today=True, spike_and_fade=True)
    assert sc.classify_psych_state(m) == "SELL_NEWS"


def test_fear_building():
    m = _m(consecutive_down_days=3, rsi_5m=35.0, volume_rising_daily=True)
    assert sc.classify_psych_state(m) == "FEAR_BUILDING"


def test_capitulation():
    m = _m(consecutive_down_days=5, rsi_5m=20.0, price_vs_vwap_pct=-4.0,
           delivery_rising=True, volume_rising_daily=True)
    assert sc.classify_psych_state(m) == "CAPITULATION"
    # without the delivery confirmation it's fear, not capitulation
    assert sc.classify_psych_state(
        _m(**{**m, "delivery_rising": False})) == "FEAR_BUILDING"


def test_relief_bounce():
    m = _m(pre_event_run_10d=-12.0, event_arrived_today=True, price_rising_today=True)
    assert sc.classify_psych_state(m) == "RELIEF_BOUNCE"


def test_dead_cat_bounce():
    m = _m(ret_5d=-9.0, price_rising_today=True, today_vol_vs_down_avg=0.6)
    assert sc.classify_psych_state(m) == "DEAD_CAT_BOUNCE"
    # bounce on HEAVIER volume than the fall isn't a dead cat
    assert sc.classify_psych_state(
        _m(**{**m, "today_vol_vs_down_avg": 1.4})) == "NEUTRAL_TRENDING"


def test_event_states_outrank_momentum_extremes():
    m = _m(event_arrived_today=True, spike_and_fade=True,
           consecutive_up_days=6, volume_rising_daily=True,
           rsi_5m=85.0, price_vs_vwap_pct=5.0)
    assert sc.classify_psych_state(m) == "SELL_NEWS"


# ============================================================================
# Pass integration: measurements from the DB, write-back to live + Redis
# ============================================================================

@pytest.fixture()
def conn(tmp_path):
    c = dbmod.open_db(str(tmp_path / "t.db"))
    dbmod.apply_migrations(c, migrations_dir="migrations")
    yield c
    c.close()


class FakeRedis:
    def __init__(self):
        self.hashes: dict[str, dict] = {}

    def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update(mapping)


def _seed_daily(conn, symbol, closes, volumes):
    d = dt.date(2025, 5, 12)
    for close, vol in zip(closes, volumes):
        while d.weekday() >= 5:
            d += dt.timedelta(days=1)
        conn.execute(
            "INSERT INTO raw_bhavcopy_cm (date, symbol, series, close, volume) "
            "VALUES (?, ?, 'EQ', ?, ?)",
            (d.isoformat(), symbol, close, vol),
        )
        d += dt.timedelta(days=1)
    conn.commit()


def _seed_intraday_close(conn, symbol, price, now):
    session_open = int(now.replace(hour=9, minute=15, second=0, microsecond=0).timestamp())
    for i in range(10):
        conn.execute(
            "INSERT INTO raw_intraday_candles "
            "(symbol, interval, ts, open, high, low, close, volume) "
            "VALUES (?, 'minute', ?, ?, ?, ?, ?, 1000)",
            (symbol, session_open + i * 60, price, price, price, price),
        )
    conn.commit()


def test_pass_classifies_fomo_and_persists(conn):
    # 7 straight up days on rising volume…
    closes = [100 + i for i in range(8)]
    volumes = [1000 + i * 200 for i in range(8)]
    _seed_daily(conn, "ACME", closes, volumes)
    # …RSI(5m) 80 with VWAP 100 on the live row, price 105 (>3% above).
    conn.execute(
        "INSERT INTO indicator_live (symbol, updated_at, vwap, rsi_5m) "
        "VALUES ('ACME', 'x', 100.0, 80.0)",
    )
    conn.commit()
    _seed_intraday_close(conn, "ACME", 105.0, NOW)
    r = FakeRedis()

    report = sc.run_psychology_pass(conn, redis_client=r, now=NOW, symbols=["ACME"])
    assert report.get("FOMO_EUPHORIA") == 1

    row = conn.execute(
        "SELECT psych_state, consecutive_up_days, consecutive_down_days, vwap "
        "FROM indicator_live WHERE symbol='ACME'",
    ).fetchone()
    assert row[0] == "FOMO_EUPHORIA"
    assert row[1] == 7
    assert row[2] == 0
    assert row[3] == 100.0        # live job's column preserved by the UPSERT
    assert r.hashes["ind:ACME"]["psych_state"] == "FOMO_EUPHORIA"


def test_pass_neutral_for_quiet_symbol(conn):
    _seed_daily(conn, "QUIET", [100, 101, 100, 101, 100, 101, 100], [1000] * 7)
    _seed_intraday_close(conn, "QUIET", 100.0, NOW)

    report = sc.run_psychology_pass(conn, now=NOW, symbols=["QUIET"])
    assert report.get("NEUTRAL_TRENDING") == 1
    state = conn.execute(
        "SELECT psych_state FROM indicator_live WHERE symbol='QUIET'",
    ).fetchone()[0]
    assert state == "NEUTRAL_TRENDING"
