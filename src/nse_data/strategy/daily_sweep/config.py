"""Daily Sweep strategy — all rule parameters in one place (no magic numbers in logic).

Every threshold from the spec lives here so backtest sweeps + live both read the same config.
Defaults match the spec; the backtester's walk-forward (Step 12) varies these.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DailySweepConfig:
    # --- Step 1: swing/trend (fractal lookback, bars each side) ---
    daily_swing_k: int = 5          # spec default 5 left / 5 right
    h1_swing_k: int = 5
    m5_swing_k: int = 3             # tighter on 5m (faster structure)

    # --- Step 2: 1H retracement zone ---
    fib_min: float = 0.382          # 38.2%–79% retracement band
    fib_max: float = 0.79
    require_h1_retracement: bool = True   # Step 2 gate: entry inside the 1H fib band …
    require_daily_structure_intact: bool = True   # … AND retracement hasn't broken the daily swing
    require_h1_demand_zone: bool = True    # … AND entry sits in an aligned 1H FVG (demand/supply)

    # --- Step 6: sequencing windows (5m bars) ---
    bos_max_bars: int = 24          # BOS must confirm within ~2h of the sweep
    entry_wait_bars: int = 24       # FVG must be revisited within ~2h of forming
    fvg_search_bars: int = 6        # the BOS-impulse FVG forms within a few bars of the sweep

    # --- Step 3: 5m liquidity sweep ---
    sweep_min_pct: float = 0.001    # 0.1% of price …
    sweep_min_atr: float = 0.25     # … OR 0.25 × ATR(14) — whichever is larger
    atr_len: int = 14
    vol_ma_len: int = 20            # volume must exceed this many-bar average

    # --- Step 7: risk ---
    risk_pct: float = 1.0           # 1% of capital per trade (risk-based qty, before the cap)
    max_alloc_per_trade: float = 40_000.0   # capital (margin) allocated per trade
    leverage: float = 5.0                   # intraday leverage → position value = alloc × leverage
                                            # (₹40k × 5 = ₹200k notional); P&L is on the ₹40k margin
    min_stop_pct: float = 0.0015    # skip if the sweep stop is < 0.15% away (noise → oversized qty)
    rr_target: float = 3.0          # Target Model A: 1:3
    partial_rr: float = 2.0         # Target Model C: 50% at 1:2, trail rest
    trail_atr_mult: float = 1.5     # ATR trail for the runner

    # --- Step 8: session windows (IST, "HH:MM") ---
    sessions: tuple[tuple[str, str], ...] = (("09:20", "11:00"), ("13:30", "15:00"))

    # --- Step 9: market filters ---
    max_gap_pct: float = 2.0        # skip if open gaps > 2% vs prior close
    block_event_days: bool = True   # RBI/Fed/earnings-within-24h guard

    # --- universe / instruments ---
    indices: tuple[str, ...] = ("NIFTY", "BANKNIFTY", "FINNIFTY")
    target_model: str = "A"         # 'A' 1:3 | 'B' nearest-liquidity | 'C' partial+trail

    # --- backtest ---
    capital: float = 1_000_000.0
    segment: str = "intraday"       # cost model leg (F&O/intraday)
    extra: dict = field(default_factory=dict)


DEFAULT = DailySweepConfig()
