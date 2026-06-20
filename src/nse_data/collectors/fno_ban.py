"""F&O securities-in-ban list (PROFITABILITY_PLAN R13).

NSE publishes the daily "Securities in F&O ban period" as a small CSV at a fixed URL.
A name enters ban when aggregate open interest crosses 95% of its market-wide position
limit (MWPL) — i.e. derivative speculation is frothy; fresh F&O positions are blocked
until it cools. For a DELIVERY/positional book this is a risk flag, not a hard gate (the
ban restricts derivatives, not cash equity).

The file format is quirky and has changed over time, so the parser is heuristic: it pulls
NSE-symbol-looking tokens (all-caps alnum, with a letter) out of the comma-separated rows
and ignores the header / serial-number columns. Verified against the live file on the
server. One row per (symbol, capture-date) so a forward ban history accumulates.
"""
from __future__ import annotations

import time
from datetime import date
from typing import Any

from .base import CsvCollector, Request, Row

FO_SECBAN_URL = "https://nsearchives.nseindia.com/content/fo/fo_secban.csv"

# all-caps tokens that appear in the file but are not securities
_NOT_SYMBOLS = {"SYMBOL", "FO", "F&O", "NSE", "BAN", "LIST", "NIL", "NA", "SRNO", "SR", "NO",
                "SECURITIES", "PERIOD", "DATE"}


def _looks_like_symbol(tok: str) -> bool:
    t = tok.strip().strip('"').strip()
    if not t or t in _NOT_SYMBOLS or len(t) > 20 or len(t) < 2:
        return False
    if t.upper() != t:                       # must be ALL CAPS (headers are mixed case)
        return False
    core = t.replace("&", "").replace("-", "")
    return core.isalnum() and any(c.isalpha() for c in t)


class FnoBan(CsvCollector):
    name = "fno_ban"
    table = "raw_fno_ban"
    pk_cols = ("symbol", "ban_date")
    response_type = "text"

    def url_for_date(self, d: date) -> str:
        return FO_SECBAN_URL                  # the file is always "current", not date-stamped

    def normalize(self, data: Any, request: Request) -> list[Row]:
        if isinstance(data, (bytes, bytearray)):
            data = data.decode("utf-8", errors="replace")
        if not isinstance(data, str):
            return []
        ban_date = (request.meta or {}).get("date") or date.today().isoformat()
        now = int(time.time())
        seen: set[str] = set()
        rows: list[Row] = []
        for line in data.splitlines():
            for tok in line.split(","):
                if _looks_like_symbol(tok) and tok.strip().strip('"') not in seen:
                    sym = tok.strip().strip('"')
                    seen.add(sym)
                    rows.append({"symbol": sym, "ban_date": ban_date, "fetched_at": now})
        return rows


# ---- gate / lookup (consumed by the pre-buy card + any dispatcher gate) -----

def latest_ban_date(conn) -> str | None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='raw_fno_ban'").fetchone():
        return None
    r = conn.execute("SELECT MAX(ban_date) FROM raw_fno_ban").fetchone()
    return r[0] if r else None


def is_fno_banned(conn, symbol: str) -> bool:
    """True if `symbol` is in the most recent captured ban list. Fail-open (False) on no data."""
    d = latest_ban_date(conn)
    if not d:
        return False
    return conn.execute(
        "SELECT 1 FROM raw_fno_ban WHERE symbol=? AND ban_date=?", (symbol, d)).fetchone() is not None
