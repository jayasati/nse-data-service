"""Buy Decision Engine — integrates the validated factor engines into a single,
explainable per-stock decision card (grand-prompt v2). NOT a new factor: it COMBINES
the existing point-in-time engine scores with regime-adaptive weights, reads the
factor_snapshot history for score velocity/acceleration, classifies the setup, and
emits a Buy/Hold/Reduce/Exit verdict with drivers.

The crucial fix it encodes (the HDFCBANK value-trap lesson): Opportunity (Quality+
Value) is price-blind and RISES as a good stock gets cheaper — so the Buy Score
GATES on Trend (momentum). Cheap + high-quality + broken-trend = value trap, NOT a buy.

Weights here are documented heuristics, regime-adaptive but NOT yet backtest-optimized
— this is a decision/explainability layer; the validated tradeable edge remains the
Q+V(+Mom) backtests. Marked clearly in the output.
"""
from __future__ import annotations

import datetime as _dt

_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))


def _eod_ep(as_of_date: str) -> int:
    d = _dt.date.fromisoformat(as_of_date)
    return int(_dt.datetime(d.year, d.month, d.day, 15, 35, tzinfo=_IST).timestamp())


# Buy-Score components (0-100) drawn from the engines. Risk is handled separately
# (a gate/multiplier, higher=safer), not a positive additive component.
COMPONENTS = ("opportunity", "trend", "catalyst", "expectation", "turnaround", "liquidity", "sector_flow")

# Engine-3 Sector Rotation: map our fundamental sector_class → the NSE sectoral index
# tracked in sector_state, so a stock inherits its sector's relative-strength flow.
SECTOR_INDEX = {
    "bfsi": "NIFTY BANK", "nbfc": "NIFTY BANK", "it": "NIFTY IT", "pharma": "NIFTY PHARMA",
    "fmcg": "NIFTY FMCG", "metals": "NIFTY METAL", "auto": "NIFTY AUTO",
    "energy": "NIFTY ENERGY", "capgoods": "NIFTY INFRA", "realty": "NIFTY REALTY",
}


def sector_flow_score(conn, sector_class: str | None, as_of_ep: int) -> float | None:
    """0-100 from the stock's sector RS rank (1=strongest) in sector_state, ± a trend
    nudge. None for sectors without a tracked index (capmarkets/generic) or no data."""
    idx = SECTOR_INDEX.get(sector_class or "")
    if not idx:
        return None
    iso = _dt.datetime.fromtimestamp(as_of_ep, _IST).isoformat()
    r = conn.execute(
        "SELECT rs_rank, rs_trend, (SELECT COUNT(DISTINCT sector_name) FROM sector_state s2 "
        "  WHERE s2.as_of=(SELECT MAX(as_of) FROM sector_state s3 WHERE s3.as_of<=?)) "
        "FROM sector_state WHERE sector_name=? AND as_of<=? ORDER BY as_of DESC LIMIT 1",
        (iso, idx, iso)).fetchone()
    if not r or r[0] is None:
        return None
    rank, trend, n = r[0], r[1], (r[2] or 11)
    base = 100.0 * (n - rank) / max(1, n - 1)              # rank 1 → 100, rank n → 0
    if trend == "improving":
        base = min(100.0, base + 10)
    elif trend == "deteriorating":
        base = max(0.0, base - 10)
    return round(base, 1)

# Regime-adaptive weights (sum need not be 1; normalised over AVAILABLE components).
# Bull → reward trend/catalyst; Bear/Panic → lean on quality-value + de-emphasise trend
# chasing; Neutral → balanced. Keys match market_state.overall_regime (lowercased).
REGIME_WEIGHTS = {
    "strong_bull": {"opportunity": 0.9, "trend": 1.5, "catalyst": 1.2, "expectation": 1.0, "turnaround": 0.8, "liquidity": 0.4, "sector_flow": 0.8},
    "bull":        {"opportunity": 1.0, "trend": 1.3, "catalyst": 1.1, "expectation": 1.0, "turnaround": 0.8, "liquidity": 0.4, "sector_flow": 0.7},
    "neutral":     {"opportunity": 1.2, "trend": 1.0, "catalyst": 1.0, "expectation": 1.0, "turnaround": 1.0, "liquidity": 0.5, "sector_flow": 0.6},
    "bear":        {"opportunity": 1.5, "trend": 0.7, "catalyst": 0.8, "expectation": 0.9, "turnaround": 1.1, "liquidity": 0.7, "sector_flow": 0.5},
    "panic":       {"opportunity": 1.6, "trend": 0.5, "catalyst": 0.6, "expectation": 0.8, "turnaround": 1.0, "liquidity": 1.0, "sector_flow": 0.4},
}


