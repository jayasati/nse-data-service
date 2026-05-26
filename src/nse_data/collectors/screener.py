"""
screener.in fundamentals scraper — weekly, watchlist-scoped.

Feeds §5 Fundamentals: ROE, ROCE, P/E, market cap, book value, dividend yield,
and 3-year revenue/profit CAGR per watchlist symbol.

External source (not NSE): fetched with httpx, outside the SessionManager.
robots.txt permits /company/<sym>/; we scrape politely — weekly, watchlist-only
(config/universe.yaml `watchlist`, not the full universe), sequentially with a
small inter-request delay. The two regions we parse are stable, simple markup:
the #top-ratios <ul> (name/number spans) and the `ranges-table` CAGR tables.
No HTML-parser dependency — targeted regex over those regions.

run() is overridden (external fan-out, like MacroCollector) with per-symbol
error isolation; persist() is SnapshotCollector's upsert on (symbol, as_of_date).

Known gap: debt_to_equity is only captured when screener lists it (not a
default top-ratio); a balance-sheet-derived D/E is a later enhancement.
"""

from __future__ import annotations

import re
import time
import traceback
from pathlib import Path
from typing import Any, Mapping, Sequence

import httpx
import yaml

from ..scheduler.market_hours import IST, now_ist
from .base import ErrorRecord, Request, Row, RunReport, SnapshotCollector

COMPANY_URL = "https://www.screener.in/company/{symbol}/"
_HEADERS = {"User-Agent": "Mozilla/5.0"}
_TIMEOUT = 20.0

# screener top-ratio label -> our column.
_RATIO_MAP = {
    "ROCE": "roce",
    "ROE": "roe",
    "Stock P/E": "stock_pe",
    "Market Cap": "market_cap",
    "Book Value": "book_value",
    "Dividend Yield": "dividend_yield",
    "Debt to equity": "debt_to_equity",
}

class ScreenerParseError(Exception):
    """Raised when a fetched page is present but unparseable — i.e. screener's
    markup drifted from what the regex expects. Surfaces as a per-symbol failure
    on the RunReport (loud), instead of silently inserting an all-NULL row."""


_TOP_RATIOS_RE = re.compile(r'id="top-ratios".*?</ul>', re.S)
_NAME_NUM_RE = re.compile(
    r'<span class="name">\s*(.*?)\s*</span>.*?'
    r'<span class="number">\s*([-\d.,]+)\s*</span>',
    re.S,
)


class ScreenerFundamentals(SnapshotCollector):
    name = "screener_fundamentals"
    table = "raw_fundamentals_screener"
    pk_cols = ("symbol", "as_of_date")

    universe_path: str = "config/universe.yaml"
    request_delay: float = 1.0   # politeness gap between symbols (seconds)

    def watchlist(self) -> list[str]:
        try:
            cfg = yaml.safe_load(Path(self.universe_path).read_text()) or {}
        except OSError:
            return []
        return [str(s).strip() for s in (cfg.get("watchlist") or []) if str(s).strip()]

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return []   # run() is overridden for the external fan-out

    def fetch(self, client: httpx.Client, symbol: str) -> str:
        """One company page. Isolated so tests can override without network."""
        resp = client.get(COMPANY_URL.format(symbol=symbol))
        resp.raise_for_status()
        return resp.text

    def normalize(self, data: Any, request: Request) -> list[Row]:
        if not isinstance(data, str) or not data:
            return []
        symbol = (request.meta or {}).get("symbol")

        ratios = _parse_top_ratios(data)
        # Parse-health guard: a real company page has a #top-ratios block with
        # recognized ratios. If the block is missing, or present but yields
        # nothing, the page changed / was blocked / markup drifted — fail loudly
        # rather than persist a silently-empty row. (A company merely missing one
        # metric still yields a partial `ratios`, so this only fires on drift.)
        if 'id="top-ratios"' not in data:
            raise ScreenerParseError(
                f"{symbol}: #top-ratios section absent (page changed/blocked)"
            )
        if not ratios:
            raise ScreenerParseError(
                f"{symbol}: #top-ratios present but no ratios parsed (markup drift)"
            )

        row: Row = {
            "symbol":         symbol,
            "as_of_date":     now_ist().date().isoformat(),
            "roce":           ratios.get("roce"),
            "roe":            ratios.get("roe"),
            "stock_pe":       ratios.get("stock_pe"),
            "market_cap":     ratios.get("market_cap"),
            "book_value":     ratios.get("book_value"),
            "dividend_yield": ratios.get("dividend_yield"),
            "debt_to_equity": ratios.get("debt_to_equity"),
            "sales_cagr_3y":  _parse_cagr_3y(data, "Compounded Sales Growth"),
            "profit_cagr_3y": _parse_cagr_3y(data, "Compounded Profit Growth"),
            "source_url":     COMPANY_URL.format(symbol=symbol),
            "captured_at":    int(time.time()),
        }
        return [row]

    def run(self, session, db, context: Mapping[str, Any] | None = None) -> RunReport:
        started = now_ist().astimezone(IST)
        t0 = time.perf_counter()
        report = RunReport(collector=self.name, started_at=started)

        symbols = self.watchlist()
        all_rows: list[Row] = []
        with httpx.Client(headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True) as client:
            for i, symbol in enumerate(symbols):
                report.fetched += 1
                req = Request(path_or_url=symbol, meta={"symbol": symbol})
                try:
                    html = self.fetch(client, symbol)
                    rows = self.normalize(html, req)
                    report.rows_seen += len(rows)
                    all_rows.extend(rows)
                    report.succeeded += 1
                except Exception as e:
                    report.failed += 1
                    report.errors.append(ErrorRecord(
                        request_url=COMPANY_URL.format(symbol=symbol),
                        request_meta={"symbol": symbol},
                        exc_type=type(e).__name__,
                        message=str(e),
                        traceback=traceback.format_exc(),
                    ))
                if self.request_delay and i < len(symbols) - 1:
                    time.sleep(self.request_delay)

        if all_rows:
            try:
                report.persist = self.persist(db, all_rows)
            except Exception as e:
                report.errors.append(ErrorRecord(
                    request_url="<persist>", request_meta={},
                    exc_type=type(e).__name__, message=str(e),
                    traceback=traceback.format_exc(),
                ))

        report.finished_at = now_ist().astimezone(IST)
        report.duration_ms = int((time.perf_counter() - t0) * 1000)
        return report


def _parse_top_ratios(html: str) -> dict[str, float]:
    m = _TOP_RATIOS_RE.search(html)
    section = m.group(0) if m else ""
    out: dict[str, float] = {}
    for name, num in _NAME_NUM_RE.findall(section):
        col = _RATIO_MAP.get(name.strip())
        if col:
            out[col] = _f(num)
    return out


def _parse_cagr_3y(html: str, title: str):
    block = re.search(re.escape(title) + r".*?</table>", html, re.S)
    if not block:
        return None
    m = re.search(r"<td>\s*3 Years:\s*</td>\s*<td>\s*([-\d.]+)\s*%", block.group(0), re.S)
    return _f(m.group(1)) if m else None


def _f(v):
    if v is None:
        return None
    v = str(v).replace(",", "").strip()
    if v in ("", "-"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
