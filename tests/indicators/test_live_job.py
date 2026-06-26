"""Live indicator job: market-hours gate + universe routing."""

from __future__ import annotations

from unittest.mock import patch

from nse_data.indicators import live_job


def test_run_intraday_pass_skips_when_market_closed(tmp_path):
    """Off-hours fires should be a cheap no-op, no DB open, no work."""
    with patch("nse_data.indicators.live_job.is_market_open", return_value=False):
        # db_path is irrelevant — gate returns before we touch it.
        report = live_job.run_intraday_pass(str(tmp_path / "no-such.db"))
    assert report == {"skipped": "market_closed"}


def test_run_intraday_pass_invokes_compute_when_market_open(tmp_path):
    """When the gate passes, we open the DB and run the intraday cadence."""
    db_path = tmp_path / "live.db"

    # Seed a minimal DB: bhavcopy + intraday_candles + indicator_rsi_5m schema
    # plus one symbol's worth of minute bars so RsiIntraday has something to chew.
    import sqlite3
    from pathlib import Path
    import nse_data
    pkg_root = Path(nse_data.__file__).resolve().parent.parent.parent
    migrations = pkg_root / "migrations"

    conn = sqlite3.connect(db_path)
    for m in [
        "003_bhavcopy.sql", "010_phase7_day5.sql", "025_intraday_candles.sql",
        "029_indicator_rsi_5m.sql", "030_indicator_macd_5m.sql",
        "034_indicator_vwap_5m.sql", "035_indicator_live.sql",
        "043_indicator_eod_set.sql", "044_intraday_supertrend_voldelta.sql",
        "066_indicator_expansion.sql", "067_indicator_adx_5m.sql",
        "068_indicator_ema_200.sql", "069_indicator_chop.sql",
        "070_pre_event_psychology.sql",          # indicator_live psych/pre_event cols
        "106_indicator_orb_5m.sql",              # indicator_orb_5m table
        "107_indicator_live_intraday_board.sql", # indicator_live ltp/orb_*/rvol_5m/structure_5m
    ]:
        conn.executescript((migrations / m).read_text())

    # Tradable-universe fixture: one symbol in raw_fno_list so
    # fno_plus_nifty500() returns a non-empty list.
    conn.execute(
        "INSERT INTO raw_fno_list (symbol, fetched_at) VALUES ('TESTSYM', 0)"
    )

    # Enough 1-min bars to clear RSI/MACD warm-up after 5-min resample.
    base_ts = 1_779_000_000
    for i in range(500):
        price = 100.0 + (i / 5)
        conn.execute(
            "INSERT INTO raw_intraday_candles "
            "(symbol, interval, ts, open, high, low, close, volume) "
            "VALUES ('TESTSYM', 'minute', ?, ?, ?, ?, ?, 1000)",
            (base_ts + i * 60, price, price, price, price),
        )
    conn.commit()
    conn.close()

    with patch("nse_data.indicators.live_job.is_market_open", return_value=True):
        report = live_job.run_intraday_pass(str(db_path))

    assert report["symbols"] == 1
    assert report["rows_written"].get("rsi_5m", 0) > 0
    assert report["rows_written"].get("macd_5m", 0) > 0