def _opportunity(f: dict):
    """Quality + Valuation mean = business attractiveness (the validated composite)."""
    vals = [f[k] for k in ("quality", "valuation") if f.get(k) is not None]
    return sum(vals) / len(vals) if vals else None


def _components(f: dict) -> dict:
    return {
        "opportunity": _opportunity(f),
        "trend": f.get("momentum"),
        "catalyst": f.get("catalyst"),
        "expectation": f.get("surprise"),
        "turnaround": f.get("turnaround"),
        "liquidity": f.get("liquidity"),
        "sector_flow": f.get("sector_flow"),
    }


def buy_raw(f: dict, weights: dict) -> tuple[float | None, dict]:
    """Regime-weighted mean over AVAILABLE components, then a Risk multiplier
    (higher=safer → less haircut). Returns (buy_score, component_contributions)."""
    comp = _components(f)
    num = den = 0.0
    contrib = {}
    for k in COMPONENTS:
        v, w = comp[k], weights.get(k, 1.0)
        if v is not None:
            num += v * w
            den += w
            contrib[k] = round(v, 1)
    if den == 0:
        return None, contrib
    base = num / den
    risk = f.get("risk")
    # Risk gate as a multiplier: safe(100)→×1.0, neutral(60)→×0.9, dangerous(20)→×0.7.
    mult = 1.0 if risk is None else (0.6 + 0.4 * risk / 100.0)
    return round(base * mult, 1), contrib


def classify(f: dict) -> str:
    """Investment classification from the factor profile (price-aware via trend)."""
    opp, trend = _opportunity(f), f.get("momentum")
    q, val, turn, risk = f.get("quality"), f.get("valuation"), f.get("turnaround"), f.get("risk")
    if risk is not None and risk < 35:
        return "High Risk Speculation"
    if (q or 0) >= 65 and (trend or 0) >= 60 and (risk or 100) >= 55:
        return "Compounder"
    if (trend or 0) >= 70 and (opp or 0) >= 50:
        return "Momentum Leader"
    if (turn or 0) >= 70:
        return "Turnaround Candidate"
    if (val or 0) >= 65 and (trend or 100) < 40:
        return "Deep Value (weak trend — value-trap risk)"
    if (val or 0) >= 60:
        return "Deep Value"
    return "Neutral / No Edge"


def verdict(buy, f: dict, velocity, regime: str | None, confidence) -> tuple[str, list, list]:
    """Buy/Hold/Reduce/Exit + (positive drivers, negative drivers). Encodes the v2
    BUY rules and the value-trap gate: a strong Opportunity with a broken Trend is
    NOT a buy."""
    opp, trend = _opportunity(f), f.get("momentum")
    risk = f.get("risk")
    pos, neg = [], []
    comp = _components(f)
    for k, v in comp.items():
        if v is None:
            continue
        (pos if v >= 60 else neg if v <= 40 else pos if v >= 50 else neg).append((k, v))
    pos = [f"{k} {v:.0f}" for k, v in sorted(pos, key=lambda x: -x[1])[:4]]
    neg = [f"{k} {v:.0f}" for k, v in sorted(neg, key=lambda x: x[1])[:4]]
    if risk is not None and risk < 40:
        neg.append(f"risk {risk:.0f} (elevated)")

    panic = regime in ("panic",)
    # BUY: opportunity strong AND market agrees (trend) AND safe AND confident AND no panic.
    if (opp or 0) >= 65 and (trend or 0) >= 60 and (risk or 0) >= 55 and (confidence or 0) >= 60 and not panic:
        return ("STRONG BUY" if (buy or 0) >= 75 else "BUY"), pos, neg
    # Value-trap / falling-knife guard: cheap+quality but trend broken → do NOT buy.
    if (opp or 0) >= 60 and (trend or 100) < 40:
        return "AVOID — value-trap risk (cheap/quality but trend broken)", pos, neg
    if velocity is not None and velocity <= -8:
        return "REDUCE — score deteriorating fast", pos, neg
    if (trend or 100) < 30 or (risk is not None and risk < 35):
        return "EXIT / AVOID — trend or risk failing", pos, neg
    return "HOLD", pos, neg


