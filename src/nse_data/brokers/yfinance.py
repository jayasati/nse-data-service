"""
Yahoo Finance client — historical intraday candle fallback.

A no-auth fallback for filling raw_intraday_candles when Groww/Kite are
unavailable (rate-limited, not-on-plan, or auth-failed). Uses the `yfinance`
library; NSE symbols get a `.NS` suffix on the wire.

Caveats vs Groww/Kite:
- 1-minute history is capped to the last ~7 days by Yahoo.
- Sub-day intervals are capped to the last ~60 days.
- Data quality / completeness is "best effort" — gaps and the occasional
  late bar happen. Treat this as a stopgap, not the primary source.

No credentials required. Interval vocabulary matches the rest of the pipeline
(kite-style: 'minute','5minute','day', …); we translate to yfinance strings.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

# kite-style interval -> (yfinance string, max lookback days Yahoo serves)
_INTERVAL = {
    "minute":   ("1m", 7),
    "3minute":  ("2m", 60),    # yfinance has no 3m; 2m is the closest
    "5minute":  ("5m", 60),
    "15minute": ("15m", 60),
    "30minute": ("30m", 60),
    "60minute": ("60m", 730),
    "day":      ("1d", 365 * 50),
}


class YFinanceError(RuntimeError):
    pass


def credentials_present() -> bool:
    # yfinance scrapes Yahoo; no auth.
    return True


def _yf():
    try:
        import yfinance as yf
    except ImportError as e:
        raise YFinanceError("yfinance not installed — `pip install yfinance`") from e
    return yf


def fetch_symbol(symbol: str, interval: str, start: date, end: date) -> list[dict]:
    """Fetch candles for an NSE symbol over [start, end].
    Returns candles oldest-first with `ts` (UTC epoch secs)."""
    if interval not in _INTERVAL:
        raise YFinanceError(f"unsupported interval {interval!r}")
    yf_interval, max_days = _INTERVAL[interval]

    # Clamp the request to what Yahoo actually serves for this interval.
    today = date.today()
    earliest = today - timedelta(days=max_days)
    eff_start = max(start, earliest)
    if eff_start > end:
        return []

    yf = _yf()
    ticker = yf.Ticker(f"{symbol}.NS")
    # end is exclusive in yfinance; bump by 1 day so the last day is included.
    df = ticker.history(
        start=eff_start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        interval=yf_interval,
        auto_adjust=False,
        prepost=False,
        actions=False,
        raise_errors=False,
    )
    if df is None or df.empty:
        return []

    out: list[dict] = []
    for idx, row in df.iterrows():
        ts = int(idx.timestamp())  # tz-aware -> correct UTC epoch
        o, h, l, c = row.get("Open"), row.get("High"), row.get("Low"), row.get("Close")
        v = row.get("Volume")
        # Drop fully-NaN rows (Yahoo occasionally emits them at session edges).
        if o is None or (isinstance(o, float) and math.isnan(o)):
            continue
        out.append({
            "ts": ts,
            "open": float(o), "high": float(h), "low": float(l), "close": float(c),
            "volume": int(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else None,
        })
    return out
