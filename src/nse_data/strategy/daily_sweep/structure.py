"""Step 1 — higher-timeframe trend from swing structure (+ helpers Steps 2 & 4 will reuse).

Thin wrapper over the shared, look-ahead-safe swing engine
(`indicators.trend.market_structure._structure_frame`): a swing high/low is a fractal confirmed
`k` bars each side, and trend = HH-HL (bullish) / LH-LL (bearish) / mixed. No new swing code —
the strategy and the live indicator share ONE implementation.
"""
from __future__ import annotations

import pandas as pd

from ...indicators.trend.market_structure import _structure_frame

_LABEL = {1.0: "bullish", -1.0: "bearish", 0.0: "mixed"}


def structure_frame(ohlcv: pd.DataFrame, k: int) -> pd.DataFrame:
    """OHLCV → per-bar swing_high, swing_low, structure(+1/-1/0). Pass-through to the
    shared engine with the strategy's configurable lookback."""
    return _structure_frame(ohlcv, k=k)


def trend_at(ohlcv: pd.DataFrame, k: int) -> dict:
    """Trend as of the LAST bar of `ohlcv`: {trend, structure, swing_high, swing_low}.
    `swing_high`/`swing_low` are the latest confirmed structural levels (the break lines)."""
    if ohlcv is None or len(ohlcv) < (2 * k + 1):
        return {"trend": "none", "structure": None, "swing_high": None, "swing_low": None}
    sf = _structure_frame(ohlcv, k=k)
    last = sf.iloc[-1]
    st = last["structure"]
    return {
        "trend": _LABEL.get(st, "none") if st is not None and not pd.isna(st) else "none",
        "structure": None if pd.isna(st) else int(st),
        "swing_high": None if pd.isna(last["swing_high"]) else float(last["swing_high"]),
        "swing_low": None if pd.isna(last["swing_low"]) else float(last["swing_low"]),
    }


def retracement_zone(h1: pd.DataFrame, *, trend: str, fib_min: float, fib_max: float, k: int,
                     daily_swing_low: float | None = None,
                     daily_swing_high: float | None = None) -> dict:
    """Step 2 — is 1H price inside the trend-aligned retracement zone?

    The zone is the 38.2%–79% Fibonacci band of the last 1H leg (swing_low→swing_high for an
    uptrend, mirror for a downtrend) — which also brackets the prior hourly swing / demand-supply.
    `in_zone` additionally requires the Daily structure to be intact (price hasn't broken the
    daily swing the trend rests on). Returns the band bounds + the leg for downstream use.
    """
    out = {"in_zone": False, "zone_low": None, "zone_high": None,
           "leg_low": None, "leg_high": None, "price": None}
    if h1 is None or len(h1) < (2 * k + 1) or trend not in ("bullish", "bearish"):
        return out
    sf = _structure_frame(h1, k=k)
    sh, sl = sf["swing_high"].iloc[-1], sf["swing_low"].iloc[-1]
    price = float(h1["close"].iloc[-1])
    out["price"], out["leg_high"], out["leg_low"] = price, (None if pd.isna(sh) else float(sh)), \
        (None if pd.isna(sl) else float(sl))
    if pd.isna(sh) or pd.isna(sl) or sh <= sl:
        return out
    rng = float(sh - sl)
    if trend == "bullish":
        zlo, zhi = float(sh) - fib_max * rng, float(sh) - fib_min * rng    # 79% (deep) … 38.2%
        intact = daily_swing_low is None or price > daily_swing_low
    else:
        zlo, zhi = float(sl) + fib_min * rng, float(sl) + fib_max * rng
        intact = daily_swing_high is None or price < daily_swing_high
    out["zone_low"], out["zone_high"] = zlo, zhi
    out["in_zone"] = bool(zlo <= price <= zhi and intact)
    return out


def bos_after(m5: pd.DataFrame, *, k: int, sweep_idx: int, direction: str,
              max_bars: int = 24) -> dict | None:
    """Step 4 — market-structure shift confirming a sweep. After a sweep at `sweep_idx`, the
    first bar to CLOSE through the structural level in place confirms BOS:

      bull — close > the swing high at the sweep (breaks the last lower high)
      bear — close < the swing low at the sweep (breaks the last higher low)

    Returns {bos_index, bos_time, broken_level, direction} or None within `max_bars`."""
    if sweep_idx < 0 or sweep_idx >= len(m5):
        return None
    sf = _structure_frame(m5, k=k)
    ref = sf["swing_high"].iloc[sweep_idx] if direction == "bull" else sf["swing_low"].iloc[sweep_idx]
    if pd.isna(ref):
        return None
    ref = float(ref)
    close = m5["close"]
    end = min(len(m5), sweep_idx + 1 + max_bars)
    for j in range(sweep_idx + 1, end):
        cl = float(close.iloc[j])
        if (direction == "bull" and cl > ref) or (direction == "bear" and cl < ref):
            return {"bos_index": j, "bos_time": m5.index[j], "broken_level": ref,
                    "direction": direction}
    return None


def daily_trend(conn, symbol: str, k: int, *, as_of: str | None = None) -> dict:
    """Step 1 entry point — load daily candles up to `as_of` and label the trend.
    `as_of` (YYYY-MM-DD) makes it point-in-time for the backtest (no look-ahead)."""
    from .data import read_daily
    daily = read_daily(conn, symbol, end=as_of)
    out = trend_at(daily, k)
    out["symbol"] = symbol
    out["as_of"] = (daily.index[-1].date().isoformat() if len(daily) else None)
    return out
