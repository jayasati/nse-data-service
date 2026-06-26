"""
Macro backdrop collector — USDINR, Brent, Gold, US indices via Yahoo Finance.

Feeds the macro_regime feature (Layer 4/5). Source is Yahoo's lightweight JSON
chart API, fetched with httpx directly: this collector lives OUTSIDE the NSE
SessionManager (Yahoo isn't NSE — no cookie warm-up, rate limiter or circuit
breaker apply). We use the direct chart API rather than the `yfinance` library
to avoid a heavy scraping dependency; httpx is already in the stack.

run() is overridden because the base Collector pipeline dispatches through the
NSE session. We keep the same RunReport contract and per-call error isolation
(one failing ticker doesn't sink the rest), and reuse SnapshotCollector.persist
(upsert_many on (asset, as_of_date)).
"""

from __future__ import annotations

import time
import traceback
from datetime import datetime
from typing import Any, Mapping, Sequence

import httpx

from ..scheduler.market_hours import IST, now_ist
from .base import (
    ErrorRecord,
    Request,
    Row,
    RunReport,
    SnapshotCollector,
)

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_HEADERS = {"User-Agent": "Mozilla/5.0"}
_TIMEOUT = 15.0

# Canonical asset name -> Yahoo symbol.
TICKERS: dict[str, str] = {
    "USDINR": "USDINR=X",
    "BRENT":  "BZ=F",
    "GOLD":   "GC=F",
    "SP500":  "^GSPC",
    "NASDAQ": "^IXIC",
    "DOW":    "^DJI",
    # P4 — base metals (context driver for the metals_base basket). COPPER/ALUMINIUM have Yahoo
    # futures; ZINC has no reliable free feed so it is deliberately OMITTED (no fabrication) — a
    # row is only written when Yahoo returns data, else nothing (normalize() returns []).
    "COPPER":    "HG=F",
    "ALUMINIUM": "ALI=F",
}


class MacroCollector(SnapshotCollector):
    name = "macro"
    table = "raw_macro"
    pk_cols = ("asset", "as_of_date")

    tickers: dict[str, str] = TICKERS

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        # Unused — run() is overridden to fetch from Yahoo, not the NSE session.
        return []

    def fetch(self, client: httpx.Client, symbol: str) -> Any:
        """One Yahoo chart request. Isolated so tests can override without network."""
        resp = client.get(
            CHART_URL.format(symbol=symbol),
            params={"range": "5d", "interval": "1d"},
        )
        resp.raise_for_status()
        return resp.json()

    def normalize(self, data: Any, request: Request) -> list[Row]:
        asset = (request.meta or {}).get("asset")
        try:
            meta = data["chart"]["result"][0]["meta"]
        except (KeyError, IndexError, TypeError):
            return []
        if not isinstance(meta, dict):
            return []

        price = _f(meta.get("regularMarketPrice"))
        prev = _f(meta.get("chartPreviousClose") or meta.get("previousClose"))
        change = round(price - prev, 4) if price is not None and prev is not None else None
        pct = round(change / prev * 100, 4) if change is not None and prev else None

        return [{
            "asset":        asset,
            "as_of_date":   now_ist().date().isoformat(),
            "yahoo_symbol": meta.get("symbol"),
            "price":        price,
            "prev_close":   prev,
            "change":       change,
            "pct_change":   pct,
            "day_high":     _f(meta.get("regularMarketDayHigh")),
            "day_low":      _f(meta.get("regularMarketDayLow")),
            "volume":       _i(meta.get("regularMarketVolume")),
            "currency":     meta.get("currency"),
            "market_time":  _i(meta.get("regularMarketTime")),
            "captured_at":  int(time.time()),
        }]

    def run(self, session, db, context: Mapping[str, Any] | None = None) -> RunReport:
        """Fetch each ticker from Yahoo (ignoring the NSE `session`), normalize,
        persist once. Mirrors base.run()'s report shape + per-call isolation."""
        started = now_ist().astimezone(IST)
        t0 = time.perf_counter()
        report = RunReport(collector=self.name, started_at=started)

        all_rows: list[Row] = []
        with httpx.Client(headers=_HEADERS, timeout=_TIMEOUT) as client:
            for asset, symbol in self.tickers.items():
                report.fetched += 1
                req = Request(path_or_url=symbol, meta={"asset": asset})
                try:
                    data = self.fetch(client, symbol)
                    rows = self.normalize(data, req)
                    report.rows_seen += len(rows)
                    all_rows.extend(rows)
                    report.succeeded += 1
                except Exception as e:
                    report.failed += 1
                    report.errors.append(ErrorRecord(
                        request_url=symbol,
                        request_meta={"asset": asset},
                        exc_type=type(e).__name__,
                        message=str(e),
                        traceback=traceback.format_exc(),
                    ))

        if all_rows:
            try:
                report.persist = self.persist(db, all_rows)
            except Exception as e:
                report.errors.append(ErrorRecord(
                    request_url="<persist>",
                    request_meta={},
                    exc_type=type(e).__name__,
                    message=str(e),
                    traceback=traceback.format_exc(),
                ))

        report.finished_at = now_ist().astimezone(IST)
        report.duration_ms = int((time.perf_counter() - t0) * 1000)
        return report


def _f(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v):
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None
