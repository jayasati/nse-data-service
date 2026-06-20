"""Tests for Week-20 exhaustion + announcement-reaction and Week-21 stop-hunt detectors."""
from __future__ import annotations

import sqlite3

from nse_data.psychology import announcement_tracker as at
from nse_data.psychology import exhaustion_detector as ex
from nse_data.psychology import stop_hunt_detector as sh


# ---- W20.4/20.5/20.6 exhaustion -------------------------------------------

def test_fomo_and_capitulation_messages():
    fomo = ex.fomo_warning_message("AAA", {"consecutive_up_days": 7, "rsi_5m": 82,
                                            "price_vs_vwap": "above", "resistance": 1250.0})
    assert "FOMO Warning" in fomo and "DO NOT CHASE" in fomo and "₹1250.00" in fomo
    cap = ex.capitulation_watch_message("BBB", {"consecutive_down_days": 6, "rsi_5m": 18,
                                                "price_vs_vwap": "below", "delivery_trend": "rising"})
    assert "Capitulation Zone" in cap and "oversold extreme" in cap and "rising" in cap


def test_find_exhaustion_picks_tagged_states():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE indicator_live (symbol TEXT, psych_state TEXT, "
                 "consecutive_up_days INT, consecutive_down_days INT, rsi_5m REAL, price_vs_vwap TEXT)")
    conn.executemany("INSERT INTO indicator_live VALUES (?,?,?,?,?,?)", [
        ("FOO", "FOMO_EUPHORIA", 7, 0, 82, "above"),
        ("BAR", "CAPITULATION", 0, 6, 18, "below"),
        ("BAZ", "NEUTRAL_TRENDING", 1, 0, 55, "above")])
    conn.commit()
    alerts = ex.find_exhaustion(conn)
    by = {a["symbol"]: a["type"] for a in alerts}
    assert by == {"FOO": ex.FOMO_WARNING, "BAR": ex.CAPITULATION_WATCH}    # neutral excluded


def test_exhaustion_dedup_cooldown():
    class _R:                                  # fake Redis: set NX succeeds once
        def __init__(self): self.keys = set()
        def set(self, k, v, nx=False, ex=None):
            if k in self.keys: return False
            self.keys.add(k); return True
    import datetime as dt
    now = dt.datetime(2026, 6, 20, 10, 0)
    assert ex._claim(_R(), "X", ex.FOMO_WARNING, now) is True
    r = _R()
    assert ex._claim(r, "X", ex.FOMO_WARNING, now) is True
    assert ex._claim(r, "X", ex.FOMO_WARNING, now) is False                # second within cooldown


# ---- W21.1 stop-hunt -------------------------------------------------------

def _bar(low, high, close, vol):
    return {"low": low, "high": high, "close": close, "volume": vol}


def test_stop_hunt_detects_liquidity_grab():
    bars = [_bar(100, 102, 101, 100), _bar(100, 101, 100.5, 100),
            _bar(98, 101, 99, 500),              # grab: low<100 support, 5x volume
            _bar(99.5, 102, 101, 300),           # recovery: closes back above 100
            _bar(100.5, 102, 101.5, 120)]        # volume cools after reclaim
    grab = sh.detect_stop_hunt(bars, support=100.0)
    assert grab is not None
    assert grab["wick_low"] == 98.0 and grab["entry"] == 101.0 and grab["sl"] < 98.0


def test_stop_hunt_none_without_dip_or_recovery():
    flat = [_bar(101, 103, 102, 100) for _ in range(5)]      # never dips below support
    assert sh.detect_stop_hunt(flat, support=100.0) is None
    assert sh.detect_stop_hunt(flat, support=None) is None    # no support


# ---- W20.1/20.2/20.3 announcement reaction --------------------------------

def test_classify_reaction_types():
    assert at.classify_reaction(3.0, 2.0) == at.SPIKE_AND_HOLD
    assert at.classify_reaction(3.0, 0.5) == at.SPIKE_AND_FADE
    assert at.classify_reaction(0.5, 0.3) == at.NO_REACTION
    assert at.classify_reaction(1.5, -2.0, positive_news=True) == at.REVERSE_REACTION
    assert at.classify_reaction(None, 1.0) is None


def test_sell_the_news_and_better_than_feared():
    assert at.is_sell_the_news(at.SPIKE_AND_FADE, positive_news=True) is True
    assert at.is_sell_the_news(at.REVERSE_REACTION, positive_news=True) is True
    assert at.is_sell_the_news(at.SPIKE_AND_HOLD, positive_news=True) is False
    assert at.is_better_than_feared(-8.0, 2.0) is True        # feared name rises post-news
    assert at.is_better_than_feared(-8.0, 0.2) is False       # didn't rise enough
    assert at.is_better_than_feared(2.0, 2.0) is False        # wasn't feared
