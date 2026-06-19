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

# Buy-Score components (0-100) drawn from the engines. Risk is handled separately
# (a gate/multiplier, higher=safer), not a positive additive component.
COMPONENTS = ("opportunity", "trend", "catalyst", "expectation", "turnaround", "liquidity")

# Regime-adaptive weights (sum need not be 1; normalised over AVAILABLE components).
# Bull → reward trend/catalyst; Bear/Panic → lean on quality-value + de-emphasise trend
# chasing; Neutral → balanced. Keys match market_state.overall_regime (lowercased).
REGIME_WEIGHTS = {
    "strong_bull": {"opportunity": 0.9, "trend": 1.5, "catalyst": 1.2, "expectation": 1.0, "turnaround": 0.8, "liquidity": 0.4},
    "bull":        {"opportunity": 1.0, "trend": 1.3, "catalyst": 1.1, "expectation": 1.0, "turnaround": 0.8, "liquidity": 0.4},
    "neutral":     {"opportunity": 1.2, "trend": 1.0, "catalyst": 1.0, "expectation": 1.0, "turnaround": 1.0, "liquidity": 0.5},
    "bear":        {"opportunity": 1.5, "trend": 0.7, "catalyst": 0.8, "expectation": 0.9, "turnaround": 1.1, "liquidity": 0.7},
    "panic":       {"opportunity": 1.6, "trend": 0.5, "catalyst": 0.6, "expectation": 0.8, "turnaround": 1.0, "liquidity": 1.0},
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
