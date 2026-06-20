"""Tests for the EOD summary (W27)."""
from __future__ import annotations

import datetime as _dt
import sqlite3

from nse_data.bot import eod_summary as eod

_NOW = _dt.datetime(2026, 6, 19, 18, 0, tzinfo=_dt.timezone(_dt.timedelta(hours=5, minutes=30)))


def _db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE market_state (as_of TEXT, nifty_return_pct REAL, vix_level REAL, updated_at TEXT);"
        "CREATE TABLE sector_state (sector_name TEXT, as_of TEXT, rs_rank INTEGER, sector_return_pct REAL);"
        "CREATE TABLE signals (signal_type TEXT, detected_at TEXT);"
        "CREATE TABLE paper_book (status TEXT, entry_date TEXT, exit_date TEXT, net_pct REAL);"
        "CREATE TABLE pending_events (symbol TEXT, event_type TEXT, expected_date TEXT, status TEXT);"
        "CREATE TABLE smart_money_daily (as_of_date TEXT, score REAL);")
    conn.execute("INSERT INTO smart_money_daily VALUES ('2026-06-19', 0.61)")
    conn.execute("INSERT INTO market_state VALUES ('2026-06-19T15:30', 0.85, 13.2, 'x')")
    conn.executemany("INSERT INTO sector_state VALUES (?, '2026-06-19', ?, ?)",
                     [("NIFTY IT", 1, 1.9), ("NIFTY PHARMA", 6, 0.1), ("NIFTY METAL", 11, -1.4)])
    conn.execute("INSERT INTO signals VALUES ('credit_upgrade', '2026-06-19T16:00')")
    conn.executemany("INSERT INTO paper_book VALUES (?,?,?,?)", [
        ("closed", "2026-06-01", "2026-06-19", 4.5), ("open", "2026-06-19", None, None)])
    conn.execute("INSERT INTO pending_events VALUES ('INFY','result','2026-06-22','pending')")
    conn.commit()
    return conn


def test_build_eod_summary_sections():
    out = eod.build_eod_summary(_db(), now=_NOW)
    assert "EOD Summary — 2026-06-19" in out
    assert "Nifty: +0.85% | VIX: 13.2" in out
    assert "Best NIFTY IT (+1.90%)" in out and "Worst NIFTY METAL (-1.40%)" in out
    assert "Smart money: 0.61 (mixed)" in out              # W22 score wired in
    assert "credit_upgrade 1" in out                       # today's signals
    assert "1 opened · 1 closed (net +4.5%)" in out        # paper activity
    assert "INFY (result)" in out                          # next trading day's event (Mon 22nd)


def test_build_degrades_on_empty_db():
    out = eod.build_eod_summary(sqlite3.connect(":memory:"), now=_NOW)
    assert "EOD Summary" in out and "Sectors: n/a" in out   # never crashes


def test_send_skips_without_config(monkeypatch):
    monkeypatch.setattr(eod, "load_telegram_config", lambda: (None, None))
    assert eod.send_eod_summary("x.db")["skipped"] == "no_telegram_config"
