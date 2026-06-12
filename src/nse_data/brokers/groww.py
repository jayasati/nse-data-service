"""
Groww API client — historical intraday candle backfill.

An alternative to Kite (brokers/kite.py) for filling raw_intraday_candles.
Groww's auth is simpler — no redirect-URL flow; you mint a daily access token
from an API key + TOTP. Uses the official `growwapi` SDK.

Credentials (from environment / .env), in priority order:

    GROWW_ACCESS_TOKEN   a token you generated directly (valid for the day), OR
    GROWW_API_KEY + GROWW_TOTP_SECRET   (TOTP API key flow — token minted here), OR
    GROWW_API_KEY + GROWW_API_SECRET    (approval API key flow)

Interval vocabulary matches the rest of the pipeline (kite-style strings:
'minute','5minute','day', …); we translate to Groww's candle_interval constants.
"""

from __future__ import annotations

import os
import time
import warnings
from datetime import date, datetime, timedelta
from typing import cast

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# Our kite-style interval -> Groww v2 `candle_interval` string for
# get_historical_candles. v2 (unlike the deprecated v1 get_historical_candle_data,
# which capped minute data at ~3 months) serves history back to 2020 and needs an
# active Trading API subscription. Verified interval strings from the SDK constants.
_V2_INTERVAL = {
    "minute": "1minute", "3minute": "3minute", "5minute": "5minute",
    "10minute": "10minute", "15minute": "15minute", "30minute": "30minute",
    "60minute": "1hour", "day": "1day", "week": "1week",
}
# v2 per-request max window (calendar days): 1-5min=30, 10-30min=90, 1h+=180.
_WINDOW_DAYS = {
    "minute": 30, "3minute": 30, "5minute": 30, "10minute": 90,
    "15minute": 90, "30minute": 90, "60minute": 180, "day": 180, "week": 365,
}
_RATE_SLEEP = 1.0   # between successful requests
_MAX_RETRIES = 10   # on rate-limit / transient errors


def _is_rate_limit(e: Exception) -> bool:
    s = (type(e).__name__ + " " + str(e)).lower()
    return "ratelimit" in s or "429" in s or "too many" in s


class GrowwError(RuntimeError):
    pass


def _require(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise GrowwError(
            f"{name} not set. Add it to .env "
            f"(GROWW_ACCESS_TOKEN, or GROWW_API_KEY + GROWW_TOTP_SECRET)."
        )
    return v


def credentials_present() -> bool:
    return bool(os.environ.get("GROWW_ACCESS_TOKEN")) or bool(os.environ.get("GROWW_API_KEY"))


def _token() -> str:
    """Return an access token — direct from env, or minted from API key + TOTP."""
    direct = os.environ.get("GROWW_ACCESS_TOKEN")
    if direct:
        return direct
    from growwapi import GrowwAPI
    api_key = _require("GROWW_API_KEY")
    totp_secret = os.environ.get("GROWW_TOTP_SECRET")
    api_secret = os.environ.get("GROWW_API_SECRET")
    if totp_secret:
        try:
            import pyotp
        except ImportError as e:
            raise GrowwError("pyotp needed for TOTP — `pip install pyotp`") from e
        # SDK's annotation says -> dict, but it actually returns the token string.
        return cast(str, GrowwAPI.get_access_token(api_key, totp=pyotp.TOTP(totp_secret).now()))
    if api_secret:
        return cast(str, GrowwAPI.get_access_token(api_key, secret=api_secret))
    raise GrowwError("set GROWW_ACCESS_TOKEN, or GROWW_API_KEY + GROWW_TOTP_SECRET")


_client_cache = None


def _client():
    global _client_cache
    if _client_cache is None:
        try:
            from growwapi import GrowwAPI
        except ImportError as e:
            raise GrowwError("growwapi not installed — `pip install growwapi`") from e
        _client_cache = GrowwAPI(_token())
    return _client_cache


def check() -> dict:
    """Verify credentials by minting a token and fetching the user profile."""
    return _client().get_user_profile()


def _groww_symbol(client, symbol: str) -> str:
    inst = client.get_instrument_by_exchange_and_trading_symbol("NSE", symbol)
    gs = inst.get("groww_symbol") if isinstance(inst, dict) else None
    if not gs:
        raise GrowwError(f"no groww_symbol for {symbol!r}")
    return gs


def _to_epoch(ts) -> int:
    """Normalise a candle timestamp (epoch s/ms or ISO string) to UTC epoch secs."""
    if isinstance(ts, str):
        try:
            return int(float(ts))
        except ValueError:
            return int(datetime.fromisoformat(ts).timestamp())
    ts = int(ts)
    return ts // 1000 if ts > 1_000_000_000_000 else ts  # ms -> s


def _norm(c) -> dict:
    """Normalise one Groww candle (list [ts,o,h,l,c,v] or dict) to our row shape."""
    if isinstance(c, dict):
        ts = c.get("timestamp") or c.get("time") or c.get("epoch") or c.get("start_time")
        o, h, l, cl = c.get("open"), c.get("high"), c.get("low"), c.get("close")
        v = c.get("volume")
    else:
        ts, o, h, l, cl = c[0], c[1], c[2], c[3], c[4]
        v = c[5] if len(c) > 5 else None
    return {"ts": _to_epoch(ts), "open": o, "high": h, "low": l, "close": cl, "volume": v}


def fetch_symbol(symbol: str, interval: str, start: date, end: date) -> list[dict]:
    """Fetch candles for a symbol over [start, end], paginating per window limit.
    Returns candles oldest-first with `ts` (UTC epoch secs). Interval is a
    kite-style string ('minute','5minute','day',…).

    Uses Groww v2 ``get_historical_candles`` — signature
    (exchange, segment, groww_symbol, start_time, end_time, candle_interval) —
    which serves history back to 2020 (the deprecated v1 capped minute data at
    ~3 months). Needs an active Trading API subscription; without one the API
    raises "Access forbidden for this request"."""
    if interval not in _V2_INTERVAL:
        raise GrowwError(f"unsupported interval {interval!r}")
    client = _client()
    gs = _groww_symbol(client, symbol)            # e.g. 'NSE-RELIANCE'
    civ = _V2_INTERVAL[interval]
    window = timedelta(days=_WINDOW_DAYS[interval])

    out: list[dict] = []
    cur = start
    while cur <= end:
        win_end = min(cur + window - timedelta(days=1), end)
        s = f"{cur:%Y-%m-%d} 00:00:00"
        e = f"{win_end:%Y-%m-%d} 23:59:59"
        payload = None
        for attempt in range(_MAX_RETRIES):
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    payload = client.get_historical_candles(
                        client.EXCHANGE_NSE, client.SEGMENT_CASH, gs, s, e, civ)
                break
            except Exception as ex:
                if _is_rate_limit(ex) and attempt < _MAX_RETRIES - 1:
                    # Linear backoff capped at 60s — Groww's per-minute rate
                    # window resets within ~60s, so this rides through it.
                    time.sleep(min(60, 5 * (attempt + 1)))
                    continue
                raise
        candles = (payload or {}).get("candles") or []
        out.extend(_norm(c) for c in candles)
        time.sleep(_RATE_SLEEP)
        cur = win_end + timedelta(days=1)
    return out
