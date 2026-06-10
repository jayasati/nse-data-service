"""Yahoo Finance consensus estimates for NSE symbols (P6, source='yahoo').

Endpoint: ``query2.finance.yahoo.com/v10/finance/quoteSummary/<SYM>.NS
?modules=earningsTrend&crumb=...`` — the public quote API the Yahoo frontend
uses; it requires the cookie+crumb handshake (GET fc.yahoo.com for the cookie,
then /v1/test/getcrumb). Verified live 2026-06-10 (INFY.NS: EPS avg ₹18.66 ×7
analysts, revenue avg ₹477.85B ×12 for the 0q quarter, currency INR).

What it carries: per-quarter **EPS estimate** and **revenue estimate** (INR for
.NS listings) for the current ("0q") and next ("+1q") unreported quarters, with
``endDate`` as the quarter end. No PAT (Moneycontrol covers that) and no
NII/NIM (manual covers that). Coverage thins below large-caps — that's why
yahoo ranks last in ``consensus.SOURCE_RANK``.
"""
from __future__ import annotations

import time
import urllib.parse

import structlog

log = structlog.get_logger()

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_COOKIE_URL = "https://fc.yahoo.com"
_CRUMB_URL = "https://query2.finance.yahoo.com/v1/test/getcrumb"
_SUMMARY_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
_QUARTER_PERIODS = ("0q", "+1q")     # current + next unreported quarter
_SLEEP_BETWEEN_CALLS = 0.7           # be polite
_TIMEOUT = 15.0


def _raw(node) -> float | None:
    """Yahoo wraps numbers as {'raw': x, 'fmt': '...'}; missing nodes are {}."""
    v = (node or {}).get("raw") if isinstance(node, dict) else None
    return float(v) if isinstance(v, (int, float)) else None


def parse_quote_summary(data: dict, symbol: str) -> list[dict]:
    """Pure: quoteSummary JSON → canonical estimate records (₹ crore / ₹ EPS).

    Revenue arrives in rupees (1 cr = 1e7); skipped entirely unless Yahoo says
    the currency is INR — a USD-denominated revenue silently divided by 1e7
    would be exactly the unit bug this table must never carry."""
    try:
        trend = data["quoteSummary"]["result"][0]["earningsTrend"]["trend"]
    except (KeyError, IndexError, TypeError):
        return []
    records: list[dict] = []
    for t in trend:
        if not isinstance(t, dict) or t.get("period") not in _QUARTER_PERIODS:
            continue
        period_ending = t.get("endDate")
        if not period_ending:
            continue
        ee, re_ = t.get("earningsEstimate") or {}, t.get("revenueEstimate") or {}
        eps = _raw(ee.get("avg")) if ee.get("earningsCurrency") in (None, "INR") else None
        rev = _raw(re_.get("avg")) if re_.get("revenueCurrency") == "INR" else None
        rev_cr = rev / 1e7 if rev is not None else None
        if eps is None and rev_cr is None:
            continue
        records.append({
            "symbol": symbol, "period_ending": period_ending,
            "rev_est_cr": rev_cr, "eps_est": eps,
        })
    return records


class _YahooSession:
    """One cookie+crumb handshake shared across a batch."""

    def __init__(self):
        import httpx

        self.client = httpx.Client(
            headers={"User-Agent": _UA}, timeout=_TIMEOUT, follow_redirects=True,
        )
        self._crumb: str | None = None

    def crumb(self) -> str:
        if self._crumb is None:
            try:
                self.client.get(_COOKIE_URL)      # sets the A3 cookie; 404 is fine
            except Exception:  # noqa: BLE001 — cookie host can be flaky
                pass
            r = self.client.get(_CRUMB_URL)
            r.raise_for_status()
            self._crumb = r.text.strip()
        return self._crumb

    def quote_summary(self, ticker: str) -> dict:
        r = self.client.get(
            _SUMMARY_URL.format(ticker=urllib.parse.quote(ticker)),
            params={"modules": "earningsTrend", "crumb": self.crumb()},
        )
        if r.status_code == 401:                  # stale crumb — one re-handshake
            self._crumb = None
            r = self.client.get(
                _SUMMARY_URL.format(ticker=urllib.parse.quote(ticker)),
                params={"modules": "earningsTrend", "crumb": self.crumb()},
            )
        r.raise_for_status()
        return r.json()

    def close(self):
        self.client.close()


def make_yahoo_fetcher(session: _YahooSession | None = None):
    """A ``fetcher(symbol) -> list[dict]`` for ``fetch_and_ingest``.

    NSE symbol → Yahoo ticker is ``<symbol>.NS`` (M&M → M&M.NS, URL-quoted).
    Per-symbol failures raise — fetch_and_ingest logs and continues."""
    sess = session or _YahooSession()

    def fetch(symbol: str) -> list[dict]:
        time.sleep(_SLEEP_BETWEEN_CALLS)
        data = sess.quote_summary(f"{symbol}.NS")
        recs = parse_quote_summary(data, symbol)
        log.info("yahoo_estimates", symbol=symbol, records=len(recs))
        return recs

    return fetch
