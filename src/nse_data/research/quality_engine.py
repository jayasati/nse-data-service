"""P1 — Quality engine (grand-prompt ranking system). Cross-sectional, point-in-time.

Per stock, from the latest result REPORTED as-of the date (point-in-time via
`broadcast_dt`, consolidated-preferred), compute raw fundamental factors; then
percentile-rank each factor WITHIN the stock's sector across the universe and
average → Quality score [0,100]. Higher = stronger/faster-growing business vs peers.

Factors (what extracted_financials cleanly supports point-in-time): revenue & PAT &
operating-profit growth (YoY + QoQ) and margins (net, operating). ROE/ROCE/D-E need
historical balance-sheet equity we don't yet store per-quarter — added later.
Validated by scripts/backtest_quality.py before it earns any composite weight.
"""
from __future__ import annotations

import datetime as _dt

_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
# higher-is-better factors averaged into the engine score
FACTORS = ("rev_yoy", "pat_yoy", "op_yoy", "rev_qoq", "net_margin", "op_margin")


def _bdt_epoch(s: str | None) -> int | None:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return int(_dt.datetime.strptime(s, fmt).replace(tzinfo=_IST).timestamp())
        except ValueError:
            continue
    return None


def _growth(now, prior):
    """YoY/QoQ % — only meaningful when the prior base is positive."""
    if now is None or prior is None or prior <= 0:
        return None
    return (now - prior) / prior * 100.0


def quality_raw(conn, symbol: str, as_of_ep: int) -> dict | None:
    """Raw quality factors from the latest result REPORTED on/before as_of_ep
    (point-in-time). consolidated preferred, standalone fallback. None if no
    usable history."""
    rows = conn.execute(
        "SELECT period_ending, scope, revenue_cr, pat_cr, total_income_cr, "
        "operating_profit_cr, broadcast_dt FROM extracted_financials WHERE symbol=?",
        (symbol,)).fetchall()
    # keep rows reported by as_of; one per period_ending (prefer consolidated)
    by_pe: dict[str, tuple] = {}
    for pe, scope, rev, pat, ti, op, bdt in rows:
        ep = _bdt_epoch(bdt)
        if ep is None or ep > as_of_ep or not pe:
            continue
        cur = by_pe.get(pe)
        if cur is None or (scope == "consolidated" and cur[0] != "consolidated"):
            by_pe[pe] = (scope, rev, pat, ti, op)
    if len(by_pe) < 2:
        return None
    pes = sorted(by_pe)                       # oldest→newest period_ending
    latest = by_pe[pes[-1]]
    qoq = by_pe[pes[-2]]
    # YoY: the period ~4 quarters back (closest period_ending ~365d earlier)
    yoy = None
    try:
        ld = _dt.date.fromisoformat(pes[-1])
        target = (ld - _dt.timedelta(days=365)).isoformat()
        yp = min(pes, key=lambda p: abs((_dt.date.fromisoformat(p) - _dt.date.fromisoformat(target)).days))
        if abs((_dt.date.fromisoformat(yp) - _dt.date.fromisoformat(target)).days) <= 45:
            yoy = by_pe[yp]
    except ValueError:
        pass
    _, rev, pat, ti, op = latest
    f = {
        "rev_yoy": _growth(rev, yoy[1]) if yoy else None,
        "pat_yoy": _growth(pat, yoy[2]) if yoy else None,
        "op_yoy": _growth(op, yoy[4]) if yoy else None,
        "rev_qoq": _growth(rev, qoq[1]),
        "net_margin": (pat / ti * 100.0) if (ti and ti > 0) else None,
        "op_margin": (op / rev * 100.0) if (rev and rev > 0 and op is not None) else None,
    }
    return f if any(v is not None for v in f.values()) else None


def _pctile(value, pool):
    """percentile rank of value within pool (both already non-None)."""
    if not pool:
        return 50.0
    below = sum(1 for x in pool if x < value)
    eq = sum(1 for x in pool if x == value)
    return 100.0 * (below + 0.5 * eq) / len(pool)


def score_universe(conn, symbols, as_of_ep: int, sector_of) -> dict:
    """{symbol: {'score','components'}} — cross-sectional percentile WITHIN sector,
    averaged across factors. Missing factor → neutral (skipped in the mean)."""
    raw = {s: quality_raw(conn, s, as_of_ep) for s in symbols}
    raw = {s: f for s, f in raw.items() if f}
    # group raw factor values by sector for percentile pools
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
            if len(pool) < 6:                  # too few sector peers → all-market pool
                pool = [x for (sk, fk), lst in pools.items() if fk == fac for x in lst] or pool
            comps[fac] = round(_pctile(v, pool), 1)
        if comps:
            out[s] = {"score": round(sum(comps.values()) / len(comps), 1), "components": comps}
    return out
