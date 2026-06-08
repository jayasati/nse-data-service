"""ORB-breakout and VWAP-reclaim intraday rules (detect.py)."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from nse_data.scheduler.market_hours import IST
from nse_data.signals import detect

NOW = datetime(2026, 6, 8, 9, 40, tzinfo=IST)   # after the 09:30 opening range


def _ts(h, m) -> int:
    return int(NOW.replace(hour=h, minute=m, second=0, microsecond=0).timestamp())


def _bars(rows) -> pd.DataFrame:
    return pd.DataFrame(
        rows, columns=["ts", "open", "high", "low", "close", "volume"]
    ).set_index("ts")


def _stub(monkeypatch, rows, vol_ratio=2.0, price_chg=(2.0, 0.0)):
    monkeypatch.setattr(detect, "read_intraday_5m", lambda *a, **k: _bars(rows))
    monkeypatch.setattr(detect.compute, "compute_volume_ratio", lambda *a, **k: vol_ratio)
    monkeypatch.setattr(detect.compute, "compute_price_change", lambda *a, **k: price_chg)


# ---- ORB breakout ----------------------------------------------------------

def test_orb_breakout_fires(monkeypatch):
    rows = [(_ts(9, 15), 99, 100, 98, 99, 1000),
            (_ts(9, 20), 99, 100, 98, 99, 1000),
            (_ts(9, 25), 99, 100, 98, 99, 1000),
            (_ts(9, 30), 100, 101, 100, 101, 1000),
            (_ts(9, 35), 101, 103, 101, 102, 1000)]   # close 102 > ORH 100
    _stub(monkeypatch, rows, price_chg=(3.0, 102.0))
    m = detect._rule_orb_breakout(None, "X", NOW)
    assert m is not None and m["price"] == 102.0 and m["volume_ratio"] == 2.0


def test_orb_no_breakout_below_range(monkeypatch):
    rows = [(_ts(9, 15), 99, 100, 98, 99, 1000),
            (_ts(9, 25), 99, 100, 98, 99, 1000),
            (_ts(9, 35), 99, 100, 98, 99.5, 1000)]    # 99.5 ≤ ORH 100
    _stub(monkeypatch, rows, price_chg=(0.5, 99.5))
    assert detect._rule_orb_breakout(None, "X", NOW) is None


def test_orb_low_volume_blocked(monkeypatch):
    rows = [(_ts(9, 15), 99, 100, 98, 99, 1000),
            (_ts(9, 25), 99, 100, 98, 99, 1000),
            (_ts(9, 35), 101, 103, 101, 102, 1000)]
    _stub(monkeypatch, rows, vol_ratio=1.0, price_chg=(3.0, 102.0))  # < 1.5
    assert detect._rule_orb_breakout(None, "X", NOW) is None


def test_orb_skipped_before_range_complete(monkeypatch):
    rows = [(_ts(9, 15), 99, 100, 98, 99, 1000)]
    _stub(monkeypatch, rows)
    early = NOW.replace(hour=9, minute=25)            # before 09:30
    assert detect._rule_orb_breakout(None, "X", early) is None


# ---- VWAP reclaim ----------------------------------------------------------

def test_vwap_reclaim_fires(monkeypatch):
    rows = [(_ts(9, 15), 100, 100, 100, 100, 1000),   # at vwap
            (_ts(9, 20), 99, 99, 97, 98, 2000),        # below
            (_ts(9, 25), 98, 99, 98, 98, 1000),        # below (prev bar)
            (_ts(9, 30), 99, 102, 99, 102, 3000)]      # reclaim above vwap
    _stub(monkeypatch, rows, price_chg=(2.0, 102.0))
    m = detect._rule_vwap_reclaim(None, "X", NOW)
    assert m is not None and m["price"] == 102.0


def test_vwap_no_reclaim_when_stays_above(monkeypatch):
    rows = [(_ts(9, 15), 100, 101, 100, 101, 1000),
            (_ts(9, 20), 101, 102, 101, 102, 1000),
            (_ts(9, 30), 102, 103, 102, 103, 1000)]    # never went below vwap
    _stub(monkeypatch, rows)
    assert detect._rule_vwap_reclaim(None, "X", NOW) is None


def test_intraday_rules_have_intraday_horizon():
    assert detect.horizon_for("orb_breakout") == "intraday"
    assert detect.horizon_for("vwap_reclaim") == "intraday"
