"""Tests for the pure option analytics (Greeks, GEX, max-pain, PCR)."""
from __future__ import annotations

import datetime as _dt

from nse_data.options import greeks as g

_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))


def test_bs_greeks_atm_sanity():
    call = g.bs_greeks(100, 100, 0.25, 0.20, "CE")
    put = g.bs_greeks(100, 100, 0.25, 0.20, "PE")
    assert 0.50 < call["delta"] < 0.65 and -0.50 < put["delta"] < -0.35   # ATM, slight call tilt
    assert call["gamma"] > 0 and call["gamma"] == put["gamma"]             # gamma identical CE/PE
    assert call["vega"] > 0 and call["theta"] < 0                          # long option bleeds theta
    assert g.bs_greeks(100, 100, 0.25, 0, "CE") is None                    # zero IV → None


def test_gamma_exposure_sign_dealer_short_calls_long_puts():
    pe = [{"strike": 100, "option_type": "PE", "oi": 1000, "iv": 0.2, "t_years": 0.1}]
    ce = [{"strike": 100, "option_type": "CE", "oi": 1000, "iv": 0.2, "t_years": 0.1}]
    assert g.gamma_exposure(pe, 100)["gex_sign"] == "positive"   # dealer long puts → +γ
    assert g.gamma_exposure(ce, 100)["gex_sign"] == "negative"   # dealer short calls → −γ


def test_gamma_flip_found_between_dominant_sides():
    # puts dominate below, calls above → GEX flips sign somewhere around spot
    rows = [{"strike": 90, "option_type": "PE", "oi": 5000, "iv": 0.2, "t_years": 0.1},
            {"strike": 110, "option_type": "CE", "oi": 5000, "iv": 0.2, "t_years": 0.1}]
    out = g.gamma_exposure(rows, 100)
    assert out["gex_flip_level"] is None or 90 <= out["gex_flip_level"] <= 110


def test_max_pain_minimises_writer_payout():
    rows = [{"strike": 90, "option_type": "CE", "oi": 0}, {"strike": 90, "option_type": "PE", "oi": 0},
            {"strike": 100, "option_type": "CE", "oi": 1000}, {"strike": 100, "option_type": "PE", "oi": 1000},
            {"strike": 110, "option_type": "CE", "oi": 0}, {"strike": 110, "option_type": "PE", "oi": 0}]
    assert g.max_pain(rows) == 100.0          # all OI at 100 → settlement there pays out nothing


def test_put_call_ratio():
    rows = [{"strike": 100, "option_type": "CE", "oi": 1000},
            {"strike": 100, "option_type": "PE", "oi": 1300}]
    assert g.put_call_ratio(rows) == 1.3
    assert g.put_call_ratio([{"strike": 100, "option_type": "PE", "oi": 10}]) is None   # no calls


def test_days_to_expiry():
    ep = int(_dt.datetime(2026, 6, 20, 12, 0, tzinfo=_IST).timestamp())
    assert g.days_to_expiry("30-Jun-2026", ep) == 10
    assert g.days_to_expiry("bad-date", ep) is None


def test_pcr_and_max_pain_signals():
    assert g.pcr_signal(0.6) == "pcr_extreme_low"        # too many calls → contrarian bearish
    assert g.pcr_signal(1.4) == "pcr_extreme_high"       # too many puts → contrarian bullish
    assert g.pcr_signal(1.0) is None
    assert g.max_pain_drift(100, 103)["direction"] == "up"      # 3% gap → drift toward 103
    assert g.max_pain_drift(100, 100.5) is None                 # < 1.5% → no setup
