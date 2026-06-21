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


def daily_trend(conn, symbol: str, k: int, *, as_of: str | None = None) -> dict:
    """Step 1 entry point — load daily candles up to `as_of` and label the trend.
    `as_of` (YYYY-MM-DD) makes it point-in-time for the backtest (no look-ahead)."""
    from .data import read_daily
    daily = read_daily(conn, symbol, end=as_of)
    out = trend_at(daily, k)
    out["symbol"] = symbol
    out["as_of"] = (daily.index[-1].date().isoformat() if len(daily) else None)
    return out
