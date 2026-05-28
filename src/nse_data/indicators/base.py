"""
Indicator base class.

An Indicator is a pure function: it takes a price/volume history DataFrame
for one symbol and returns a DataFrame of computed values indexed the same
way as the input. It owns no DB connection and no symbol awareness — the
orchestrator in compute.py supplies the OHLCV slice and persists the result.

Each concrete subclass declares the small amount of metadata the orchestrator
needs to incrementalize and persist its output:

    name            stable slug, used in logs and registry keys.
    table           target SQLite table created by a migration.
    output_columns  exactly the writable columns produced by compute(); the
                    writer uses this tuple verbatim in its INSERT.
    min_history     bars of OHLCV history required before the first non-NaN
                    output row. The orchestrator pulls this many bars of
                    lookback when running incrementally, so the indicator can
                    assume "if I have fewer than this, my outputs are NaN".

compute() returns one row per input bar, indexed by `date` (matching the
TEXT date column on raw_bhavcopy_cm) and containing exactly output_columns.
Rows that can't yet be computed (early history) should be NaN — the writer
skips all-NaN rows so we don't pollute the indicator table with empty rows.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

import pandas as pd

# Where in the chart an indicator should render. `overlay` rides on the price
# axis (SMA, EMA, Bollinger middle); `oscillator` needs its own sub-pane with
# an independent y-scale (RSI 0–100, MACD diverges around 0). The dashboard
# reads this off the `/indicators` payload and routes accordingly.
Pane = Literal["overlay", "oscillator"]

# When the indicator runs. `eod` = nightly off bhavcopy (the only mode wired
# today). `intraday` = recomputed every minute during market hours off the
# live 1-min feed + intraday_candles. `session` = once per session, anchored
# to that session's intraday data (pivot points, volume profile).
Cadence = Literal["eod", "intraday", "session"]


class Indicator(ABC):
    """Abstract base for all indicators. See module docstring for the contract."""

    #: Stable slug ("sma", "rsi_14"). Used in logs and registry keys.
    name: str

    #: Target SQLite table (must exist via migration).
    table: str

    #: Primary-key columns on `table`. Must match the migration.
    pk_cols: tuple[str, ...] = ("symbol", "date")

    #: Computed columns produced by `compute()`. Used verbatim by the writer.
    output_columns: tuple[str, ...]

    #: Bars of history needed before the first non-NaN output. The
    #: orchestrator pulls this many bars of OHLCV lookback when running
    #: incrementally.
    min_history: int

    #: Render hint for the dashboard chart. See `Pane`.
    pane: Pane = "overlay"

    #: When to compute. See `Cadence`. Today only "eod" runs; intraday/session
    #: are reserved for the live-market scanner.
    cadence: Cadence = "eod"

    @abstractmethod
    def compute(self, ohlcv: pd.DataFrame) -> pd.DataFrame:
        """
        Pure compute. Given an OHLCV DataFrame indexed by `date` (ascending)
        with columns at least `open`, `high`, `low`, `close`, `volume`,
        return a DataFrame indexed by the same `date` with exactly
        `output_columns`. Early rows (insufficient history) should be NaN.
        """
        raise NotImplementedError
