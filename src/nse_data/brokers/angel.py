"""
Angel One SmartAPI client — historical intraday candle backfill.

A free alternative to Kite (brokers/kite.py) and Groww (brokers/groww.py) for
filling raw_intraday_candles. SmartAPI's historical-candle endpoint
(getCandleData) is free with a regular Angel demat account — no paid Trading
API subscription — and serves multi-year 1-minute history.

Auth is a daily TOTP login: API key + client code + MPIN + a TOTP code minted
here from your TOTP secret. The session token lives in the SmartConnect object.

Credentials (from environment / .env):

    ANGEL_API_KEY        the API Key of your SmartAPI app (X-PrivateKey)
    ANGEL_CLIENT_CODE    your Angel login / client ID  (e.g. A123456)
    ANGEL_PIN            your MPIN (the "password" in generateSession)
    ANGEL_TOTP_SECRET    the base32 TOTP secret (NOT the rotating 6-digit code)

Interval vocabulary matches the rest of the pipeline (kite-style strings:
'minute','5minute','day', …); we translate to SmartAPI's interval constants.

Symbol -> instrument token comes from Angel's public ScripMaster JSON, cached
to data/angel_instruments_NSE.json once per day.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# Our kite-style interval -> SmartAPI getCandleData `interval` constant.
_INTERVAL = {
    "minute": "ONE_MINUTE", "3minute": "THREE_MINUTE", "5minute": "FIVE_MINUTE",
    "10minute": "TEN_MINUTE", "15minute": "FIFTEEN_MINUTE",
    "30minute": "THIRTY_MINUTE", "60minute": "ONE_HOUR", "day": "ONE_DAY",
}
# SmartAPI per-request max window (calendar days), straight from the docs.
_WINDOW_DAYS = {
    "minute": 30, "3minute": 60, "5minute": 100, "10minute": 100,
    "15minute": 200, "30minute": 200, "60minute": 400, "day": 2000,
}

# Public instrument dump (token <-> tradingsymbol). Override via env if Angel
# moves it.
_SCRIP_URL = os.environ.get(
    "ANGEL_SCRIP_URL",
    "https://margincalc.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json",
)
_INSTRUMENTS_CACHE = Path("data/angel_instruments_NSE.json")

_RATE_SLEEP = 0.4    # between successful requests (historical limit ~3 req/sec)
_MAX_RETRIES = 10    # on rate-limit / transient errors


class AngelError(RuntimeError):
    pass


def _is_rate_limit(e: Exception) -> bool:
    s = (type(e).__name__ + " " + str(e)).lower()
    return ("exceeding access rate" in s or "ab1004" in s or "ratelimit" in s
            or "429" in s or "too many" in s or "access denied" in s)


def _is_auth_error(x) -> bool:
    """Session-token death (AG8001 'Invalid Token') — Angel sessions expire
    after a few hours, mid-run on a long backfill. Recoverable by re-login."""
    s = str(x).lower()
    return "invalid token" in s or "ag8001" in s or "token expired" in s


def _require(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        raise AngelError(
            f"{name} not set. Add it to .env "
            f"(ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PIN, ANGEL_TOTP_SECRET)."
        )
    return v


def credentials_present() -> bool:
    return all(os.environ.get(k) for k in
               ("ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_PIN", "ANGEL_TOTP_SECRET"))


_client_cache = None


def _client():
    """Build and authenticate a SmartConnect client (TOTP login). Cached."""
    global _client_cache
    if _client_cache is not None:
        return _client_cache
    try:
        from SmartApi import SmartConnect
    except ImportError as e:
        raise AngelError("smartapi-python not installed — "
                         "`pip install smartapi-python`") from e
    # The SDK logs every request/searchScrip hit at INFO via logzero — mute it.
    try:
        import logging
        import logzero
        logzero.loglevel(logging.WARNING)
    except Exception:
        pass
    try:
        import pyotp
    except ImportError as e:
        raise AngelError("pyotp needed for TOTP — `pip install pyotp`") from e

    obj = SmartConnect(api_key=_require("ANGEL_API_KEY"))
    totp = pyotp.TOTP(_require("ANGEL_TOTP_SECRET")).now()
    resp = cast(dict[str, Any],
                obj.generateSession(_require("ANGEL_CLIENT_CODE"),
                                    _require("ANGEL_PIN"), totp))
    if not (isinstance(resp, dict) and resp.get("status")):
        msg = resp.get("message") if isinstance(resp, dict) else resp
        raise AngelError(f"login failed: {msg}")
    _client_cache = obj
    return obj


def _relogin():
    """Drop the cached client and authenticate fresh (new TOTP login)."""
    global _client_cache
    _client_cache = None
    return _client()


def check() -> dict:
    """Verify credentials by logging in and fetching the user profile."""
    obj = _client()
    # getProfile wants the refresh token captured during login.
    refresh = getattr(obj, "refresh_token", None) or getattr(obj, "refreshToken", None)
    prof = obj.getProfile(refresh)
    return cast(dict, prof.get("data", prof) if isinstance(prof, dict) else prof)


# ---- instruments -----------------------------------------------------------

# Spot index tokens (SmartAPI AMXIDX instruments — not in the -EQ scrip cache).
# Validated live against getCandleData on 2026-06-12. Index candles carry
# volume = 0; downstream VWAP-style math must fall back to a typical-price
# average for these symbols.
INDEX_TOKENS: dict[str, str] = {
    "NIFTY": "99926000",        # Nifty 50
    "BANKNIFTY": "99926009",    # Nifty Bank
}


def _load_cache() -> dict[str, str]:
    if _INSTRUMENTS_CACHE.exists():
        try:
            return json.loads(_INSTRUMENTS_CACHE.read_text())
        except Exception:
            pass
    return {}


def _save_cache(mapping: dict[str, str]) -> None:
    _INSTRUMENTS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _INSTRUMENTS_CACHE.write_text(json.dumps(mapping))


def load_instruments(refresh: bool = False) -> dict[str, str]:
    """Map NSE tradingsymbol -> symboltoken, cached daily. Equity only (-EQ).

    Primary source is Angel's bulk ScripMaster dump. If that host is
    unreachable, fall back to whatever is cached — per-symbol gaps are then
    filled lazily via searchScrip in _token_for (which uses the API host)."""
    cache = _INSTRUMENTS_CACHE
    if cache.exists() and not refresh:
        age_days = (time.time() - cache.stat().st_mtime) / 86400
        if age_days < 1:
            return _load_cache()

    try:
        import requests
        rows = requests.get(_SCRIP_URL, timeout=60).json()
    except Exception:
        return _load_cache()   # offline / host blocked -> cache + lazy lookup

    mapping: dict[str, str] = {}
    for r in rows:
        if r.get("exch_seg") != "NSE":
            continue
        sym = r.get("symbol", "")
        if not sym.endswith("-EQ"):          # equities only; skip F&O/indices
            continue
        mapping[sym[:-3]] = str(r["token"])  # 'RELIANCE-EQ' -> 'RELIANCE'
    if mapping:
        _save_cache(mapping)
        return mapping
    return _load_cache()


def _search_token(symbol: str) -> str | None:
    """Resolve one symbol's NSE -EQ token via the searchScrip API (the host
    that's always reachable). Re-logins once on an expired session (the SDK
    returns the AG8001 error dict instead of raising). None if not found."""
    for attempt in (0, 1):
        try:
            resp = cast(dict, _client().searchScrip("NSE", symbol))
        except Exception as e:
            if attempt == 0 and _is_auth_error(e):
                _relogin()
                continue
            return None
        # error responses carry success=False/message; happy path has status=True
        if not (resp.get("status") or resp.get("success")):
            if attempt == 0 and _is_auth_error(resp.get("message", "")):
                _relogin()
                continue
            return None
        want = f"{symbol}-EQ"
        for item in (resp.get("data") or []):
            if item.get("tradingsymbol") == want:
                return str(item.get("symboltoken"))
        return None
    return None


def _token_for(symbol: str, instruments: dict[str, str]) -> str:
    tok = INDEX_TOKENS.get(symbol) or instruments.get(symbol)
    if tok:
        return tok
    tok = _search_token(symbol)              # API fallback, then persist
    if tok:
        instruments[symbol] = tok
        cache = _load_cache()
        cache[symbol] = tok
        _save_cache(cache)
        return tok
    raise AngelError(f"no NSE symboltoken for {symbol!r}")


# ---- historical candles ----------------------------------------------------

def _norm(c: list) -> dict:
    """One SmartAPI candle [ts_iso, o, h, l, c, v] -> our row shape.
    ts is ISO with +05:30 offset; fromisoformat -> aware dt -> correct UTC epoch."""
    return {
        "ts": int(datetime.fromisoformat(c[0]).timestamp()),
        "open": c[1], "high": c[2], "low": c[3], "close": c[4],
        "volume": c[5] if len(c) > 5 else None,
    }


def fetch_symbol(
    symbol: str, interval: str, start: date, end: date,
    instruments: dict[str, str] | None = None,
) -> list[dict]:
    """Fetch all candles for a symbol over [start, end], paginating around the
    per-interval window limit. Returns candles oldest-first with `ts` (UTC epoch
    seconds). Interval is a kite-style string ('minute','5minute','day',…).

    Uses SmartAPI getCandleData; needs only a free Angel account. On rate-limit
    it backs off and retries (the historical limit is ~3 req/sec)."""
    if interval not in _INTERVAL:
        raise AngelError(f"unsupported interval {interval!r}")
    client = _client()
    instruments = instruments if instruments is not None else load_instruments()
    token = _token_for(symbol, instruments)
    iv = _INTERVAL[interval]
    window = timedelta(days=_WINDOW_DAYS[interval])

    out: list[dict] = []
    cur = start
    while cur <= end:
        win_end = min(cur + window - timedelta(days=1), end)
        params = {
            "exchange": "NSE",
            "symboltoken": token,
            "interval": iv,
            "fromdate": f"{cur:%Y-%m-%d} 09:15",
            "todate": f"{win_end:%Y-%m-%d} 15:30",
        }
        payload = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = cast(dict, client.getCandleData(params))
                if isinstance(resp, dict) and not resp.get("status"):
                    raise AngelError(resp.get("message") or resp.get("errorcode") or "request failed")
                payload = resp
                break
            except Exception as ex:
                if attempt < _MAX_RETRIES - 1 and _is_auth_error(ex):
                    client = _relogin()      # session died mid-run: fresh login
                    continue
                if _is_rate_limit(ex) and attempt < _MAX_RETRIES - 1:
                    # Linear backoff capped at 60s — rides through the 1-minute
                    # rate window.
                    time.sleep(min(60, 5 * (attempt + 1)))
                    continue
                raise
        candles = (payload or {}).get("data") or []
        out.extend(_norm(c) for c in candles)
        time.sleep(_RATE_SLEEP)
        cur = win_end + timedelta(days=1)
    return out
