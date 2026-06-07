"""Unit tests for market.expiry — expiry weekdays + max-pain multiplier."""

from __future__ import annotations

from datetime import date

from nse_data.market import expiry

# June 2026: Jun 1 is a Monday, so Jun 9 = Tue, Jun 11 = Thu, Jun 25 = last Thu.
TUE = date(2026, 6, 9)
THU = date(2026, 6, 11)
LAST_THU = date(2026, 6, 25)
WED = date(2026, 6, 10)


# ---- weekly / monthly expiry (raw weekday rule, no holiday calendar) --------

def test_nifty_expiry_is_tuesday():
    assert expiry.is_nifty_expiry(TUE, holiday_adjust=False) is True
    assert expiry.is_nifty_expiry(THU, holiday_adjust=False) is False


def test_banknifty_expiry_is_thursday():
    assert expiry.is_banknifty_expiry(THU, holiday_adjust=False) is True
    assert expiry.is_banknifty_expiry(TUE, holiday_adjust=False) is False


def test_monthly_expiry_is_last_thursday():
    assert expiry.is_monthly_expiry(LAST_THU, holiday_adjust=False) is True
    assert expiry.is_monthly_expiry(THU, holiday_adjust=False) is False   # earlier Thu


def test_expiry_flags_shape():
    flags = expiry.expiry_flags(WED, holiday_adjust=False)
    assert flags == {
        "is_nifty_expiry": False,
        "is_banknifty_expiry": False,
        "is_monthly_expiry": False,
    }


# ---- max-pain alignment multiplier -----------------------------------------

def test_max_pain_aligned_gets_plus_five():
    # max_pain above spot -> expected drift UP -> an 'up' signal aligns
    assert expiry.max_pain_multiplier("long", max_pain=102.0, spot=100.0) == 5
    # max_pain below spot -> expected drift DOWN -> a 'down' signal aligns
    assert expiry.max_pain_multiplier("short", max_pain=98.0, spot=100.0) == 5


def test_max_pain_against_gets_minus_ten():
    assert expiry.max_pain_multiplier("long", max_pain=98.0, spot=100.0) == -10
    assert expiry.max_pain_multiplier("short", max_pain=102.0, spot=100.0) == -10


def test_max_pain_neutral_within_deadband():
    assert expiry.max_pain_multiplier("long", max_pain=100.05, spot=100.0) == 0


def test_max_pain_no_data_is_zero():
    assert expiry.max_pain_multiplier("long", None, 100.0) == 0
    assert expiry.max_pain_multiplier(None, 102.0, 100.0) == 0
    assert expiry.max_pain_multiplier("long", 102.0, None) == 0


def test_signal_type_strings_normalize():
    # the live signal_type tokens map to a direction
    assert expiry.max_pain_multiplier("breakout_52wh", 102.0, 100.0) == 5
    assert expiry.max_pain_multiplier("long_buildup", 98.0, 100.0) == -10
