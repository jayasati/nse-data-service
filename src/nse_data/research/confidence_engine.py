"""P6 — Confidence engine: per-stock DATA-COVERAGE score [0,100], point-in-time.

NOT sector-relative and NOT a return factor — it answers "how much do we actually
know about this name as-of the date?" and is reported ALONGSIDE the composite: low
confidence → wider expected-return band + smaller suggested size, never a penalty
on the score itself (absence is not evidence). Coverage weights (arch doc §G):

    price 20 + financials(≥4q) 25 + shareholding 15 + announcements/news 15
    + analyst 10 + valuation 15   → capped 100

Partial coverage scales (1–3 quarters of financials earns less than the full 25).
Every check is point-in-time (only data knowable on/before as_of). Same
score_universe(conn, symbols, as_of_ep, sector_of) signature as the other engines
so the output card can call it uniformly; sector_of is unused.
"""
from __future__ import annotations

import datetime as _dt

from .ownership_engine import _disclosed_ep

_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
_YEAR = 365 * 24 * 3600


def _dt_epoch(s: str | None) -> int | None:
    if not s:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return int(_dt.datetime.strptime(s.strip(), fmt).replace(tzinfo=_IST).timestamp())
        except ValueError:
            continue
    return None


def _fin_quarters(conn, symbol: str, as_of_ep: int) -> int:
    """Distinct quarters REPORTED on/before as_of (point-in-time via broadcast_dt)."""
    pes = set()
    for pe, bdt in conn.execute(
            "SELECT period_ending, broadcast_dt FROM extracted_financials WHERE symbol=?",
            (symbol,)):
        ep = _dt_epoch(bdt)
        if ep is not None and ep <= as_of_ep and pe:
            pes.add(pe)
    return len(pes)


def _shp_quarters(conn, symbol: str, as_of_ep: int) -> int:
    """Shareholding quarters DISCLOSED on/before as_of (filename timestamp)."""
    n = 0
    for qe, url in conn.execute(
            "SELECT qe_date, xbrl_url FROM raw_shareholding_quarterly WHERE symbol=?",
            (symbol,)):
        ep = _disclosed_ep(url, qe)
        if ep is not None and ep <= as_of_ep:
            n += 1
    return n


def _candle_days(conn, symbol: str, as_of_ep: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM raw_intraday_candles "
        "WHERE symbol=? AND interval='day' AND ts<=?", (symbol, as_of_ep)).fetchone()[0]


def _has_announcement(conn, symbol: str, as_of_ep: int) -> bool:
    lo = as_of_ep - _YEAR
    for (bdt,) in conn.execute(
            "SELECT broadcast_dt FROM raw_announcements WHERE symbol=?", (symbol,)):
        ep = _dt_epoch(bdt)
        if ep is not None and lo <= ep <= as_of_ep:
            return True
    return False


def _has_news(conn, symbol: str, as_of_ep: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM raw_news WHERE symbol=? AND published_epoch IS NOT NULL "
        "AND published_epoch BETWEEN ? AND ? LIMIT 1",
        (symbol, as_of_ep - _YEAR, as_of_ep)).fetchone() is not None


def _has_analyst(conn, symbol: str, as_of_ep: int) -> bool:
    # rating actions are PIT-dated; analyst feed rows lack a clean epoch, so for
    # this soft 10-pt coverage flag we accept their existence as best-effort.
    for (bdt,) in conn.execute(
            "SELECT broadcast_dt FROM raw_rating_actions WHERE symbol=?", (symbol,)):
        ep = _dt_epoch(bdt)
        if ep is not None and ep <= as_of_ep:
            return True
    return conn.execute(
        "SELECT 1 FROM raw_analyst_ratings WHERE symbol=? LIMIT 1", (symbol,)).fetchone() is not None


def confidence_raw(conn, symbol: str, as_of_ep: int) -> dict:
    """{'score','components'} — absolute coverage [0,100] as-of the date."""
    nd = _candle_days(conn, symbol, as_of_ep)
    fq = _fin_quarters(conn, symbol, as_of_ep)
    sq = _shp_quarters(conn, symbol, as_of_ep)
    comps = {
        "price":       20 if nd >= 120 else 15 if nd >= 60 else 10 if nd >= 20 else 5 if nd else 0,
        "financials":  25 if fq >= 4 else 18 if fq == 3 else 12 if fq == 2 else 6 if fq == 1 else 0,
        "shareholding": 15 if sq >= 2 else 8 if sq == 1 else 0,
        "news":        (8 if _has_announcement(conn, symbol, as_of_ep) else 0)
                       + (7 if _has_news(conn, symbol, as_of_ep) else 0),
        "analyst":     10 if _has_analyst(conn, symbol, as_of_ep) else 0,
        # PE is computable once we have ≥1 reported quarter (EPS) and a price.
        "valuation":   15 if (fq >= 1 and nd > 0) else 0,
    }
    return {"score": min(100, sum(comps.values())), "components": comps}


def score_universe(conn, symbols, as_of_ep: int, sector_of=None) -> dict:
    """{symbol: {'score','components'}} — absolute coverage, not cross-sectional."""
    return {s: confidence_raw(conn, s, as_of_ep) for s in symbols}
