"""
Full EOD indicator set (FEATURE_CHECKLIST Phase 4, Week 12, task 12.1).

EMA 9/21, Bollinger Bands (+ squeeze), ADX/DI, Supertrend, OBV, and 20-day
volume average/ratio — one Indicator computing them all into `indicator_eod`,
the same way SimpleMovingAverage packs its three windows into one table. Runs
nightly off bhavcopy (cadence="eod"); pandas-ta-classic for math parity with
the rest of the stack and the live engine.
"""

from __future__ import annotations

import pandas as pd
import pandas_ta_classic as ta

from .base import Indicator
from .trend.supertrend_calc import supertrend as _supertrend

_BB_LEN, _BB_STD = 20, 2.0
_ADX_LEN = 14
_ST_LEN, _ST_MULT = 10, 3.0   # TradingView default (was 2.0 until 2026-06)
_VOL_LEN = 20
_SQUEEZE_WINDOW = 252       # 1y of width history for the squeeze percentile
_SQUEEZE_PCT = 0.20


class EodFullSet(Indicator):
    name = "eod_full"
    table = "indicator_eod"
    pk_cols = ("symbol", "date")
    output_columns = (
        "ema9", "ema21", "bb_upper", "bb_lower", "bb_width", "bb_squeeze",
        "adx", "di_plus", "di_minus", "supertrend", "supertrend_dir",
        "obv", "vol_sma20", "volume_ratio",
    )
    # Mixed-scale table: only the price-level columns may ride the price axis.
    # Everything else gets a dedicated sub-pane (grouped where they belong
    # together) or stays API-only — see Indicator.column_panes.
    column_panes = {
        "bb_width": "bb_width",
        "bb_squeeze": "hidden",          # 0/1 flag — the bot reads it directly
        "adx": "adx", "di_plus": "adx", "di_minus": "adx",
        "obv": "obv",
        "vol_sma20": "volume",
        "volume_ratio": "volume_ratio",
    }
    # Squeeze needs a year of band-width history; that dominates the lookback.
    min_history = _SQUEEZE_WINDOW

    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        close, high, low, volume = (
            ohlcv["close"], ohlcv["high"], ohlcv["low"],
            # Coerce volume to numeric: some universe tickers (ETFs / index-like
            # symbols) carry non-numeric or empty volume in bhavcopy, which made
            # `volume / vol_sma` raise and abort the whole EOD sweep.
            pd.to_numeric(ohlcv["volume"], errors="coerce"),
        )
        out = pd.DataFrame(index=ohlcv.index)

        out["ema9"] = ta.ema(close, length=9)
        out["ema21"] = ta.ema(close, length=21)

        bb = ta.bbands(close, length=_BB_LEN, std=_BB_STD)
        if bb is not None and not bb.empty:
            sfx = f"_{_BB_LEN}_{_BB_STD}"
            upper, lower, mid = bb[f"BBU{sfx}"], bb[f"BBL{sfx}"], bb[f"BBM{sfx}"]
            out["bb_upper"], out["bb_lower"] = upper, lower
            width = (upper - lower) / mid
            out["bb_width"] = width
            floor = width.rolling(_SQUEEZE_WINDOW).quantile(_SQUEEZE_PCT)
            out["bb_squeeze"] = (width < floor).where(floor.notna()).astype("float")
        else:
            for c in ("bb_upper", "bb_lower", "bb_width", "bb_squeeze"):
                out[c] = pd.NA

        adx = ta.adx(high, low, close, length=_ADX_LEN)
        if adx is not None and not adx.empty:
            out["adx"] = adx[f"ADX_{_ADX_LEN}"]
            out["di_plus"] = adx[f"DMP_{_ADX_LEN}"]
            out["di_minus"] = adx[f"DMN_{_ADX_LEN}"]
        else:
            out["adx"] = out["di_plus"] = out["di_minus"] = pd.NA

        st = _supertrend(high, low, close, length=_ST_LEN, multiplier=_ST_MULT)
        out["supertrend"] = st["supertrend"]
        out["supertrend_dir"] = st["supertrend_dir"]

        out["obv"] = ta.obv(close, volume)
        vol_sma = ta.sma(volume, length=_VOL_LEN)
        out["vol_sma20"] = vol_sma
        out["volume_ratio"] = volume / vol_sma

        return out[list(self.output_columns)]
