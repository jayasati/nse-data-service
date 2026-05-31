"""Session-anchored intraday VWAP on 5-min bars — reset at 09:15, volume weighting."""

from __future__ import annotations

from nse_data.indicators.compute import compute_for_symbol
from nse_data.indicators.volume.vwap_intraday import VwapIntraday

from .conftest import insert_intraday_candles

_BAR_SECS_1M = 60
_SESSION_SECS = 86_400

# Mon 2026-05-26 03:45 UTC ≈ 09:15 IST — the same anchor the RSI test uses.
_BASE_TS = 1_779_435_900


def _vwap_rows(conn, symbol):
    return conn.execute(
        "SELECT ts, vwap FROM indicator_vwap_5m WHERE symbol=? ORDER BY ts",
        (symbol,),
    ).fetchall()


def test_vwap_resets_each_session(indicators_db):
    """VWAP accumulates within a session and restarts at the next day's open.

    Helper bars are O=H=L=C with a constant 1000 volume, so each 5-min bar's
    typical price is its close and equal weights make VWAP the running mean of
    the 5-min closes within the session.
    """
    # Session A (day 0): two 5-min bars — closes 100 then 200.
    insert_intraday_candles(
        indicators_db, "TEST", [100.0] * 5 + [200.0] * 5,
        start_ts=_BASE_TS, bar_seconds=_BAR_SECS_1M,
    )
    # Session B (next IST day): two 5-min bars — closes 50 then 60.
    insert_intraday_candles(
        indicators_db, "TEST", [50.0] * 5 + [60.0] * 5,
        start_ts=_BASE_TS + _SESSION_SECS, bar_seconds=_BAR_SECS_1M,
    )

    result = compute_for_symbol(indicators_db, VwapIntraday(), "TEST")
    assert result.rows_written == 4

    rows = _vwap_rows(indicators_db, "TEST")
    vwaps = [v for _, v in rows]
    # Session A: 100, then mean(100, 200) = 150.
    assert vwaps[0] == 100.0
    assert vwaps[1] == 150.0
    # Session B resets — NOT blended with A. 50, then mean(50, 60) = 55.
    assert vwaps[2] == 50.0
    assert vwaps[3] == 55.0
    # PK column is `ts` (int epoch), not a date string.
    assert isinstance(rows[0][0], int)


def test_vwap_is_volume_weighted(indicators_db):
    """A high-volume bar pulls VWAP toward its price (not a simple average)."""
    # Bar 1: five minutes at price 100, volume 100/min → 5m bar vol 500.
    # Bar 2: five minutes at price 200, volume 900/min → 5m bar vol 4500.
    rows = []
    for i in range(5):
        rows.append((_BASE_TS + i * 60, 100.0, 100))
    for i in range(5, 10):
        rows.append((_BASE_TS + i * 60, 200.0, 900))
    for ts, price, vol in rows:
        indicators_db.execute(
            "INSERT INTO raw_intraday_candles "
            "(symbol, interval, ts, open, high, low, close, volume) "
            "VALUES (?, 'minute', ?, ?, ?, ?, ?, ?)",
            ("WTD", ts, price, price, price, price, vol),
        )
    indicators_db.commit()

    compute_for_symbol(indicators_db, VwapIntraday(), "WTD")
    vwaps = [v for _, v in _vwap_rows(indicators_db, "WTD")]

    # Bar 1 VWAP = 100. Bar 2 VWAP = (100·500 + 200·4500) / 5000 = 190 —
    # weighted toward 200, well above the simple mean of 150.
    assert vwaps[0] == 100.0
    assert abs(vwaps[1] - 190.0) < 1e-9
