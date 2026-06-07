"""
Signal metrics — the three numbers the detector's rules are built on.

Pure read functions: a DB connection + symbol in, a number (or None when the
inputs aren't ready) out. No Redis, no rule logic, no writes — `detect.py`
composes these into the actual signal conditions (tasks 4.5/4.6).

Three metrics:

  compute_oi_change(conn, symbol)      → (oi_change_pct, prev_oi, curr_oi)
  compute_price_change(conn, symbol)   → (price_change_pct, price)
  compute_volume_ratio(conn, symbol)   → ratio

LEARNINGS note carried into 4.5: `raw_oi_spurts` has NO price field, so the OI
and price legs of `long_buildup` come from two different tables — OI here,
price from `raw_equity_quotes`. Keeping them as separate functions makes that
join explicit at the call site rather than hidden in a query.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from ..indicators.intraday_ohlcv import read_intraday_5m
from ..scheduler.market_hours import now_ist

# A full NSE equity session (09:15–15:30) is 375 minutes = 75 five-minute bars.
# Used to convert a 20-day *daily* average volume into an average *per-5m-bar*
# volume, the denominator of the volume ratio.
BARS_PER_SESSION = 75

# How many trailing daily bars feed the average-volume baseline (task 4.2).
_VOLUME_LOOKBACK_DAYS = 20

# Intraday read window for the current 5-min bar's volume. Two hours always
# spans several bars once the session is underway and keeps the per-symbol read
# cheap (the detector calls this every minute across the whole universe).
_INTRADAY_WINDOW_SECS = 2 * 3600


def compute_oi_change(
    conn: sqlite3.Connection, symbol: str,
) -> tuple[float | None, int | None, int | None]:
    """Open-interest change from the latest `raw_oi_spurts` snapshot.

    Returns (oi_change_pct, prev_oi, curr_oi). `oi_change_pct` is the percent
    change of the current OI over the previous OI — equivalent to the feed's
    own `avg_oi_pct`, recomputed here so the trio stays internally consistent.

    Returns (None, prev, curr) when the percent can't be formed (no row, or a
    missing/zero previous OI), so callers can still see the raw legs.
    """
    row = conn.execute(
        "SELECT latest_oi, prev_oi FROM raw_oi_spurts "
        "WHERE symbol = ? ORDER BY as_of DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    if row is None:
        return (None, None, None)

    curr_oi, prev_oi = row[0], row[1]
    if curr_oi is None or prev_oi is None or prev_oi == 0:
        return (None, prev_oi, curr_oi)
    return ((curr_oi - prev_oi) / prev_oi * 100.0, prev_oi, curr_oi)


def compute_price_change(
    conn: sqlite3.Connection, symbol: str,
) -> tuple[float | None, float | None]:
    """Day's percent change + last price from the latest `raw_equity_quotes`.

    Returns (price_change_pct, price). The symbol can appear under several index
    lists; we take the most recent snapshot regardless of which list it came
    from. Returns (None, None) if the symbol isn't in the quote feed yet.
    """
    row = conn.execute(
        "SELECT pct_change, last_price FROM raw_equity_quotes "
        "WHERE symbol = ? ORDER BY as_of DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    if row is None:
        return (None, None)
    return (row[0], row[1])


def compute_volume_ratio(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    now: datetime | None = None,
) -> float | None:
    """Current 5-min bar volume relative to its 20-day average (task 4.2).

        ratio = current_5m_volume / (avg_20d_daily_volume / 75)

    The numerator is the volume of the most recent 5-minute bar (merged live +
    historical intraday). The denominator turns the 20-day average *daily*
    volume from bhavcopy into an average *per-5m-bar* volume. A ratio of 1.0
    means this bar is trading at exactly its typical pace; 1.5 (the rule
    threshold) means 50% hotter than usual.

    Returns None when either side is unavailable (no intraday bar yet, or no
    daily history), so the detector treats the volume leg as un-met rather than
    firing on a bogus number.
    """
    now = now or now_ist()

    current_vol = _latest_5m_volume(conn, symbol, now)
    if current_vol is None:
        return None

    avg_daily = _avg_daily_volume(conn, symbol, _VOLUME_LOOKBACK_DAYS)
    if not avg_daily:   # None or 0
        return None

    avg_5m = avg_daily / BARS_PER_SESSION
    if avg_5m == 0:
        return None
    return current_vol / avg_5m


# ------------------------------------------------------------------- reads

def _latest_5m_volume(
    conn: sqlite3.Connection, symbol: str, now: datetime,
) -> float | None:
    """Volume of the most recent 5-minute bar, or None if there isn't one."""
    since_ts = int(now.timestamp()) - _INTRADAY_WINDOW_SECS
    bars = read_intraday_5m(conn, symbol, since_ts=since_ts)
    if bars is None or bars.empty or "volume" not in bars.columns:
        return None
    value = bars["volume"].iloc[-1]
    if value is None or value != value:   # NaN guard
        return None
    return float(value)


def _avg_daily_volume(
    conn: sqlite3.Connection, symbol: str, n: int,
) -> float | None:
    """Average EQ daily volume over the last `n` bhavcopy sessions."""
    row = conn.execute(
        "SELECT AVG(volume) FROM ("
        "  SELECT volume FROM raw_bhavcopy_cm "
        "  WHERE symbol = ? AND series = 'EQ' AND volume IS NOT NULL "
        "  ORDER BY date DESC LIMIT ?"
        ")",
        (symbol, n),
    ).fetchone()
    return row[0] if row and row[0] is not None else None
