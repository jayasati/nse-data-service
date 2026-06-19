"""Engine 10 — Fundamental Trend (Improvement Score). Cross-sectional, point-in-time.
Scores the DIRECTION of the fundamentals, not their level (Quality does level): a
business going 8%→12%→16% ROE with falling debt and accelerating revenue is improving
even if its absolute numbers aren't yet elite. The early-warning of a turnaround
before momentum confirms it.

Per stock (point-in-time, consolidated-preferred), compute the trailing-year CHANGE in
each metric, percentile-rank each WITHIN sector, average → Improvement [0,100]:
  rev_accel    — Δ in YoY revenue growth (is growth accelerating?)
  net_margin_chg / op_margin_chg — margin expansion
  roe_chg      — Δ TTM ROE              (needs balance-sheet equity)
  neg_de_chg   — −Δ debt/equity          (debt REDUCTION is positive; needs BS)
  cfo_chg      — Δ TTM operating cash flow
Missing factors (e.g. no balance sheet) are skipped, not penalised. Broader than the
Turnaround engine (margins only); validated by scripts/backtest_engine.py --engine improvement.
"""
from __future__ import annotations

import datetime as _dt

_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
FACTORS = ("rev_accel", "net_margin_chg", "op_margin_chg", "roe_chg", "neg_de_chg", "cfo_chg")


def _bdt_epoch(s):
    if not s:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return int(_dt.datetime.strptime(s.strip(), fmt).replace(tzinfo=_IST).timestamp())
        except ValueError:
            continue
    return None


def _pit(conn, symbol, as_of_ep):
    """oldest→newest list of period dicts reported on/before as_of (consol-preferred)."""
    by_pe = {}
    for pe, scope, rev, pat, ti, op, eq, borr, cfo, bdt in conn.execute(
            "SELECT period_ending, scope, revenue_cr, pat_cr, total_income_cr, operating_profit_cr, "
            "equity_cr, borrowings_cr, cfo_cr, broadcast_dt FROM extracted_financials WHERE symbol=?",
            (symbol,)):
        ep = _bdt_epoch(bdt)
        if ep is None or ep > as_of_ep or not pe:
            continue
        cur = by_pe.get(pe)
        if cur is None or (scope == "consolidated" and cur["scope"] != "consolidated"):
            by_pe[pe] = {"scope": scope, "rev": rev, "pat": pat, "ti": ti, "op": op,
                         "eq": eq, "borr": borr, "cfo": cfo}
    return [dict(pe=pe, **by_pe[pe]) for pe in sorted(by_pe)]


def _yoy(now, prior):
    return (now - prior) / abs(prior) * 100 if (now is not None and prior not in (None, 0)) else None


def _idx_about(pes, target_pe, back_days):
    try:
        tgt = _dt.date.fromisoformat(target_pe) - _dt.timedelta(days=back_days)
    except ValueError:
        return None
    cand = min(pes, key=lambda p: abs((_dt.date.fromisoformat(p) - tgt).days), default=None)
    if cand and abs((_dt.date.fromisoformat(cand) - tgt).days) <= 45:
        return cand
    return None


def improvement_raw(conn, symbol, as_of_ep):
    qs = _pit(conn, symbol, as_of_ep)
    if len(qs) < 5:
        return None
    by = {q["pe"]: q for q in qs}
    pes = [q["pe"] for q in qs]
    latest = pes[-1]
    prior_y = _idx_about(pes, latest, 365)              # ~1 year ago
    if not prior_y:
        return None
    L, P = by[latest], by[prior_y]

    def margin(q, num, den):
        v, d = q.get(num), q.get(den)
        return (v / d * 100.0) if (v is not None and d and d > 0) else None

    f = {}
    # revenue-growth ACCELERATION: latest YoY rev growth minus the year-ago quarter's YoY
    yoy_now = _yoy(L["rev"], P["rev"])
    py2 = _idx_about(pes, prior_y, 365)
    yoy_then = _yoy(P["rev"], by[py2]["rev"]) if py2 else None
    if yoy_now is not None and yoy_then is not None:
        f["rev_accel"] = yoy_now - yoy_then
    # margin expansion (latest vs ~1y ago)
    for key, num, den in (("net_margin_chg", "pat", "ti"), ("op_margin_chg", "op", "rev")):
        mn, mp = margin(L, num, den), margin(P, num, den)
        if mn is not None and mp is not None:
            f[key] = mn - mp

    # --- balance-sheet trajectories (TTM flows / period-end BS) ---
    def ttm_to(pe_end):
        i = pes.index(pe_end)
        vals = [by[pes[j]]["pat"] for j in range(max(0, i - 3), i + 1) if by[pes[j]]["pat"] is not None]
        return sum(vals) / len(vals) * 4.0 if vals else None

    def roe(pe_end):
        eq = by[pe_end]["eq"]
        t = ttm_to(pe_end)
        return (t / eq * 100.0) if (t is not None and eq and eq > 0) else None

    rn, rp = roe(latest), roe(prior_y)
    if rn is not None and rp is not None:
        f["roe_chg"] = rn - rp

    def de(q):
        return (q["borr"] / q["eq"]) if (q.get("borr") is not None and q.get("eq") and q["eq"] > 0) else None

    dn, dp = de(L), de(P)
    if dn is not None and dp is not None:
        f["neg_de_chg"] = -(dn - dp)                    # debt REDUCTION is positive

    if L.get("cfo") is not None and P.get("cfo") is not None:
        f["cfo_chg"] = L["cfo"] - P["cfo"]
    return f or None


def _pctile(value, pool):
    if not pool:
        return 50.0
    below = sum(1 for x in pool if x < value)
    eq = sum(1 for x in pool if x == value)
    return 100.0 * (below + 0.5 * eq) / len(pool)


def score_universe(conn, symbols, as_of_ep, sector_of):
    raw = {s: improvement_raw(conn, s, as_of_ep) for s in symbols}
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
