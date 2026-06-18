"""P3 — Valuation engine. Cross-sectional, point-in-time. Cheap vs sector peers = +.

Per stock (point-in-time via `broadcast_dt`, consolidated-preferred): trailing-12m
EPS = sum of the last 4 reported quarters' eps_basic; price = close on the date.
Factors: earnings yield `ey = TTM_EPS/price` (higher = cheaper) and PEG-style value
`peg = PE / PAT-YoY-growth` (lower = cheaper-for-the-growth). Percentile-ranked
WITHIN sector → Valuation [0,100]. Validated before it earns composite weight.

(EV/EBITDA + P/B need historical net-debt + book equity per quarter we don't yet
store — added when that lands.)
"""
from __future__ import annotations

import datetime as _dt

_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
FACTORS = ("ey", "neg_peg")   # both higher-is-better after transform


def _bdt_epoch(s):
    if not s:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return int(_dt.datetime.strptime(s.strip(), fmt).replace(tzinfo=_IST).timestamp())
        except ValueError:
            continue
    return None


def _reported_quarters(conn, symbol, as_of_ep):
    """Point-in-time quarters (one per period_ending, consolidated-preferred),
    oldest→newest: list of (period_ending, eps_basic, pat_cr)."""
    by_pe = {}
    for pe, scope, eps, pat, bdt in conn.execute(
            "SELECT period_ending, scope, eps_basic, pat_cr, broadcast_dt "
            "FROM extracted_financials WHERE symbol=?", (symbol,)):
        ep = _bdt_epoch(bdt)
        if ep is None or ep > as_of_ep or not pe:
            continue
        cur = by_pe.get(pe)
        if cur is None or (scope == "consolidated" and cur[0] != "consolidated"):
            by_pe[pe] = (scope, eps, pat)
    return [(pe, by_pe[pe][1], by_pe[pe][2]) for pe in sorted(by_pe)]


def _close_asof(conn, symbol, as_of_ep):
    r = conn.execute(
        "SELECT close FROM raw_intraday_candles WHERE symbol=? AND interval='day' "
        "AND close IS NOT NULL AND ts <= ? ORDER BY ts DESC LIMIT 1",
        (symbol, as_of_ep)).fetchone()
    return r[0] if r else None


def valuation_raw(conn, symbol, as_of_ep):
    qs = _reported_quarters(conn, symbol, as_of_ep)
    if len(qs) < 4:
        return None
    price = _close_asof(conn, symbol, as_of_ep)
    if not price or price <= 0:
        return None
    ttm_eps = sum(q[1] for q in qs[-4:] if q[1] is not None)
    if not ttm_eps or ttm_eps <= 0:          # loss-making / no EPS → no value score
        return None
    ey = ttm_eps / price * 100.0             # earnings yield %
    pe = price / ttm_eps
    # PAT YoY growth for PEG (latest vs ~4 quarters back)
    peg = None
    if len(qs) >= 5 and qs[-5][2] and qs[-5][2] > 0:
        g = (qs[-1][2] - qs[-5][2]) / qs[-5][2] * 100.0
        if g > 0:
            peg = pe / g
    return {"ey": ey, "neg_peg": (-peg if peg is not None else None)}


def _pctile(value, pool):
    if not pool:
        return 50.0
    below = sum(1 for x in pool if x < value)
    eq = sum(1 for x in pool if x == value)
    return 100.0 * (below + 0.5 * eq) / len(pool)


def score_universe(conn, symbols, as_of_ep, sector_of):
    raw = {s: valuation_raw(conn, s, as_of_ep) for s in symbols}
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
