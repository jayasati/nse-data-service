"""Option Greeks + GEX + max-pain + PCR (FEATURE_CHECKLIST Week 23, tasks 23.1–23.4/23.7).

Pure, dependency-free analytics over an option chain (no `mibian` — Black-Scholes Greeks are
a few lines of `math`, easier to test and audit). All functions take plain rows so they unit-
test without a DB; the job layer (`options/job.py`) reads `raw_option_chain` and persists.

  bs_greeks         Black-Scholes delta/gamma/theta/vega for one option
  gamma_exposure    net DEALER gamma exposure (GEX) + sign + gamma-flip level (23.3)
                      dealer is SHORT calls (−γ·OI) and LONG puts (+γ·OI); total GEX > 0 ⇒
                      mean-reverting tape, < 0 ⇒ trending tape
  max_pain          the settlement price that minimises total option-holder payout (23.4)
  put_call_ratio    ΣPE-OI / ΣCE-OI (23.7)
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

_IST = timezone(timedelta(hours=5, minutes=30))

_RISK_FREE = 0.065        # Indian ~10y G-sec proxy (task 23.2)
_MIN_T = 0.5 / 365.0      # floor time-to-expiry at half a day (avoid div-by-zero on expiry)


def _N(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _n(x: float) -> float:
    return math.exp(-x * x / 2.0) / math.sqrt(2.0 * math.pi)


def days_to_expiry(expiry: str, as_of_epoch: int) -> int | None:
    """Calendar days from the chain snapshot to the NSE-format expiry ('DD-Mon-YYYY')."""
    try:
        exp = datetime.strptime(expiry, "%d-%b-%Y").date()
    except (ValueError, TypeError):
        return None
    asof = datetime.fromtimestamp(as_of_epoch, _IST).date()
    return max((exp - asof).days, 0)


def bs_greeks(spot: float, strike: float, t_years: float, iv: float,
              opt_type: str, rate: float = _RISK_FREE) -> dict | None:
    """Black-Scholes Greeks. `iv` in decimal (0.30 = 30%). None on bad inputs."""
    if not (spot > 0 and strike > 0 and iv > 0):
        return None
    t = max(t_years, _MIN_T)
    sqrt_t = math.sqrt(t)
    d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * t) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    gamma = _n(d1) / (spot * iv * sqrt_t)
    vega = spot * _n(d1) * sqrt_t / 100.0                              # per 1% IV move
    if opt_type == "CE":
        delta = _N(d1)
        theta = (-(spot * _n(d1) * iv) / (2 * sqrt_t)
                 - rate * strike * math.exp(-rate * t) * _N(d2)) / 365.0
    else:
        delta = _N(d1) - 1.0
        theta = (-(spot * _n(d1) * iv) / (2 * sqrt_t)
                 + rate * strike * math.exp(-rate * t) * _N(-d2)) / 365.0
    return {"delta": round(delta, 4), "gamma": round(gamma, 8),
            "theta": round(theta, 4), "vega": round(vega, 4)}


def _total_gex_at(rows: list[dict], spot: float, lot_size: float, rate: float) -> float:
    """Σ dealer gamma exposure across strikes at a given spot (dealer −CE / +PE)."""
    tot = 0.0
    for r in rows:
        g = bs_greeks(spot, r["strike"], r["t_years"], r["iv"], r["option_type"], rate)
        if not g:
            continue
        v = g["gamma"] * (r["oi"] or 0) * lot_size * spot
        tot += -v if r["option_type"] == "CE" else v
    return tot


def gamma_exposure(rows: list[dict], spot: float, *, lot_size: float = 1.0,
                   rate: float = _RISK_FREE) -> dict | None:
    """Net dealer GEX, its sign, and the gamma-flip spot (where total GEX crosses 0).

    rows: dicts with strike / option_type ('CE'|'PE') / oi / iv(decimal) / t_years.
    sign/flip are lot-size independent; the magnitude scales with lot_size.
    """
    if not rows or spot <= 0:
        return None
    total = _total_gex_at(rows, spot, lot_size, rate)
    # gamma flip: scan ±10% of spot for the zero-crossing of total GEX(S)
    flip = None
    prev = prev_s = None
    s = spot * 0.9
    while s <= spot * 1.1:
        g = _total_gex_at(rows, s, lot_size, rate)
        if prev is not None and (prev < 0) != (g < 0):
            flip = round((prev_s + s) / 2.0, 2)
            break
        prev, prev_s = g, s
        s += spot * 0.005
    return {"gex_total": round(total, 2),
            "gex_sign": "positive" if total >= 0 else "negative",
            "gex_flip_level": flip}


def max_pain(rows: list[dict]) -> float | None:
    """Settlement strike minimising total writer payout (task 23.4). rows need strike/option_type/oi."""
    strikes = sorted({r["strike"] for r in rows})
    if not strikes:
        return None
    ce = {r["strike"]: (r["oi"] or 0) for r in rows if r["option_type"] == "CE"}
    pe = {r["strike"]: (r["oi"] or 0) for r in rows if r["option_type"] == "PE"}
    best = best_loss = None
    for settle in strikes:
        loss = (sum(max(0.0, settle - k) * ce.get(k, 0) for k in strikes)
                + sum(max(0.0, k - settle) * pe.get(k, 0) for k in strikes))
        if best_loss is None or loss < best_loss:
            best, best_loss = settle, loss
    return best


def put_call_ratio(rows: list[dict]) -> float | None:
    """ΣPE-OI / ΣCE-OI (task 23.7). None if no calls."""
    ce = sum((r["oi"] or 0) for r in rows if r["option_type"] == "CE")
    pe = sum((r["oi"] or 0) for r in rows if r["option_type"] == "PE")
    return round(pe / ce, 2) if ce > 0 else None


def pcr_signal(pcr: float | None) -> str | None:
    """Contrarian PCR extremes (task 23.7). Low PCR = too many calls = contrarian bearish."""
    if pcr is None:
        return None
    if pcr < 0.7:
        return "pcr_extreme_low"
    if pcr > 1.3:
        return "pcr_extreme_high"
    return None


def max_pain_drift(spot: float | None, mp: float | None, *, min_gap_pct: float = 1.5) -> dict | None:
    """Drift-toward-max-pain setup (task 23.6): spot far enough from max pain to drift to it."""
    if not spot or not mp or spot <= 0:
        return None
    gap = (mp - spot) / spot * 100.0
    if abs(gap) >= min_gap_pct:
        return {"direction": "up" if gap > 0 else "down",
                "gap_pct": round(gap, 2), "max_pain": mp}
    return None
