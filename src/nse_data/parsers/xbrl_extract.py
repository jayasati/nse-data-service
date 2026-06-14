"""XBRL-first financial extraction (deterministic, free, authoritative).

The LLM vision path reads numbers off the P&L image — fast to build but
fallible on dense/odd layouts. NSE, though, already publishes the company's own
structured submission as XBRL with every result. This module resolves that XBRL
URL from the collected feeds, downloads + parses it into the SAME
ExtractionResult shape the LLM path returns, so it can be the PRIMARY extractor
with the vision path as a fallback only when no parseable XBRL exists.

Sources of the URL (preferred first):
  * raw_integrated_filings — NSE's modern "Integrated Filing- Financials", one
    row per (qe_date, scope) with ixbrl_url/xbrl_url. The whole top-1000 era of
    results lives here; every row carries an xbrl_url.
  * raw_financial_results — the older per-result feed's xbrl_url, as a fallback.

Matched by symbol + the reported quarter (the most recent quarter-end on/before
the result's broadcast date) — robust to the integrated feed's missing
broadcast_dt.

    res = extract_via_xbrl(conn, symbol="WAAREEENER", broadcast_dt="30-Apr-2026 ...")
    # res.strategy == "xbrl", res.fields/res.consolidated populated, cost 0
"""
from __future__ import annotations

import datetime as _dt
import sqlite3

import structlog

from .financial_extractor import ExtractionResult
from .xbrl_financials import parse_xbrl

log = structlog.get_logger()

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_QE_MONTHS = {3: "MAR", 6: "JUN", 9: "SEP", 12: "DEC"}


def _broadcast_date(s: str | None) -> _dt.date | None:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return _dt.datetime.strptime(s[:len(fmt) + 4].strip(), fmt).date()
        except ValueError:
            continue
    try:
        return _dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def _quarter_end_on_or_before(d: _dt.date) -> _dt.date:
    ends = [(3, 31), (6, 30), (9, 30), (12, 31)]
    cands = [_dt.date(d.year, m, day) for m, day in ends if _dt.date(d.year, m, day) <= d]
    cands.append(_dt.date(d.year - 1, 12, 31))
    return max(cands)


def _qe_label(qe: _dt.date) -> str:
    """date(2026,3,31) -> '31-MAR-2026' (the integrated-filing qe_date form)."""
    return f"{qe.day:02d}-{_QE_MONTHS[qe.month]}-{qe.year}"


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,),
    ).fetchone() is not None


def _resolve_via_api(session, symbol: str, qe: _dt.date) -> dict:
    """Per-symbol XBRL URLs from NSE's NextApi (authoritative + current).

    The global integrated-filing feed is incomplete and the old results feed
    went stale when NSE moved to integrated filings, so the reliable source is
    the per-symbol quote API. Tries the integrated feed first (modern results,
    both scopes), then the financial-results feed (older quarters)."""
    from urllib.parse import quote
    out: dict = {"standalone": None, "consolidated": None}
    esym = quote(symbol, safe="")        # symbols like M&M / J&KBANK break the query otherwise
    ref = f"https://www.nseindia.com/get-quote/equity/{symbol}"

    def _scope(v: str | None) -> str:
        return "consolidated" if (v or "").strip().lower().startswith("cons") \
            and "non" not in (v or "").lower() else "standalone"

    # 1. integrated filing (modern) — gfrQuaterEnded / gfrConsolidated / gfrXbrlFname
    try:
        data = session.get_json(
            "nextapi",
            f"/api/NextApi/apiClient/GetQuoteApi?functionName=getIntegratedFilingData&symbol={esym}",
            referer=ref)
    except Exception as e:  # noqa: BLE001
        log.warning("xbrl_api_failed", fn="integrated", symbol=symbol, error=str(e))
        data = None
    # Newest filing first: a company can re-file a corrected result for the same
    # quarter (e.g. ENRIN), and only the latest submission matches the headline.
    for rec in sorted(data or [], key=lambda r: _file_ts(r.get("gfSystym")), reverse=True):
        if _api_date(rec.get("gfrQuaterEnded")) != qe:
            continue
        scope = _scope(rec.get("gfrConsolidated"))
        if out[scope] is None and rec.get("gfrXbrlFname"):
            out[scope] = rec["gfrXbrlFname"]
    if out["standalone"] or out["consolidated"]:
        return out

    # 2. older financial-results feed — to_date / consolidated / xbrl_attachment
    try:
        data = session.get_json(
            "nextapi",
            f"/api/NextApi/apiClient/GetQuoteApi?functionName=getFinancialResultData&symbol={esym}&marketApiType=equities&noOfRecords=8",
            referer=ref)
    except Exception as e:  # noqa: BLE001
        log.warning("xbrl_api_failed", fn="financial", symbol=symbol, error=str(e))
        data = None
    for rec in data or []:
        if _api_date(rec.get("to_date")) != qe:
            continue
        scope = _scope(rec.get("consolidated"))
        if out[scope] is None and rec.get("xbrl_attachment"):
            out[scope] = rec["xbrl_attachment"]
    return out


