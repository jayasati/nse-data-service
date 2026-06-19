"""P5 — Surprise engine. Cross-sectional, point-in-time, EVENT-DRIVEN. Markets pay
for the gap between what was reported and what was expected — not the level.

Per stock, ONLY when a result was reported in the trailing RECENCY window (a surprise
is an event, not a standing state), compare the latest actual to an expectation and
percentile-rank the surprise WITHIN sector → Surprise score [0,100]. Names with no
recent result are absent (neutral in the composite roll-up).

Expectation, in priority order (both PIT — the estimate must predate the result):
  1. CONSENSUS — a `consensus_estimates` row for (symbol, period_ending) whose `as_of`
     precedes the result broadcast. This is the real bar; the table is sparse today
     (1 row), so it rarely fires yet — populate it and this path lights up for free.
  2. PROXY (seasonal-trend) — expected = year-ago actual grown by the stock's own
     trailing YoY trend (median of up-to-3 prior YoY growth rates; 0 ⇒ pure seasonal).
     A 20%/yr grower beating last year flat is a MISS vs trend, not a beat. This is the
     project's blessed "react vs a proxy expectation" model (earnings-reaction engine).

Factors (higher = bigger positive surprise): surprise_rev, surprise_pat, surprise_eps.
Validated by scripts/backtest_engine.py --engine surprise before it earns any weight.
"""
from __future__ import annotations

import datetime as _dt

_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
RECENCY_DAYS = 75                 # only score a result within this many days of as_of
FACTORS = ("surprise_rev", "surprise_pat", "surprise_eps")


def _bdt_epoch(s):
    if not s:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return int(_dt.datetime.strptime(s.strip(), fmt).replace(tzinfo=_IST).timestamp())
        except ValueError:
            continue
    return None


def _quarters(conn, symbol, as_of_ep):
    """PIT quarters (one per period_ending, consolidated-preferred), oldest→newest:
    {period_ending: {'rev','pat','eps','bep'(broadcast epoch),'scope'}}."""
    by_pe = {}
    for pe, scope, rev, pat, eps, bdt in conn.execute(
            "SELECT period_ending, scope, revenue_cr, pat_cr, eps_basic, broadcast_dt "
            "FROM extracted_financials WHERE symbol=?", (symbol,)):
        ep = _bdt_epoch(bdt)
        if ep is None or ep > as_of_ep or not pe:
            continue
        cur = by_pe.get(pe)
        if cur is None or (scope == "consolidated" and cur["scope"] != "consolidated"):
            by_pe[pe] = {"rev": rev, "pat": pat, "eps": eps, "bep": ep, "scope": scope}
    return by_pe


def _yoy_pe(pes, target_pe):
    """period_ending ~365d before target_pe (±45d), else None."""
    try:
        td = _dt.date.fromisoformat(target_pe) - _dt.timedelta(days=365)
    except ValueError:
        return None
    cand = min(pes, key=lambda p: abs((_dt.date.fromisoformat(p) - td).days), default=None)
    if cand and abs((_dt.date.fromisoformat(cand) - td).days) <= 45:
        return cand
    return None


def _surprise(actual, expected):
    """signed % surprise vs expectation; needs a meaningful (positive) base."""
    if actual is None or expected is None or expected <= 0:
        return None
    return (actual - expected) / expected * 100.0


def _consensus(conn, symbol, pe, result_bep):
    """(rev_est, pat_est, eps_est) if a consensus row predates the result, else None."""
    r = conn.execute(
        "SELECT rev_est_cr, pat_est_cr, eps_est, as_of FROM consensus_estimates "
        "WHERE symbol=? AND period_ending=?", (symbol, pe)).fetchone()
    if not r:
        return None
    est_ep = _bdt_epoch(r[3])
    if est_ep is None or est_ep >= result_bep:        # estimate must precede the print
        return None
    return r[0], r[1], r[2]


def surprise_raw(conn, symbol, as_of_ep):
    by_pe = _quarters(conn, symbol, as_of_ep)
    if len(by_pe) < 2:
        return None
    pes = sorted(by_pe)
    latest = pes[-1]
    cur = by_pe[latest]
    # event gate: the latest result must be RECENT (a fresh print), else no surprise.
    if (as_of_ep - cur["bep"]) > RECENCY_DAYS * 86400:
        return None
    yoy = _yoy_pe(pes, latest)
    # --- expectation: consensus first, else seasonal-trend proxy ---
    cons = _consensus(conn, symbol, latest, cur["bep"])
    if cons:
        exp_rev, exp_pat, exp_eps = cons
    else:
        if not yoy:
            return None
        ya = by_pe[yoy]
        # trailing YoY growth (median of up-to-3 prior transitions, excl. the current)
        gs = []
        for i in range(1, len(pes) - 1):
            p, yp = pes[i], _yoy_pe(pes[:i + 1], pes[i])
            if yp and by_pe[yp]["pat"] and by_pe[yp]["pat"] > 0 and by_pe[p]["pat"] is not None:
                gs.append((by_pe[p]["pat"] - by_pe[yp]["pat"]) / by_pe[yp]["pat"])
        g = sorted(gs)[len(gs) // 2] if gs else 0.0
        g = max(-0.5, min(1.0, g))                    # clamp a wild trend
        exp_rev = ya["rev"] * (1 + g) if ya["rev"] is not None else None
        exp_pat = ya["pat"] * (1 + g) if ya["pat"] is not None else None
        exp_eps = ya["eps"] * (1 + g) if ya["eps"] is not None else None
    f = {
        "surprise_rev": _surprise(cur["rev"], exp_rev),
        "surprise_pat": _surprise(cur["pat"], exp_pat),
        "surprise_eps": _surprise(cur["eps"], exp_eps),
    }
    return f if any(v is not None for v in f.values()) else None


def _pctile(value, pool):
    if not pool:
        return 50.0
    below = sum(1 for x in pool if x < value)
    eq = sum(1 for x in pool if x == value)
    return 100.0 * (below + 0.5 * eq) / len(pool)


def score_universe(conn, symbols, as_of_ep, sector_of):
    """{symbol: {'score','components'}} — cross-sectional percentile WITHIN sector,
    averaged across factors. Sparse: only names with a recent result appear."""
    raw = {s: surprise_raw(conn, s, as_of_ep) for s in symbols}
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
            if len(pool) < 6:                  # too few sector peers → all-market pool
                pool = [x for (sk, fk), lst in pools.items() if fk == fac for x in lst] or pool
            comps[fac] = round(_pctile(v, pool), 1)
        if comps:
            out[s] = {"score": round(sum(comps.values()) / len(comps), 1), "components": comps}
    return out
