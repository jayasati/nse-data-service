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
# higher-is-better factors averaged into the engine score. The BS ratios
# (roe/roce/current_ratio/asset_turnover) come from the balance sheet (migration
# 081, half-yearly) — present when extracted, skipped (neutral) when not.
FACTORS = ("rev_yoy", "pat_yoy", "op_yoy", "rev_qoq", "net_margin", "op_margin",
           "roe", "roce", "current_ratio", "asset_turnover")


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
        "operating_profit_cr, pbt_cr, finance_cost_cr, equity_cr, total_assets_cr, "
        "current_assets_cr, current_liabilities_cr, broadcast_dt "
        "FROM extracted_financials WHERE symbol=?", (symbol,)).fetchall()
    # keep rows reported by as_of; one per period_ending (prefer consolidated)
    by_pe: dict[str, dict] = {}
    for pe, scope, rev, pat, ti, op, pbt, fin, eq, ta, ca, cl, bdt in rows:
        ep = _bdt_epoch(bdt)
        if ep is None or ep > as_of_ep or not pe:
            continue
        cur = by_pe.get(pe)
        if cur is None or (scope == "consolidated" and cur["scope"] != "consolidated"):
            by_pe[pe] = {"scope": scope, "rev": rev, "pat": pat, "ti": ti, "op": op,
                         "pbt": pbt, "fin": fin, "eq": eq, "ta": ta, "ca": ca, "cl": cl}
    if len(by_pe) < 2:
        return None
    pes = sorted(by_pe)                       # oldest→newest period_ending
    latest, qoq = by_pe[pes[-1]], by_pe[pes[-2]]
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
    f = {
        "rev_yoy": _growth(latest["rev"], yoy["rev"]) if yoy else None,
        "pat_yoy": _growth(latest["pat"], yoy["pat"]) if yoy else None,
        "op_yoy": _growth(latest["op"], yoy["op"]) if yoy else None,
        "rev_qoq": _growth(latest["rev"], qoq["rev"]),
        "net_margin": (latest["pat"] / latest["ti"] * 100.0)
                      if (latest["pat"] is not None and latest["ti"] and latest["ti"] > 0) else None,
        "op_margin": (latest["op"] / latest["rev"] * 100.0)
                     if (latest["rev"] and latest["rev"] > 0 and latest["op"] is not None) else None,
    }
    # --- balance-sheet ratios (point-in-time): TTM flows / latest-reported BS ---
    def ttm(key):
        vals = [by_pe[p][key] for p in pes[-4:] if by_pe[p][key] is not None]
        if len(vals) == 4:
            return sum(vals)
        return sum(vals) / len(vals) * 4.0 if vals else None      # annualise if <4q

    bs = next((by_pe[p] for p in reversed(pes) if by_pe[p]["eq"] is not None), None)
    if bs:
        t_pat, t_rev, t_pbt, t_fin = ttm("pat"), ttm("rev"), ttm("pbt"), ttm("fin")
        eq, ta, ca, cl = bs["eq"], bs["ta"], bs["ca"], bs["cl"]
        if t_pat is not None and eq and eq > 0:
            f["roe"] = t_pat / eq * 100.0
        if t_pbt is not None and ta and cl is not None and (ta - cl) > 0:
            f["roce"] = (t_pbt + (t_fin or 0.0)) / (ta - cl) * 100.0      # EBIT/capital-employed
        if ca is not None and cl and cl > 0:
            f["current_ratio"] = ca / cl
        if t_rev is not None and ta and ta > 0:
            f["asset_turnover"] = t_rev / ta
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