def _file_ts(s: str | None) -> _dt.datetime:
    """Filing timestamp ('08-May-2026 16:42') for newest-first ordering."""
    if s:
        for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y"):
            try:
                return _dt.datetime.strptime(s.strip(), fmt)
            except ValueError:
                continue
    return _dt.datetime.min


def _api_date(s: str | None) -> _dt.date | None:
    """Parse NSE NextApi date strings ('31 Mar 2026', '31 Dec 2024')."""
    if not s:
        return None
    for fmt in ("%d %b %Y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return _dt.datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def resolve_xbrl_urls(
    conn: sqlite3.Connection, symbol: str, broadcast_dt: str | None, *, session=None,
) -> dict:
    """{'standalone': url|None, 'consolidated': url|None} for the result's quarter.

    Primary source is NSE's per-symbol API (when a ``session`` is given) —
    authoritative and complete. Falls back to the local integrated-filings /
    financial-results feeds (which are partial/stale) when no session or the API
    misses.
    """
    out: dict = {"standalone": None, "consolidated": None}
    bdate = _broadcast_date(broadcast_dt)
    qe = _quarter_end_on_or_before(bdate) if bdate else None

    if session is not None and qe is not None:
        out = _resolve_via_api(session, symbol, qe)
        if out["standalone"] or out["consolidated"]:
            return out

    if _has_table(conn, "raw_integrated_filings") and qe is not None:
        rows = conn.execute(
            "SELECT consolidated, xbrl_url, created_at FROM raw_integrated_filings "
            "WHERE symbol = ? AND UPPER(qe_date) = ? AND xbrl_url IS NOT NULL "
            "AND filing_type LIKE '%inancial%' "
            "ORDER BY created_at DESC",
            (symbol, _qe_label(qe)),
        ).fetchall()
        for scope_raw, url, _ in rows:
            scope = "consolidated" if (scope_raw or "").strip().lower().startswith("cons") \
                else "standalone"
            if out[scope] is None:        # newest row per scope wins
                out[scope] = url

    if out["standalone"] is None and out["consolidated"] is None \
            and _has_table(conn, "raw_financial_results") and qe is not None:
        row = conn.execute(
            "SELECT xbrl_url FROM raw_financial_results "
            "WHERE symbol = ? AND to_date LIKE ? AND xbrl_url IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (symbol, f"%{qe.year}%"),
        ).fetchone()
        if row:
            out["standalone"] = row[0]
    return out


def _http_get(url: str) -> bytes:
    import httpx
    r = httpx.get(url, headers={"User-Agent": _UA, "Referer": "https://www.nseindia.com/"},
                  timeout=30, follow_redirects=True)
    r.raise_for_status()
    return r.content


def extract_via_xbrl(
    conn: sqlite3.Connection, symbol: str, broadcast_dt: str | None, *,
    session=None, fetch=None,
) -> ExtractionResult | None:
    """Deterministic ExtractionResult from the result's XBRL, or None if no
    parseable XBRL is available (caller then falls back to the LLM path).
    ``session`` (SessionManager) enables the authoritative per-symbol NSE URL
    lookup; without it, only the local feeds are consulted."""
    urls = resolve_xbrl_urls(conn, symbol, broadcast_dt, session=session)
    # Distinct URLs — an integrated XBRL is one scope per file (NatureOfReport),
    # so standalone and consolidated are usually two different files; dedupe in
    # case the feed pointed both slots at the same one.
    distinct = list(dict.fromkeys(u for u in (urls["standalone"], urls["consolidated"]) if u))
    if not distinct:
        return None
    fetch = fetch or _http_get

    fields: dict = {}
    consolidated: dict = {}
    period: str | None = None
    for url in distinct:
        try:
            parsed = parse_xbrl(fetch(url))
        except Exception as e:  # noqa: BLE001 — a bad/blocked XBRL just means LLM fallback
            log.warning("xbrl_fetch_failed", symbol=symbol, error=str(e))
            continue
        if not parsed or not parsed.get("fields"):
            continue
        period = period or parsed.get("period_ending")
        # Slot by the XBRL's OWN declared scope, not the feed's label.
        if parsed.get("scope") == "consolidated":
            consolidated = parsed["fields"]
        else:
            fields = parsed["fields"]

    if not (fields or consolidated):
        return None
    log.info("xbrl_extracted", symbol=symbol, period=period,
             standalone=bool(fields), consolidated=bool(consolidated))
    return ExtractionResult(
        fields=fields, consolidated=consolidated, period_ending=period,
        strategy="xbrl", confidence=1.0, llm_cost_usd=0.0,
    )
