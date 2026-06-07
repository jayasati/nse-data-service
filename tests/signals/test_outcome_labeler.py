"""Nightly outcome labeler (signals/outcome_labeler.py)."""

from __future__ import annotations

from datetime import datetime

import pytest

from nse_data.scheduler.market_hours import IST
from nse_data.signals import outcome_labeler as ol

from .conftest import SESSION_START

# Signal detection anchored to a 5-min boundary so resampled bars line up.
DETECTED_TS = SESSION_START                       # 09:15, divisible by 300
DETECTED_AT = datetime.fromtimestamp(DETECTED_TS, IST).isoformat()
DETECTED_DAY = DETECTED_AT[:10]                    # '2025-06-02'
# Labeler "now": that evening at 19:30.
NOW_EVENING = datetime(2025, 6, 2, 19, 30, 0, tzinfo=IST)


def _seed_signal(conn, *, symbol="ACME", price=100.0, atr=2.0,
                 detected_at=DETECTED_AT) -> int:
    cur = conn.execute(
        "INSERT INTO signals (symbol, signal_type, detected_at, price) "
        "VALUES (?, 'long_buildup', ?, ?)",
        (symbol, detected_at, price),
    )
    sid = int(cur.lastrowid)
    conn.execute(
        "INSERT INTO signal_features (signal_id, symbol, detected_at, atr_14_daily) "
        "VALUES (?, ?, ?, ?)",
        (sid, symbol, detected_at, atr),
    )
    conn.commit()
    return sid


def _bar(conn, symbol, ts, *, o, h, l, c):
    conn.execute(
        "INSERT INTO raw_intraday_candles "
        "(symbol, interval, ts, open, high, low, close, volume) "
        "VALUES (?, 'minute', ?, ?, ?, ?, ?, 1000)",
        (symbol, ts, o, h, l, c),
    )


def _seed_intraday_session(conn, symbol="ACME"):
    """Bars at entry +0..+2h. Entry=100; crafted highs/lows for MAE/MFE/hits."""
    # Per-bucket overrides (k -> OHLC); everything else is a flat 100 bar.
    overrides = {
        6: (104, 105, 104, 105),    # +30m close = 105
        9: (106, 112, 106, 108),    # +45m high spike 112 -> MFE
        12: (100, 100, 96, 98),     # +60m low dip 96 -> MAE
        24: (109, 110, 109, 110),   # +2h close = 110
        30: (109, 109, 107, 108),   # last (EOD) bar close = 108
    }
    for k in range(0, 31):                         # 0..150 min
        o, h, l, c = overrides.get(k, (100, 100, 100, 100))
        _bar(conn, symbol, DETECTED_TS + k * 300, o=o, h=h, l=l, c=c)
    conn.commit()


def _seed_future_closes(conn, symbol="ACME", closes=(103.0, 104.0, 106.0)):
    from datetime import date, timedelta
    base = date.fromisoformat(DETECTED_DAY)
    for i, c in enumerate(closes, start=1):
        d = (base + timedelta(days=i)).isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO raw_bhavcopy_cm (date, symbol, series, close, volume) "
            "VALUES (?, ?, 'EQ', ?, 1000)",
            (d, symbol, c),
        )
    conn.commit()


def _outcome(conn, sid):
    return conn.execute(
        "SELECT ret_30m, ret_2h, ret_eod, ret_1d, ret_3d, mae, mfe, hit_t1, hit_sl "
        "FROM signal_outcomes WHERE signal_id = ?", (sid,),
    ).fetchone()


def test_full_label(signals_db):
    sid = _seed_signal(signals_db)
    _seed_intraday_session(signals_db)
    _seed_future_closes(signals_db)

    report = ol.run_labeler_pass(signals_db, now=NOW_EVENING)
    assert report["labeled"] == 1

    ret_30m, ret_2h, ret_eod, ret_1d, ret_3d, mae, mfe, hit_t1, hit_sl = _outcome(signals_db, sid)
    assert ret_30m == pytest.approx(5.0)     # 105 vs 100
    assert ret_2h == pytest.approx(10.0)     # 110
    assert ret_eod == pytest.approx(8.0)     # 108
    assert ret_1d == pytest.approx(3.0)      # T+1 close 103
    assert ret_3d == pytest.approx(6.0)      # T+3 close 106
    assert mfe == pytest.approx(12.0)        # high 112
    assert mae == pytest.approx(-4.0)        # low 96
    assert (hit_t1, hit_sl) == (1, 1)        # t1=103 hit, sl=97 hit


def test_longer_horizons_fill_in_later(signals_db):
    sid = _seed_signal(signals_db)
    _seed_intraday_session(signals_db)
    _seed_future_closes(signals_db, closes=(103.0,))     # only T+1 available

    ol.run_labeler_pass(signals_db, now=NOW_EVENING)
    row = _outcome(signals_db, sid)
    assert row[3] == pytest.approx(3.0)      # ret_1d set
    assert row[4] is None                     # ret_3d not yet

    # Next nights' bhavcopy arrives -> re-label fills ret_3d.
    _seed_future_closes(signals_db, closes=(103.0, 104.0, 106.0))
    report = ol.run_labeler_pass(signals_db, now=NOW_EVENING)
    assert report["labeled"] == 1             # picked up because ret_3d was NULL
    assert _outcome(signals_db, sid)[4] == pytest.approx(6.0)


def test_completed_signal_skipped(signals_db):
    _seed_signal(signals_db)
    _seed_intraday_session(signals_db)
    _seed_future_closes(signals_db)
    ol.run_labeler_pass(signals_db, now=NOW_EVENING)

    # Second run: ret_3d already set -> not a candidate.
    report = ol.run_labeler_pass(signals_db, now=NOW_EVENING)
    assert report["candidates"] == 0


def test_signal_outside_lookback_ignored(signals_db):
    _seed_signal(signals_db, detected_at="2025-05-01T10:00:00+05:30")
    report = ol.run_labeler_pass(signals_db, now=NOW_EVENING, lookback_days=5)
    assert report["candidates"] == 0


def test_no_entry_price_returns_none(signals_db):
    cur = signals_db.execute(
        "INSERT INTO signals (symbol, signal_type, detected_at, price) "
        "VALUES ('X', 'long_buildup', ?, NULL)", (DETECTED_AT,),
    )
    signals_db.commit()
    out = ol.label_signal(signals_db, int(cur.lastrowid), "X", DETECTED_AT, None, 2.0, now=NOW_EVENING)
    assert out is None