def assemble_card(conn, symbol: str, f: dict, regime: str | None, vol_ann_pct: float | None,
                  as_of_date: str, horizon: int = 60) -> dict:
    """Full decision card dict from a factor row `f` (engine scores + sector/sector_rank).
    Used by both the CLI (f from a live universe rescore) and the web service (f from
    the latest factor_snapshot row) so the verdict is identical either way."""
    weights = REGIME_WEIGHTS.get((regime or "neutral").lower(), REGIME_WEIGHTS["neutral"])
    # Engine-3 sector flow is sector-level (not stored per-stock) → injected live from
    # the stock's sector so the current Buy Score reflects sector rotation.
    if f.get("sector_flow") is None:
        f = {**f, "sector_flow": sector_flow_score(conn, f.get("sector"), _eod_ep(as_of_date))}
    buy, contrib = buy_raw(f, weights)
    vel, accel, hist_n = velocity_accel(conn, symbol, weights, as_of_date)
    cls = classify(f)
    conf = f.get("confidence")
    action, pos, neg = verdict(buy, f, vel, regime, conf)

    # Engine-1 macro overlay: a great stock is still a poor buy in a panic.
    from . import macro_engine
    macro = macro_engine.macro_risk(conn, _eod_ep(as_of_date))
    if macro["state"] == "Panic":
        if action.startswith(("BUY", "STRONG")):
            action = "HOLD — macro Panic (stand aside)"
        neg = neg + [f"macro {macro['state']} (VIX {macro.get('vix')})"]
    elif macro["state"] == "Risk Off" and action.startswith(("BUY", "STRONG")):
        action = "BUY (cautious — macro Risk Off)"

    # Engine-6 News Intelligence — DISPLAY-ONLY (can't be backtested yet, ~2.5mo of
    # announcement history): structured recent events, not folded into the Buy Score.
    from . import news_engine
    news = news_engine.news_raw(conn, symbol, _eod_ep(as_of_date))
    if news["top_pos"]:
        pos = pos + [f"news:{news['top_pos'][0]}"]
    if news["top_neg"] and news["news_risk"] < 75:        # only surface a material bad-news flag
        neg = neg + [f"news:{news['top_neg'][0]}"]

    annv = (vol_ann_pct if vol_ann_pct else 30.0) / 100.0
    sig = annv * (horizon / 252.0) ** 0.5 * 100                # ~1σ move over horizon (%)
    edge = ((buy or 50) - 50) / 50.0
    exp_dd = -round(sig * (1.2 - 0.4 * max(0, edge)), 1)
    exp_up = round(sig * max(0.0, edge) * 1.8, 1)
    rr = round(exp_up / abs(exp_dd), 2) if exp_dd else None
    buyable = action.startswith(("BUY", "STRONG"))
    macro_mult = 0.5 if macro["state"] == "Risk Off" else 0.0 if macro["state"] == "Panic" else 1.0
    alloc = round(max(0.0, ((buy or 0) - 50) / 5.0) * (conf or 60) / 100.0 * macro_mult, 1) if buyable else 0.0

    return {
        "symbol": symbol, "as_of": as_of_date, "sector": f.get("sector"),
        "regime": regime, "macro": macro, "weights": {k: weights[k] for k in COMPONENTS},
        "news": {"score": news["news_score"], "risk": news["news_risk"],
                 "n_events": news["n_events"],
                 "top_pos": news["top_pos"][0] if news["top_pos"] else None,
                 "top_neg": news["top_neg"][0] if news["top_neg"] else None},
        "factors": {k: f.get(k) for k in ("quality", "valuation", "momentum", "surprise",
                    "catalyst", "turnaround", "liquidity", "risk", "confidence", "sector_flow")},
        "sector_rank": f.get("sector_rank"), "sector_n": f.get("sector_n"),
        "opportunity": _opportunity(f), "buy_score": buy, "contributions": contrib,
        "velocity": vel, "acceleration": accel, "history_days": hist_n,
        "confidence": conf, "classification": cls, "verdict": action,
        "drivers_positive": pos, "drivers_negative": neg,
        "expected_upside_pct": exp_up, "expected_drawdown_pct": exp_dd,
        "reward_risk": rr, "suggested_allocation_pct": alloc, "horizon": horizon,
    }


def velocity_accel(conn, symbol: str, weights: dict, as_of_date: str, lookback=20) -> tuple:
    """Score velocity (Δ buy-score over ~lookback trading snapshots) + acceleration,
    recomputed from the STORED factor_snapshot history (point-in-time)."""
    # catalyst is not a stored snapshot column (engine not in the snapshot pipeline) →
    # absent here, treated as a missing component by buy_raw.
    cols = "snapshot_date, quality, valuation, momentum, surprise, turnaround, liquidity, risk"
    rows = conn.execute(
        f"SELECT {cols} FROM factor_snapshot WHERE symbol=? AND snapshot_date<=? "
        "ORDER BY snapshot_date", (symbol, as_of_date)).fetchall()
    if len(rows) < 2 * lookback:
        return None, None, len(rows)
    keys = ["quality", "valuation", "momentum", "surprise", "turnaround", "liquidity", "risk"]
    series = [buy_raw(dict(zip(keys, r[1:])), weights)[0] for r in rows]
    series = [s for s in series if s is not None]
    if len(series) < 2 * lookback:
        return None, None, len(rows)
    now, prev, prev2 = series[-1], series[-1 - lookback], series[-1 - 2 * lookback]
    vel = round(now - prev, 1)
    accel = round((now - prev) - (prev - prev2), 1)
    return vel, accel, len(rows)
