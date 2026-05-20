"""Unit tests for the market-hours gate."""

from __future__ import annotations

from datetime import date, datetime, time

from nse_data.scheduler.market_hours import (
    IST,
    is_market_open,
    is_pre_market_open,
    is_trading_day,
    is_trading_holiday,
    is_weekend,
)


def at(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    """Build an IST datetime for tests."""
    return datetime(year, month, day, hour, minute, tzinfo=IST)


# ----- is_weekend / is_trading_day / is_trading_holiday -----

def test_weekend_is_weekend():
    assert is_weekend(date(2026, 5, 16))   # Saturday
    assert is_weekend(date(2026, 5, 17))   # Sunday


def test_weekday_is_not_weekend():
    assert not is_weekend(date(2026, 5, 19))   # Tuesday


def test_known_holiday():
    assert is_trading_holiday(date(2026, 1, 26))   # Republic Day
    assert is_trading_holiday(date(2026, 8, 15))   # Independence Day


def test_normal_weekday_is_trading_day():
    assert is_trading_day(date(2026, 5, 19))   # Tuesday


def test_weekend_is_not_trading_day():
    assert not is_trading_day(date(2026, 5, 17))


def test_holiday_is_not_trading_day_even_on_weekday():
    # 2026-08-15 is a Saturday actually, find a weekday holiday
    holiday = date(2026, 1, 26)   # Republic Day, Monday
    assert holiday.weekday() < 5
    assert not is_trading_day(holiday)


# ----- is_market_open -----

def test_market_open_at_10am_weekday():
    assert is_market_open(at(2026, 5, 19, 10, 0))


def test_market_open_exact_open_boundary():
    assert is_market_open(at(2026, 5, 19, 9, 15))


def test_market_open_exact_close_boundary():
    assert is_market_open(at(2026, 5, 19, 15, 30))


def test_market_closed_before_open():
    assert not is_market_open(at(2026, 5, 19, 9, 0))   # pre-market, not market
    assert not is_market_open(at(2026, 5, 19, 9, 14))


def test_market_closed_after_close():
    assert not is_market_open(at(2026, 5, 19, 15, 31))
    assert not is_market_open(at(2026, 5, 19, 18, 0))


def test_market_closed_on_weekend_even_during_hours():
    assert not is_market_open(at(2026, 5, 16, 10, 0))   # Saturday 10am
    assert not is_market_open(at(2026, 5, 17, 12, 0))   # Sunday noon


def test_market_closed_on_holiday():
    assert not is_market_open(at(2026, 1, 26, 11, 0))   # Republic Day


# ----- is_pre_market_open -----

def test_pre_market_open_during_window():
    assert is_pre_market_open(at(2026, 5, 19, 9, 0))
    assert is_pre_market_open(at(2026, 5, 19, 9, 10))


def test_pre_market_closed_outside_window():
    assert not is_pre_market_open(at(2026, 5, 19, 8, 59))
    assert not is_pre_market_open(at(2026, 5, 19, 9, 15))   # market opens here


def test_pre_market_closed_on_holiday():
    assert not is_pre_market_open(at(2026, 1, 26, 9, 5))


# ----- defaults to now_ist() when no arg -----

def test_is_market_open_uses_current_time_when_no_arg():
    # Just verify it doesn't crash and returns a bool
    result = is_market_open()
    assert isinstance(result, bool)