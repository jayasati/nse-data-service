"""Live mode — alert message + dedup."""
from __future__ import annotations

import sqlite3

import pandas as pd

from nse_data.strategy.daily_sweep import live
from nse_data.strategy.daily_sweep.setup import SweepSetup

_NOW = pd.Timestamp("2026-06-12 10:00", tz="Asia/Kolkata")


def _setup():
    return SweepSetup(symbol="RELIANCE", direction="long", daily_trend="bullish",
                      sweep_time=_NOW - pd.Timedelta("20min"), swept_level=1439.2,
                      sweep_extreme=1437.0, bos_time=_NOW - pd.Timedelta("10min"),
                      fvg_low=1440.0, fvg_high=1442.5, entry_time=_NOW, entry_price=1442.5,
                      stop=1437.0, target=1459.0, qty=226, risk_rupees=1243.0, rr=3.0)


def test_build_alert_has_key_fields():
    msg = live.build_alert(_setup())
    assert "Daily Sweep — RELIANCE" in msg and "LONG" in msg
    assert "Entry 1442.5" in msg and "SL 1437.0" in msg and "Target 1459.0" in msg
    assert "Forward-test" in msg


def test_scan_live_dedups(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE daily_sweep_signals (fingerprint TEXT PRIMARY KEY, symbol TEXT, "
                 "direction TEXT, daily_trend TEXT, sweep_time TEXT, bos_time TEXT, fvg_low REAL, "
                 "fvg_high REAL, entry_time TEXT, entry_price REAL, stop REAL, target REAL, "
                 "qty INTEGER, rr REAL, alerted_at INTEGER)")
    monkeypatch.setattr(live, "scan_setups", lambda *a, **k: [_setup()])
    monkeypatch.setattr(live, "read_daily", lambda *a, **k: None)
    monkeypatch.setattr(live, "read_1h", lambda *a, **k: None)
    monkeypatch.setattr(live, "read_5m", lambda *a, **k: None)
    monkeypatch.setattr(live, "load_telegram_config", lambda: (None, None))
    sent = []
    sender = lambda tok, ch, txt, **kw: sent.append(kw.get("channel")) or True
    first = live.scan_live(conn, symbols=["RELIANCE"], now=_NOW, sender=sender)
    second = live.scan_live(conn, symbols=["RELIANCE"], now=_NOW, sender=sender)
    assert len(first) == 1 and len(second) == 0          # fires once, then deduped
    assert sent == ["sweep"]                             # routed to the dedicated channel
