"""P-Turnaround engine. Cross-sectional, point-in-time. Catches IMPROVING
businesses (trajectory), distinct from Quality (which is the level). Factors:
operating- and net-margin EXPANSION over the last ~4 quarters (latest vs a year
ago), percentile-ranked within sector. Higher = margins improving = turnaround.
Validated before any composite weight; key question is whether it adds signal
beyond Quality+Value.
"""
from __future__ import annotations

import datetime as _dt

_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
FACTORS = ("op_margin_chg", "net_margin_chg")


def _bdt_epoch(s):
    if not s:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return int(_dt.datetime.strptime(s.strip(), fmt).replace(tzinfo=_IST).timestamp())
        except ValueError:
            continue
    return None


def turnaround_raw(conn, symbol, as_of_ep):
    by_pe = {}
    for pe, scope, rev, pat, ti, op, bdt in conn.execute(
            "SELECT period_ending, scope, revenue_cr, pat_cr, total_income_cr, "
            "operating_profit_cr, broadcast_dt FROM extracted_financials WHERE symbol=?",
            (symbol,)):
        ep = _bdt_epoch(bdt)
        if ep is None or ep > as_of_ep or not pe:
            continue
        cur = by_pe.get(pe)
        if cur is None or (scope == "consolidated" and cur[0] != "consolidated"):
            by_pe[pe] = (scope, rev, pat, ti, op)
    pes = sorted(by_pe)
    if len(pes) < 5:                          # need ~a year of prior to measure change
        return None

    def opm(row):
        _, rev, pat, ti, op = row
        return (op / rev * 100.0) if (rev and rev > 0 and op is not None) else None

    def npm(row):
        _, rev, pat, ti, op = row
        return (pat / ti * 100.0) if (ti and ti > 0 and pat is not None) else None

    latest, year_ago = by_pe[pes[-1]], by_pe[pes[-5]]
    f = {}
    o1, o0 = opm(latest), opm(year_ago)
    n1, n0 = npm(latest), npm(year_ago)
    if o1 is not None and o0 is not None:
        f["op_margin_chg"] = o1 - o0
    if n1 is not None and n0 is not None:
        f["net_margin_chg"] = n1 - n0
    return f or None


def _pctile(value, pool):
    if not pool:
        return 50.0
    below = sum(1 for x in pool if x < value)
    eq = sum(1 for x in pool if x == value)
    return 100.0 * (below + 0.5 * eq) / len(pool)


def score_universe(conn, symbols, as_of_ep, sector_of):
    raw = {s: turnaround_raw(conn, s, as_of_ep) for s in symbols}
    raw = {s: f for s, f in raw.items() if f}
    pools = {}
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
                pool = [x for (sk, fk), lst in pools.items() if fk == fac for x in lst] or pool
            comps[fac] = round(_pctile(v, pool), 1)
        if comps:
            out[s] = {"score": round(sum(comps.values()) / len(comps), 1), "components": comps}
    return out
