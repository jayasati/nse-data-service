"""Step 3 — 5-minute liquidity sweep detection.

A sweep is a stop-raid + rejection: a bar pierces a recent swing level (the resting liquidity)
but CLOSES back through it, with enough penetration and volume to be real:

  Bullish — low < recent swing low AND close > that swing low
  Bearish — high > recent swing high AND close < that swing high
  + penetration > max(0.1% × price, 0.25 × ATR(14))   (spec thresholds, from config)
  + volume > 20-bar average volume

Pure + vectorised over the frame (one row per bar) so it serves both the backtest scan and the
live "is the last bar a sweep?" check. Swing levels come from the SHARED swing engine
(`_structure_frame`), look-ahead-safe (the swept swing is confirmed ≥k bars before the sweep bar).
"""
from __future__ import annotations

import pandas as pd
import pandas_ta_classic as ta

from ...indicators.trend.market_structure import _structure_frame


def detect_sweeps(m5: pd.DataFrame, *, swing_k: int, atr_len: int = 14, vol_ma_len: int = 20,
                  min_pct: float = 0.001, min_atr: float = 0.25, require_both: bool = False
                  ) -> pd.DataFrame:
    """Per-bar sweep tags. Columns: sweep_dir ('bull'|'bear'|None), swept_level, penetration.

    Threshold = the spec's "0.1% OR 0.25·ATR": qualifies if penetration clears EITHER floor
    (`min`, default), so a volatility-appropriate sweep counts even on a high-priced index
    where 0.1% is a large move. `require_both=True` uses the stricter `max` (clear both)."""
    n = len(m5)
    out = pd.DataFrame(index=m5.index)
    out["sweep_dir"] = pd.Series([None] * n, index=m5.index, dtype="object")
    out["swept_level"] = pd.array([None] * n, dtype="float")
    out["penetration"] = pd.array([None] * n, dtype="float")
    if n < max(2 * swing_k + 1, atr_len + 1, vol_ma_len):
        return out

    sf = _structure_frame(m5, k=swing_k)
    atr = ta.atr(m5["high"], m5["low"], m5["close"], length=atr_len)
    volma = m5["volume"].rolling(vol_ma_len).mean()
    low, high, close, vol = m5["low"], m5["high"], m5["close"], m5["volume"]
    # Index SPOT (NIFTY/BANKNIFTY/FINNIFTY) carries no volume — the volume confirmation can't
    # apply, so skip it for volume-less instruments (futures volume is a later refinement).
    has_volume = float(vol.sum()) > 0

    for i in range(n):
        sl, sh = sf["swing_low"].iloc[i], sf["swing_high"].iloc[i]
        a = atr.iloc[i] if atr is not None else None
        pct_floor = close.iloc[i] * min_pct
        atr_floor = (float(a) * min_atr) if pd.notna(a) else None
        if atr_floor is None:
            thr = pct_floor
        else:
            thr = max(pct_floor, atr_floor) if require_both else min(pct_floor, atr_floor)
        vm = volma.iloc[i]
        vol_ok = (not has_volume) or (pd.notna(vm) and vol.iloc[i] > vm)
        if not vol_ok:
            continue
        if pd.notna(sl) and low.iloc[i] < sl and close.iloc[i] > sl and (sl - low.iloc[i]) > thr:
            out.iloc[i, out.columns.get_loc("sweep_dir")] = "bull"
            out.iloc[i, out.columns.get_loc("swept_level")] = float(sl)
            out.iloc[i, out.columns.get_loc("penetration")] = float(sl - low.iloc[i])
        elif pd.notna(sh) and high.iloc[i] > sh and close.iloc[i] < sh and (high.iloc[i] - sh) > thr:
            out.iloc[i, out.columns.get_loc("sweep_dir")] = "bear"
            out.iloc[i, out.columns.get_loc("swept_level")] = float(sh)
            out.iloc[i, out.columns.get_loc("penetration")] = float(high.iloc[i] - sh)
    return out
