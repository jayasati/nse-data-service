"""P5 — Catalyst engine (order-driven), cross-sectional + point-in-time. The
headline factor is the **Order Impact Ratio**: a freshly-won order's value
relative to company size — ₹700cr for a ₹500cr-revenue company is
transformational; ₹50cr for a ₹10,000cr one is noise. "Don't treat all orders
equally."

Per stock, as-of the date: scan order-win announcements DISCLOSED in the last
LOOKBACK days (point-in-time via broadcast_epoch), extract each order's ₹-crore
value (parsers.order_amount), keep the largest, and score impact =
order_value / TTM-revenue (primary) and / market-cap (secondary), time-decayed
(orders fade). Cross-sectional percentile within sector → Catalyst [0,100].

Event-driven + SPARSE: only names with a recent extractable order score; the rest
are absent (neutral 50 in the roll-up), never penalised. Coverage is bounded by
the announcement parser (order PDFs are text-extracted for the top-1000 universe;
~69% of those yield an amount with the regex extractor). Validated by
scripts/backtest_catalyst.py before it earns any composite weight.
"""
from __future__ import annotations

import datetime as _dt

from ..parsers.order_amount import extract_order_value_cr
from ..signals.detect import is_order_win_subject

_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
LOOKBACK_DAYS = 60          # an order is a "recent catalyst" for ~2 months
_HALFLIFE_DAYS = 30.0       # impact decays by half every 30d
FACTORS = ("order_impact_rev", "order_impact_mcap")


def _bdt_epoch(s: str | None) -> int | None:
    if not s:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return int(_dt.datetime.strptime(s.strip(), fmt).replace(tzinfo=_IST).timestamp())
        except ValueError:
            continue
    return None


def _ttm_revenue_cr(conn, symbol: str, as_of_ep: int) -> float | None:
    """Trailing revenue from the last ≤4 quarters REPORTED by as_of (point-in-time,
    consolidated preferred). Annualised if fewer than 4 quarters are available."""
    by_pe: dict[str, tuple] = {}
    for pe, scope, rev, bdt in conn.execute(
            "SELECT period_ending, scope, revenue_cr, broadcast_dt "
            "FROM extracted_financials WHERE symbol=?", (symbol,)):
        ep = _bdt_epoch(bdt)
        if ep is None or ep > as_of_ep or not pe or rev is None:
            continue
        cur = by_pe.get(pe)
        if cur is None or (scope == "consolidated" and cur[0] != "consolidated"):
            by_pe[pe] = (scope, rev)
    if not by_pe:
        return None
    revs = [by_pe[p][1] for p in sorted(by_pe)[-4:]]
    return sum(revs) if len(revs) == 4 else sum(revs) / len(revs) * 4.0


def _mcap_cr(conn, symbol: str) -> float | None:
    """Market cap (₹cr): live quote metadata first, else the tradeable_universe
    snapshot (raw_quote_metadata is sparse — Akamai-blocked)."""
    for sql in ("SELECT market_cap_cr FROM raw_quote_metadata WHERE symbol=?",
                "SELECT market_cap_cr FROM tradeable_universe WHERE symbol=?"):
        try:
            r = conn.execute(sql, (symbol,)).fetchone()
        except Exception:  # noqa: BLE001 — table may be absent
            r = None
        if r and r[0]:
            return r[0]
    return None


def catalyst_raw(conn, symbol: str, as_of_ep: int) -> dict | None:
    """Order-impact factors from the largest order-win DISCLOSED in the lookback
    window on/before as_of. None if no extractable recent order."""
    lo = as_of_ep - LOOKBACK_DAYS * 86400
    best: tuple[float, int] | None = None        # (order_cr, disclosed_ep)
    for subj, det, pt, bep, bdt in conn.execute(
            "SELECT subject, details, pdf_text, broadcast_epoch, broadcast_dt "
            "FROM raw_announcements WHERE symbol=? AND broadcast_epoch BETWEEN ? AND ?",
            (symbol, lo, as_of_ep)):
        if not is_order_win_subject(subj):
            continue
        ep = bep or _bdt_epoch(bdt)              # epoch column, else parse the string
        if ep is None or not (lo <= ep <= as_of_ep):
            continue
        r = extract_order_value_cr(" ".join(x for x in (subj, det, pt) if x))
        if r and (best is None or r[0] > best[0]):
            best = (r[0], ep)
    if best is None:
        return None
    order_cr, ep = best
    decay = 0.5 ** (((as_of_ep - ep) / 86400) / _HALFLIFE_DAYS)
    ttm, mcap = _ttm_revenue_cr(conn, symbol, as_of_ep), _mcap_cr(conn, symbol)
    f = {}
    if ttm and ttm > 0:
        f["order_impact_rev"] = round(order_cr / ttm * decay, 4)
    if mcap and mcap > 0:
        f["order_impact_mcap"] = round(order_cr / mcap * decay, 4)
    return f or None


def _pctile(value, pool):
    if not pool:
        return 50.0
    below = sum(1 for x in pool if x < value)
    eq = sum(1 for x in pool if x == value)
    return 100.0 * (below + 0.5 * eq) / len(pool)


def score_universe(conn, symbols, as_of_ep: int, sector_of) -> dict:
    """{symbol: {'score','components'}} — cross-sectional percentile WITHIN sector.
    Only names with a recent extractable order appear (event-driven + sparse)."""
    raw = {s: catalyst_raw(conn, s, as_of_ep) for s in symbols}
    raw = {s: f for s, f in raw.items() if f}
    pools: dict[tuple, list] = {}
    for s, f in raw.items():
        sec = sector_of(s)
        for fac in FACTORS:
            v = f.get(fac)
            if v is not None:
                pools.setdefault((sec, fac), []).append(v)
    out = {}
    for s, f in raw.items():
        sec = sector_of(s)
        comps = {}
        for fac in FACTORS:
            v = f.get(fac)
            if v is None:
                continue
            pool = pools.get((sec, fac)) or [v]
            if len(pool) < 6:
                pool = [x for (_s, fk), lst in pools.items() if fk == fac for x in lst] or pool
            comps[fac] = round(_pctile(v, pool), 1)
        if comps:
            out[s] = {"score": round(sum(comps.values()) / len(comps), 1), "components": comps}
    return out
