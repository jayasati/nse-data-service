"""
NSDL FPI custodian flow — "Daily Trends in FPI Investments" (EOD).

NSDL is SEBI's designated custodian-side monitor of FPI activity. This collector
scrapes its only daily feed — the Daily Trends report — which gives FPI gross
purchase / gross sale / net flow broken down by ASSET CLASS × INVESTMENT ROUTE
(Equity, Debt-General Limit, Debt-VRR, Debt-FAR, Hybrid, Mutual Funds, AIFs;
each split into Stock Exchange / Primary market & others / Sub-total, plus a
grand Total). This is the custody-side flow — richer than the exchange-side
`fii_dii` collector (which has no debt/MF breakdown). NSDL does NOT publish a
daily per-custodian flow; per-custodian data is the monthly AUC report.

Source: https://www.fpi.nsdl.co.in/web/Reports/Latest.aspx — an HTML report, no
JSON API. External: fetched via httpx with a browser UA, OUTSIDE the NSE
SessionManager (NSDL ≠ NSE; no cookie warm-up / rate limiter / circuit applies),
exactly like `screener` and `macro`. The page's anti-scrape JS (disabled
right-click / F12 / Ctrl+U) is client-side only and irrelevant to httpx.

run() is overridden for the external fetch while preserving the RunReport
contract. Persistence is SnapshotCollector's upsert on
(as_of_date, asset_class, investment_route), so the daily EOD run is idempotent
and a later confirmed revision overwrites the provisional figures.

Parse-health guard: if the report table or its date can't be found, or no data
rows parse (NSDL markup drift), run() records a NsdlFpiParseError on the report
instead of silently persisting nothing — making breakage loud/monitorable, the
same philosophy as ScreenerFundamentals.
"""

from __future__ import annotations

import re
import time
import traceback
from datetime import datetime
from typing import Any, Mapping, Sequence

import httpx

from ..scheduler.market_hours import IST, now_ist
from .base import ErrorRecord, Request, Row, RunReport, SnapshotCollector

LATEST_URL = "https://www.fpi.nsdl.co.in/web/Reports/Latest.aspx"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://www.fpi.nsdl.co.in/web/Reports/ReportsListing.aspx",
}
_TIMEOUT = 25.0

# "Daily Trends in FPI Investments on 26-May-2026"
_DATE_RE = re.compile(
    r"Daily Trends in FPI Investments on\s*(\d{1,2}-[A-Za-z]{3}-\d{4})", re.I
)
_TR_RE = re.compile(r"<tr.*?</tr>", re.I | re.S)
_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")


class NsdlFpiParseError(Exception):
    """The Latest.aspx markup didn't match the expected report shape."""


