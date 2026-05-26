"""
GIFT Nifty (NSE IX) — pre-open predictor of the NSE cash open.

Source: https://www.nseix.com/api/nifty-market-rate — NSE International Exchange
REST JSON, no token required. Polled every 30s during 06:30–09:15 IST (research
Pillar 2: GIFT Nifty is the strongest signal for how Nifty will open).

External source: fetched with httpx, outside the NSE SessionManager (nseix.com
is a different host — no NSE cookie warm-up applies). run() is overridden for
the external fetch while keeping the RunReport contract; persist() is
SnapshotCollector's upsert on (index_name, as_of) so each 30s poll appends a
tick to the morning series.
"""

from __future__ import annotations

import time
import traceback
from typing import Any, Mapping, Sequence

import httpx

from ..scheduler.market_hours import IST, now_ist
from .base import ErrorRecord, Request, Row, RunReport, SnapshotCollector

NIFTY_RATE_URL = "https://www.nseix.com/api/nifty-market-rate"
_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.nseix.com/"}
_TIMEOUT = 10.0


class GiftNifty(SnapshotCollector):
    name = "gift_nifty"
    table = "raw_gift_nifty"
    pk_cols = ("index_name", "as_of")

    def plan(self, context: Mapping[str, Any] | None = None) -> Sequence[Request]:
        return []   # run() is overridden for the external fetch

    def fetch(self, client: httpx.Client) -> Any:
        """The single NSE IX request. Isolated so tests override without network."""
        resp = client.get(NIFTY_RATE_URL)
        resp.raise_for_status()
        return resp.json()

    def normalize(self, data: Any, request: Request) -> list[Row]:
        # Endpoint returns a list of index rows (currently just Nifty 50).
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return []

        as_of = int(time.time())
        rows: list[Row] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            index_name = (item.get("OI_INDEX_NAME") or "").strip()
            if not index_name:
                continue
            rows.append({
                "index_name":    index_name,
                "as_of":         as_of,
                "curr_value":    _f(item.get("CURRVALUE")),
                "open_value":    _f(item.get("OI_OPEN_INDEX_VAL")),
                "close_value":   _f(item.get("OI_CLOSE_INDEX_VAL")),
                "change":        _f(item.get("CHANGE")),
                "pct_change":    _f(item.get("PERCHANGE")),
                "nse_timestamp": item.get("FULLTIMESTAMP"),
                "captured_at":   as_of,
            })
        return rows

    def run(self, session, db, context: Mapping[str, Any] | None = None) -> RunReport:
        started = now_ist().astimezone(IST)
        t0 = time.perf_counter()
        report = RunReport(collector=self.name, started_at=started)

        all_rows: list[Row] = []
        report.fetched += 1
        try:
            with httpx.Client(headers=_HEADERS, timeout=_TIMEOUT) as client:
                data = self.fetch(client)
            all_rows = self.normalize(data, Request(path_or_url=NIFTY_RATE_URL))
            report.rows_seen += len(all_rows)
            report.succeeded += 1
        except Exception as e:
            report.failed += 1
            report.errors.append(ErrorRecord(
                request_url=NIFTY_RATE_URL, request_meta={},
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


def _f(v):
    """Coerce to float; strips thousands commas ('23,913.70' -> 23913.70)."""
    if v is None:
        return None
    v = str(v).replace(",", "").strip()
    if v in ("", "-"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
