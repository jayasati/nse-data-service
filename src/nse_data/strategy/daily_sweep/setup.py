"""Steps 6–9 — turn the detection primitives into complete, filtered trade setups.

The sequence (long; short mirrors):
  daily trend bullish (Step 1, point-in-time as of the PRIOR day — no look-ahead)
  → 5m bullish sweep (Step 3)
  → bullish BOS within `bos_max_bars` (Step 4)
  → bullish FVG forms on the impulse (Step 5)
  → ENTRY when price revisits the FVG (Step 6), inside a session window (Step 8)
  → SL below the sweep low, target by model A 1:3 (Step 7), sized to 1% risk
  with the day rejected on a > 2% gap or a blocked event day (Step 9).

Pure over the three timeframes' frames → a list of `SweepSetup`. The backtest (Step 10) resolves
each to a win/loss; the live scanner reuses the SAME function on the latest bars.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd

from ...indicators.trend.market_structure import _structure_frame
from .config import DailySweepConfig
from .fvg import detect_fvgs
from .sweep import detect_sweeps


@dataclass
class SweepSetup:
    symbol: str
    direction: str            # 'long' | 'short'
    daily_trend: str
    sweep_time: pd.Timestamp
    swept_level: float
    sweep_extreme: float      # the wick low (long) / high (short) → the stop
    bos_time: pd.Timestamp
    fvg_low: float
    fvg_high: float
    entry_time: pd.Timestamp
    entry_price: float
    stop: float
    target: float
    qty: int
    risk_rupees: float
    rr: float


def in_session(ts: pd.Timestamp, sessions) -> bool:
    """Step 8 — is `ts` inside one of the allowed IST entry windows?"""
    t = ts.tz_convert("Asia/Kolkata").time() if ts.tzinfo else ts.time()
    for lo, hi in sessions:
        a = dt.time(*map(int, lo.split(":")))
        b = dt.time(*map(int, hi.split(":")))
        if a <= t <= b:
            return True
    return False


def _daily_state(daily: pd.DataFrame, k: int):
    """Per-day (trend, swing_high, swing_low) confirmed at that day's close, + a prior-day lookup."""
    ds = _structure_frame(daily, k=k)
    rows = []
    for i in range(len(daily)):
        st = ds["structure"].iloc[i]
        trend = "bullish" if st == 1 else "bearish" if st == -1 else "mixed"
        rows.append((daily.index[i].date(), trend,
                     ds["swing_high"].iloc[i], ds["swing_low"].iloc[i]))

    def prior(date):
        p = [r for r in rows if r[0] < date]
        return p[-1] if p else None
    return prior


def _daily_gap_pct(daily: pd.DataFrame) -> dict:
    """date → open-vs-prior-close gap %. Step 9 rejects days that gapped too far."""
    out = {}
    for i in range(1, len(daily)):
        pc = daily["close"].iloc[i - 1]
        if pc:
            out[daily.index[i].date()] = (daily["open"].iloc[i] - pc) / pc * 100.0
    return out


def scan_setups(daily: pd.DataFrame, h1: pd.DataFrame, m5: pd.DataFrame, *,
                config: DailySweepConfig, symbol: str,
                blocked_dates: set | None = None) -> list[SweepSetup]:
    setups: list[SweepSetup] = []
    if daily is None or daily.empty or m5 is None or len(m5) < 50:
        return setups
    blocked = blocked_dates or set()
    prior_trend = _daily_state(daily, config.daily_swing_k)
    gap = _daily_gap_pct(daily)

    sw = detect_sweeps(m5, swing_k=config.m5_swing_k, atr_len=config.atr_len,
                       vol_ma_len=config.vol_ma_len, min_pct=config.sweep_min_pct,
                       min_atr=config.sweep_min_atr)
    sf = _structure_frame(m5, k=config.m5_swing_k)
    fv = detect_fvgs(m5)
    close, low, high = m5["close"], m5["low"], m5["high"]
    n = len(m5)
    used_until = -1

    for i in range(n):
        sdir = sw["sweep_dir"].iloc[i]            # 'bull' | 'bear' | None
        if sdir is None or i <= used_until:
            continue
        date = m5.index[i].date()
        ds = prior_trend(date)
        if ds is None:
            continue
        dtrend = ds[1]
        if (sdir == "bull" and dtrend != "bullish") or (sdir == "bear" and dtrend != "bearish"):
            continue                              # Step 6: must align with the daily trend
        if config.block_event_days and date in blocked:
            continue
        if abs(gap.get(date, 0.0)) > config.max_gap_pct:   # Step 9: gap filter
            continue

        # Step 4: BOS — first close through the structural swing in place at the sweep
        ref = sf["swing_high"].iloc[i] if sdir == "bull" else sf["swing_low"].iloc[i]
        if pd.isna(ref):
            continue
        ref = float(ref)
        j = next((b for b in range(i + 1, min(n, i + 1 + config.bos_max_bars))
                  if (sdir == "bull" and close.iloc[b] > ref)
                  or (sdir == "bear" and close.iloc[b] < ref)), None)
        if j is None:
            continue

        # Step 5: aligned FVG on the impulse (between the sweep and just past the BOS)
        want = "bull" if sdir == "bull" else "bear"
        fidx = next((k for k in range(i, min(n, j + config.fvg_search_bars))
                     if fv["fvg_dir"].iloc[k] == want), None)
        if fidx is None:
            continue
        glo, ghi = float(fv["gap_low"].iloc[fidx]), float(fv["gap_high"].iloc[fidx])

        # Step 6: entry on the first revisit into the FVG, within the wait window
        entry_idx = entry_px = None
        for m in range(fidx + 1, min(n, fidx + 1 + config.entry_wait_bars)):
            if sdir == "bull" and low.iloc[m] <= ghi:
                entry_idx, entry_px = m, ghi
                break
            if sdir == "bear" and high.iloc[m] >= glo:
                entry_idx, entry_px = m, glo
                break
        if entry_idx is None:
            continue
        entry_ts = m5.index[entry_idx]
        if not in_session(entry_ts, config.sessions):          # Step 8
            continue

        # Step 7: SL = sweep extreme; target = model A (1:3); size to 1% risk
        sweep_extreme = float(low.iloc[i]) if sdir == "bull" else float(high.iloc[i])
        risk = entry_px - sweep_extreme if sdir == "bull" else sweep_extreme - entry_px
        if risk <= entry_px * config.min_stop_pct:    # too tight → noise / oversized qty → skip
            continue
        target = entry_px + config.rr_target * risk if sdir == "bull" \
            else entry_px - config.rr_target * risk
        qty = int((config.capital * config.risk_pct / 100.0) / risk)
        if qty < 1:
            continue

        setups.append(SweepSetup(
            symbol=symbol, direction="long" if sdir == "bull" else "short", daily_trend=dtrend,
            sweep_time=m5.index[i], swept_level=float(sw["swept_level"].iloc[i]),
            sweep_extreme=sweep_extreme, bos_time=m5.index[j], fvg_low=glo, fvg_high=ghi,
            entry_time=entry_ts, entry_price=float(entry_px), stop=sweep_extreme,
            target=float(target), qty=qty, risk_rupees=round(qty * risk, 2), rr=config.rr_target))
        used_until = entry_idx                    # one setup per resolved sweep
    return setups