class NsdlFpi(SnapshotCollector):
    name = "nsdl_fpi"
    table = "raw_nsdl_fpi_daily"
    pk_cols = ("as_of_date", "asset_class", "investment_route")

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return []  # run() is overridden for the external fetch

    def fetch(self, client: httpx.Client) -> str:
        """The single NSDL request. Isolated so tests override without network."""
        resp = client.get(LATEST_URL)
        resp.raise_for_status()
        return resp.text

    def normalize(self, data: Any, request: Request) -> list[Row]:
        html = data if isinstance(data, str) else ""
        date_m = _DATE_RE.search(html)
        if not date_m:
            raise NsdlFpiParseError("report date header not found")
        date_label = date_m.group(1)
        as_of_date = datetime.strptime(date_label, "%d-%b-%Y").date().isoformat()

        table = _report_table(html)
        if table is None:
            raise NsdlFpiParseError("report table (with Gross Purchases header) not found")

        captured_at = int(time.time())
        rows: list[Row] = []
        current_asset: str | None = None
        conversion: float | None = None

        for tr in _TR_RE.findall(table):
            cells = [_text(c) for c in _CELL_RE.findall(tr)]
            cells = [c for c in cells if c != ""]
            parsed = _classify_row(cells, current_asset, conversion)
            if parsed is None:
                continue
            asset, route, gp, gs, net, net_usd, conv, new_asset = parsed
            current_asset = new_asset
            if conv is not None:
                conversion = conv
            rows.append({
                "as_of_date":        as_of_date,
                "asset_class":       asset,
                "investment_route":  route,
                "gross_purchase_cr": gp,
                "gross_sales_cr":    gs,
                "net_cr":            net,
                "net_usd_mn":        net_usd,
                "conversion_rate":   conversion,
                "report_date_label": date_label,
                "captured_at":       captured_at,
            })

        if not rows:
            raise NsdlFpiParseError("report table parsed to zero data rows")
        return rows

    def run(self, session, db, context: Mapping[str, Any] | None = None) -> RunReport:
        started = now_ist().astimezone(IST)
        t0 = time.perf_counter()
        report = RunReport(collector=self.name, started_at=started)

        all_rows: list[Row] = []
        report.fetched += 1
        try:
            with httpx.Client(headers=_HEADERS, timeout=_TIMEOUT) as client:
                html = self.fetch(client)
            all_rows = self.normalize(html, Request(path_or_url=LATEST_URL))
            report.rows_seen += len(all_rows)
            report.succeeded += 1
        except Exception as e:
            report.failed += 1
            report.errors.append(ErrorRecord(
                request_url=LATEST_URL, request_meta={},
                exc_type=type(e).__name__, message=str(e),
                traceback=traceback.format_exc(),
            ))

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


# ---- HTML parsing helpers --------------------------------------------------

def _report_table(html: str) -> str | None:
    """Return the <table> whose header carries the 'Gross Purchases' column."""
    best = None
    for m in re.finditer(r"<table.*?</table>", html, re.I | re.S):
        seg = m.group(0)
        if "Gross Purchases" in seg and (best is None or len(seg) > len(best)):
            best = seg
    return best


def _classify_row(cells, current_asset, conversion):
    """Map one row's cells to (asset, route, gp, gs, net, net_usd, conv, new_asset).

    The report uses rowspans, so cell count signals the row shape:
      8 cells -> first data row: [date, asset, route, gp, gs, net, net_usd, conv]
      6 cells -> first route row of a new asset class: [asset, route, gp..net_usd]
      5 cells -> subsequent route / sub-total / grand total row: [route, gp..net_usd]
    Header / title / note rows (numbers don't parse) return None.
    """
    n = len(cells)
    if n == 8:
        asset, route = cells[1], cells[2]
        nums = _money4(cells[3:7])
        conv = _rate(cells[7])
        if nums is None:
            return None  # header row
        return (asset, route, *nums, conv, asset)
    if n == 6:
        asset, route = cells[0], cells[1]
        nums = _money4(cells[2:6])
        if nums is None:
            return None
        return (asset, route, *nums, None, asset)
    if n == 5:
        route = cells[0]
        nums = _money4(cells[1:5])
        if nums is None:
            return None
        # The grand-total row stands alone — not part of the current asset class.
        asset = "Total" if route.strip().lower() == "total" else current_asset
        if asset is None:
            return None
        return (asset, route, *nums, None, current_asset)
    return None


def _money4(cells):
    """Parse exactly four ₹/USD cells; None if any isn't numeric (= not a data row)."""
    if len(cells) != 4:
        return None
    out = [_money(c) for c in cells]
    if any(v is None for v in out):
        return None
    return out


def _money(s: str):
    """'(193.61)' -> -193.61 ; '12,847.68' -> 12847.68 ; '0.00' -> 0.0."""
    s = (s or "").strip().replace(",", "")
    if s in ("", "-"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").strip()
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return -v if neg else v


def _rate(s: str):
    """'Rs.95.2047' -> 95.2047."""
    s = (s or "").replace("Rs.", "").replace(",", "").strip()
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _text(cell_html: str) -> str:
    import html as _ihtml
    return _ihtml.unescape(_TAG_RE.sub(" ", cell_html)).strip()
