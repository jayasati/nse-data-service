"""
Intraday equity cost model (FEATURE_CHECKLIST Week 5, task 5.2).

A pure function: given the two legs of a round-trip trade it returns gross P&L,
total costs broken down by component, and net P&L. No DB, no I/O — the paper
tracker (task 5.3) calls this when it closes a trade, and the backtester can
reuse it for apples-to-apples comparisons.

The numbers are the standard Indian discount-broker intraday-equity schedule
(Zerodha-style), which is what the checklist specifies:

  Brokerage       min(₹20, 0.03% × turnover) per leg
  STT             0.025% of the SELL value only (intraday equity)
  Exchange (NSE)  0.00345% of turnover per leg
  SEBI            ₹10 per crore (0.0001%) of total turnover
  Stamp duty      0.003% of the BUY value only
  GST             18% of (brokerage + exchange + SEBI)
  Slippage        max(1 tick, 1 bps × price) per share, charged on both legs

Both LONG (buy-then-sell) and SHORT (sell-then-buy) round trips are supported;
they pay the same statutory charges but the gross P&L sign flips. "Buy value"
and "sell value" are leg roles, not time order — STT (sell) and stamp duty
(buy) always attach to the correct economic leg regardless of direction.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- statutory + brokerage rates (fractions of the relevant turnover) ---
BROKERAGE_RATE = 0.0003          # 0.03% per leg
BROKERAGE_CAP = 20.0             # ₹20 per leg cap
STT_RATE = 0.00025              # 0.025% of sell value (intraday)
EXCHANGE_RATE = 0.0000345       # 0.00345% per leg (NSE)
SEBI_RATE = 0.000001            # ₹10 per crore = 10 / 1e7 of turnover
STAMP_RATE = 0.00003            # 0.003% of buy value
GST_RATE = 0.18                 # 18% on brokerage + exchange + SEBI

# --- slippage ---
TICK_SIZE = 0.05                # NSE equity tick
SLIPPAGE_BPS = 0.0001           # 1 basis point of price


@dataclass(frozen=True)
class TradeCosts:
    """Itemised costs + gross/net P&L for one round-trip trade. All in ₹."""

    gross_pnl: float
    brokerage: float
    stt: float
    exchange: float
    sebi: float
    stamp_duty: float
    gst: float
    slippage: float
    total_costs: float
    net_pnl: float


def compute_costs(
    entry_price: float,
    exit_price: float,
    quantity: int,
    trade_type: str = "long",
) -> TradeCosts:
    """Cost-and-P&L breakdown for a round-trip intraday equity trade.

    `trade_type` is 'long' (buy at entry, sell at exit) or 'short' (sell at
    entry, buy back at exit). The statutory charges are direction-independent;
    only which leg is the "sell" (STT) and which is the "buy" (stamp duty) — and
    the gross-P&L sign — depend on it.
    """
    direction = trade_type.lower()
    if direction not in ("long", "short"):
        raise ValueError(f"trade_type must be 'long' or 'short', got {trade_type!r}")

    entry_value = entry_price * quantity
    exit_value = exit_price * quantity

    if direction == "long":
        buy_value, sell_value = entry_value, exit_value
        gross_pnl = (exit_price - entry_price) * quantity
    else:
        buy_value, sell_value = exit_value, entry_value
        gross_pnl = (entry_price - exit_price) * quantity

    # Brokerage is per leg, each leg capped independently.
    brokerage = _leg_brokerage(buy_value) + _leg_brokerage(sell_value)
    stt = STT_RATE * sell_value
    exchange = EXCHANGE_RATE * (buy_value + sell_value)
    sebi = SEBI_RATE * (buy_value + sell_value)
    stamp_duty = STAMP_RATE * buy_value
    gst = GST_RATE * (brokerage + exchange + sebi)
    slippage = _slippage(entry_price, quantity) + _slippage(exit_price, quantity)

    total_costs = brokerage + stt + exchange + sebi + stamp_duty + gst + slippage
    return TradeCosts(
        gross_pnl=gross_pnl,
        brokerage=brokerage,
        stt=stt,
        exchange=exchange,
        sebi=sebi,
        stamp_duty=stamp_duty,
        gst=gst,
        slippage=slippage,
        total_costs=total_costs,
        net_pnl=gross_pnl - total_costs,
    )


def _leg_brokerage(leg_value: float) -> float:
    """min(₹20, 0.03% × leg turnover) for one leg."""
    return min(BROKERAGE_CAP, BROKERAGE_RATE * leg_value)


def _slippage(price: float, quantity: int) -> float:
    """Per-leg slippage: max(1 tick, 1 bps × price) per share."""
    per_share = max(TICK_SIZE, SLIPPAGE_BPS * price)
    return per_share * quantity
