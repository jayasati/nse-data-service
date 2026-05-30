"""Shared dataclasses for every strategy + the runner + persistence.

Single source of truth. `persistence.write_run` and `runner.run_backtest_for_universe`
both import from here, which breaks the persistence ↔ runner circular import that
existed in v1 (resolved with TYPE_CHECKING then; resolved structurally now).

Strategies produce `Signal` and `Trade`; the runner wraps each with the symbol
to produce `SymbolSignal` / `SymbolTrade`; persistence writes those.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Direction = Literal["LONG", "SHORT"]
# Free-form string column in SQLite; strategies declare their own values.
# v1: TARGET | STOP | EOD_1515
# v2: TARGET | STOP | WILLR_EXTREME | EOD_HISTORY
ExitReason = str


# ----------------------------------------------------- strategy config base

@dataclass(frozen=True)
class StrategyConfig:
    """Shared fields every strategy carries. Each strategy subclasses this
    with its own knobs. `persistence.write_run` and the runner only depend on
    fields defined here, so any subclass works.

    All fields default so subclasses can add their own non-default fields
    without breaking dataclass field ordering.
    """
    strategy: str = ""
    leverage: float = 1.0
    rr_min: float = 1.5
    tick: float = 0.05
    allow_reentry: bool = False
    # Rupee notional per trade. qty = max(1, floor(notional_per_trade / entry_price)).
    # Without this, every trade is 1 share and high-priced names (MRF, PAGEIND)
    # dominate aggregate P&L; results across a wide-price universe become noise.
    notional_per_trade: float = 100_000.0
    extras: dict = field(default_factory=dict)


# ----------------------------------------------------- engine-level results

@dataclass(frozen=True)
class Signal:
    """A detected pattern, regardless of whether it became a trade."""
    direction: Direction
    setup_ts: int
    rr: float
    armed: bool


@dataclass(frozen=True)
class Trade:
    direction: Direction
    setup_ts: int
    entry_ts: int
    entry_price: float
    sl: float
    target: float
    exit_ts: int
    exit_price: float
    exit_reason: ExitReason
    rr_at_entry: float
    qty: int = 1
    signal_tags: str | None = None    # 'basic' | 'divergence' | 'basic+divergence' | None

    def pnl_raw(self) -> float:
        sign = 1.0 if self.direction == "LONG" else -1.0
        return (self.exit_price - self.entry_price) * self.qty * sign

    def pnl_leveraged(self, leverage: float) -> float:
        return self.pnl_raw() * leverage


# ----------------------------------------------------- runner-level wrappers

@dataclass(frozen=True)
class SymbolTrade:
    """A Trade with its symbol attached, plus pre-computed pnl fields so the
    persistence layer doesn't have to call methods or know about leverage."""
    symbol: str
    direction: str
    setup_ts: int
    entry_ts: int
    entry_price: float
    sl: float
    target: float
    exit_ts: int
    exit_price: float
    exit_reason: str
    qty: int
    rr_at_entry: float
    pnl_raw: float
    pnl_leveraged: float
    signal_tags: str | None = None


@dataclass(frozen=True)
class SymbolSignal:
    symbol: str
    direction: str
    setup_ts: int
    rr: float
    armed: bool


@dataclass(frozen=True)
class RunReport:
    signals: list[SymbolSignal]
    trades: list[SymbolTrade]

    @property
    def total_signals(self) -> int:
        return len(self.signals)

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.pnl_raw > 0)

    @property
    def losses(self) -> int:
        return sum(1 for t in self.trades if t.pnl_raw < 0)

    @property
    def pnl_raw(self) -> float:
        return sum(t.pnl_raw for t in self.trades)

    @property
    def pnl_leveraged(self) -> float:
        return sum(t.pnl_leveraged for t in self.trades)

    @property
    def win_rate(self) -> float:
        decided = self.wins + self.losses
        return self.wins / decided if decided > 0 else 0.0
