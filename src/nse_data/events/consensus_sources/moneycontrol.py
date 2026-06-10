"""Moneycontrol consensus estimates (P6, source='moneycontrol').

Endpoint: ``api.moneycontrol.com/mcapi/v1/stock/estimates/earning-forecast
?scId=<id>&ex=N&frequency=3&financialType=C&deviceType=W`` — the JSON API the
Moneycontrol app/frontend uses. Verified live 2026-06-10 (INFY/scId=IT: Jun-26
quarter revenue avg ₹47,785 cr — matching Yahoo to the crore — PAT avg
₹7,701 cr, EPS 19). ``frequency=3`` = quarterly; rows whose ``actual`` is
empty are the unreported (estimate-only) quarters we ingest.

What it carries that Yahoo doesn't: **PAT estimates** (₹ crore). The scId is
resolved once per symbol via the public autosuggest endpoint and cached.

This is the fragile leg of the three sources (unofficial API; can change or
start gating without notice) — every failure degrades per-symbol and the
nightly job carries on with manual + yahoo.
"""
from __future__ import annotations

import datetime as _dt
import time

import structlog

log = structlog.get_logger()

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_SUGGEST_URL = ("https://www.moneycontrol.com/mccode/common/"
                "autosuggestion_solr.php")
_FORECAST_URL = ("https://api.moneycontrol.com/mcapi/v1/stock/estimates/"
                 "earning-forecast")
_SLEEP_BETWEEN_CALLS = 1.0
_TIMEOUT = 15.0

_MONTHS = {m: i for i, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), start=1)}

# Known NSE symbol → Moneycontrol scId. Seeded with verified large-caps;
# anything not listed is resolved via autosuggest (and cached per process).
SC_ID_OVERRIDES: dict[str, str] = {
    "INFY": "IT",
}


def _month_end(name: str, year: int) -> _dt.date:
    m = _MONTHS[name]
    nxt = _dt.date(year + (m == 12), m % 12 + 1, 1)
    return nxt - _dt.timedelta(days=1)


def _period_from_label(label: str) -> str | None:
    """'Jun 2026' → '2026-06-30' (the quarter end)."""
    try:
        mon, year = label.strip().split()
        return _month_end(mon[:3].title(), int(year)).isoformat()
    except (ValueError, KeyError):
        return None


def _avg(row: dict) -> float | None:
    v = str(row.get("avg") or "").replace(",", "").strip()
    try:
        return float(v)
    except ValueError:
        return None


def parse_earning_forecast(data: dict, symbol: str) -> list[dict]:
    """Pure: earning-forecast JSON → canonical estimate records (₹ cr / ₹ EPS).

    Only unreported quarters (``actual`` empty) become estimates — once the
    print is out Moneycontrol fills ``actual`` and the row stops being a
    forecast."""
    if not isinstance(data, dict) or data.get("success") != 1:
        return []
    blocks = data.get("data") or {}
    by_period: dict[str, dict] = {}
    for key, canon in (("revenue", "rev_est_cr"), ("netProfit", "pat_est_cr"),
                       ("eps", "eps_est")):
        for row in blocks.get(key) or []:
            if not isinstance(row, dict) or str(row.get("actual") or "").strip():
                continue   # already reported — not an estimate any more
            period = _period_from_label(str(row.get("date") or ""))
            v = _avg(row)
            if period is None or v is None:
                continue
            by_period.setdefault(period, {"symbol": symbol, "period_ending": period})[canon] = v
    return [r for r in by_period.values()
            if any(r.get(k) is not None for k in ("rev_est_cr", "pat_est_cr", "eps_est"))]


def resolve_sc_id(client, symbol: str) -> str | None:
    """NSE symbol → Moneycontrol scId via autosuggest; the NSE symbol appears
    verbatim inside the suggestion's display name (\"INE009A01021, INFY, 500209\")."""
    r = client.get(
        _SUGGEST_URL,
        params={"classic": "true", "query": symbol, "type": "1", "format": "json"},
    )
    r.raise_for_status()
    rows = r.json()
    needle = f", {symbol.upper()},"
    for row in rows if isinstance(rows, list) else []:
        if needle in str(row.get("pdt_dis_nm") or "").upper() and row.get("sc_id"):
            return str(row["sc_id"])
    return None


def make_moneycontrol_fetcher(client=None, *, financial_type: str = "C"):
    """A ``fetcher(symbol) -> list[dict]`` for ``fetch_and_ingest``.

    ``financial_type``: 'C' consolidated (default) / 'S' standalone — matches
    the scope most estimates are published for. scIds cache per fetcher."""
    if client is None:
        import httpx

        client = httpx.Client(
            headers={"User-Agent": _UA}, timeout=_TIMEOUT, follow_redirects=True,
        )
    sc_ids: dict[str, str] = dict(SC_ID_OVERRIDES)

    def fetch(symbol: str) -> list[dict]:
        time.sleep(_SLEEP_BETWEEN_CALLS)
        sc_id = sc_ids.get(symbol) or resolve_sc_id(client, symbol)
        if not sc_id:
            log.warning("mc_scid_unresolved", symbol=symbol)
            return []
        sc_ids[symbol] = sc_id
        r = client.get(
            _FORECAST_URL,
            params={"scId": sc_id, "ex": "N", "frequency": "3",
                    "financialType": financial_type, "deviceType": "W"},
        )
        r.raise_for_status()
        recs = parse_earning_forecast(r.json(), symbol)
        log.info("mc_estimates", symbol=symbol, sc_id=sc_id, records=len(recs))
        return recs

    return fetch
