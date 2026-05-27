"""
Zerodha Kite Connect client — historical intraday candle backfill.

NSE's free feeds don't publish historical minute data; Kite Connect (a paid
broker API) does. This module wraps the official `kiteconnect` SDK to:

  - run the one-time/daily login → access-token exchange,
  - map a trading symbol to its instrument_token,
  - pull historical OHLCV candles for an interval + date range, paginating
    around Kite's per-request window limits.

Credentials come from the environment (.env is auto-loaded):

    KITE_API_KEY        from the Kite developer console (paid app)
    KITE_API_SECRET     "
    KITE_ACCESS_TOKEN   obtained via the daily login flow; expires ~6am IST

The `kiteconnect` package is imported lazily so the rest of the service runs
without it installed. See scripts/backfill_intraday.py for the CLI.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# Kite caps how much history one request may span, per interval.
# (minute is the tight one — 60 days; coarser intervals allow more.)
_WINDOW_DAYS = {
    "minute": 60, "3minute": 100, "5minute": 100, "10minute": 100,
    "15minute": 200, "30minute": 200, "60minute": 400, "day": 2000,
}

_HIST_RATE_SLEEP = 0.34  # Kite historical limit ~3 req/sec
_INSTRUMENTS_CACHE = Path("data/kite_instruments_{exchange}.json")


class KiteError(RuntimeError):
    pass


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise KiteError(
            f"{name} not set. Add it to .env. "
            f"(KITE_API_KEY/KITE_API_SECRET from the Kite console; "
            f"KITE_ACCESS_TOKEN via `python scripts/backfill_intraday.py login`.)"
        )
    return val


def _connect(with_token: bool = True):
    """Build a KiteConnect client. Imports the SDK lazily."""
    try:
        from kiteconnect import KiteConnect
    except ImportError as e:
        raise KiteError("kiteconnect not installed — `pip install kiteconnect`") from e
    kc = KiteConnect(api_key=_require("KITE_API_KEY"))
    if with_token:
        kc.set_access_token(_require("KITE_ACCESS_TOKEN"))
    return kc


# ---- auth flow -------------------------------------------------------------

def login_url() -> str:
    """URL the user opens to log in; the redirect carries a `request_token`."""
    return _connect(with_token=False).login_url()


def exchange_request_token(request_token: str) -> str:
    """Exchange a fresh request_token for an access_token (valid for the day)."""
    kc = _connect(with_token=False)
    data = kc.generate_session(request_token, api_secret=_require("KITE_API_SECRET"))
    return data["access_token"]


# ---- instruments -----------------------------------------------------------

def load_instruments(exchange: str = "NSE", refresh: bool = False) -> dict[str, int]:
    """Map tradingsymbol -> instrument_token for an exchange, cached daily."""
    cache = Path(str(_INSTRUMENTS_CACHE).format(exchange=exchange))
    if cache.exists() and not refresh:
        age_days = (time.time() - cache.stat().st_mtime) / 86400
        if age_days < 1:
            return {k: int(v) for k, v in json.loads(cache.read_text()).items()}

    kc = _connect()
    rows = kc.instruments(exchange)
    mapping = {r["tradingsymbol"]: int(r["instrument_token"]) for r in rows}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(mapping))
    return mapping


# ---- historical candles ----------------------------------------------------

def historical(token: int, interval: str, frm: datetime, to: datetime) -> list[dict]:
    """One Kite historical_data call. Returns list of candle dicts with keys
    date/open/high/low/close/volume."""
    kc = _connect()
    return kc.historical_data(token, frm, to, interval)


def fetch_symbol(
    symbol: str, interval: str, start: date, end: date,
    instruments: dict[str, int] | None = None,
) -> list[dict]:
    """Fetch all candles for a symbol over [start, end], paginating around the
    per-interval window limit. Returns candles oldest-first with an added
    `ts` field (UTC epoch seconds)."""
    if interval not in _WINDOW_DAYS:
        raise KiteError(f"unsupported interval {interval!r}")
    instruments = instruments if instruments is not None else load_instruments("NSE")
    token = instruments.get(symbol)
    if token is None:
        raise KiteError(f"no NSE instrument_token for {symbol!r}")

    window = timedelta(days=_WINDOW_DAYS[interval])
    out: list[dict] = []
    cur = start
    while cur <= end:
        win_end = min(cur + window - timedelta(days=1), end)
        frm = datetime.combine(cur, datetime.min.time())
        to = datetime.combine(win_end, datetime.max.time())
        candles = historical(token, interval, frm, to)
        for c in candles:
            dt = c["date"]
            out.append({
                "ts": int(dt.timestamp()),  # tz-aware (IST) -> correct UTC epoch
                "open": c["open"], "high": c["high"], "low": c["low"],
                "close": c["close"], "volume": c.get("volume"),
            })
        time.sleep(_HIST_RATE_SLEEP)
        cur = win_end + timedelta(days=1)
    return out


def credentials_present() -> bool:
    return all(os.environ.get(k) for k in ("KITE_API_KEY", "KITE_ACCESS_TOKEN"))
