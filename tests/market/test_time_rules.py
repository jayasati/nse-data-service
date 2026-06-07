"""Unit tests for market.time_rules — window boundaries + multipliers."""

from __future__ import annotations

from datetime import datetime

from nse_data.market import time_rules as tr
from nse_data.scheduler.market_hours import IST


def at(h: int, m: int) -> datetime:
    return datetime(2026, 6, 5, h, m, tzinfo=IST)   # a Friday


def test_pre_open_suppressed():
    r = tr.time_rule(at(9, 0))
    assert r.window == "PRE_OPEN" and r.suppressed


def test_no_trade_open_window():
    r = tr.time_rule(at(9, 20))
    assert r.window == "NO_TRADE" and r.suppressed


def test_prime_window():
    r = tr.time_rule(at(10, 0))
    assert r.window == "PRIME_WINDOW" and r.multiplier == 1.00 and not r.suppressed


def test_first_exhaustion():
    assert tr.time_rule(at(11, 15)).window == "FIRST_EXHAUSTION"
    assert tr.time_rule(at(11, 15)).multiplier == 0.90


def test_lunch_zone_has_floor():
    r = tr.time_rule(at(12, 0))
    assert r.window == "LUNCH_ZONE" and r.multiplier == 0.80
    assert r.min_confidence == 0.72


def test_second_window():
    assert tr.time_rule(at(14, 0)).window == "SECOND_WINDOW"


def test_closing_approach_and_pressure():
    assert tr.time_rule(at(14, 45)).window == "CLOSING_APPROACH"
    assert tr.time_rule(at(15, 10)).window == "CLOSING_PRESSURE"
    assert tr.time_rule(at(15, 10)).multiplier == 0.75


def test_no_new_trades_after_1520():
    r = tr.time_rule(at(15, 25))
    assert r.window == "NO_NEW_TRADES" and r.suppressed


def test_boundaries_are_inclusive_of_start():
    # exactly 09:30 -> PRIME, exactly 11:00 -> FIRST_EXHAUSTION
    assert tr.time_rule(at(9, 30)).window == "PRIME_WINDOW"
    assert tr.time_rule(at(11, 0)).window == "FIRST_EXHAUSTION"
    assert tr.time_rule(at(15, 20)).window == "NO_NEW_TRADES"


def test_time_multiplier_helper():
    assert tr.time_multiplier(at(10, 0)) == 1.00
    assert tr.time_multiplier(at(15, 10)) == 0.75
