"""P2 — Momentum engine. Cross-sectional relative strength, point-in-time.

Canonical momentum (NOT the v1 short-term version that mean-reverted): medium/long
lookback returns, percentile-ranked WITHIN sector. The 12-1m factor (12-month return
ending ~1 month ago) deliberately SKIPS the last month to avoid short-term reversal.
Higher trailing strength = higher score. Validated at 30/60/90d before any weight.
"""
from __future__ import annotations

FACTORS = ("ret_3m", "ret_6m", "ret_12_1m")


def _closes(conn, symbol, as_of_ep, n=260):
    rows = conn.execute(
        "SELECT close FROM raw_intraday_candles WHERE symbol=? AND interval='day' "
        "AND close IS NOT NULL AND ts <= ? ORDER BY ts DESC LIMIT ?",
        (symbol, as_of_ep, n)).fetchall()
    return [r[0] for r in rows][::-1]   # oldest→newest


def momentum_raw(conn, symbol, as_of_ep):
    c = _closes(conn, symbol, as_of_ep)
    if len(c) < 70 or not c[-1]:
        return None
    f = {}
    if len(c) >= 64 and c[-64]:
        f["ret_3m"] = c[-1] / c[-64] - 1.0
    if len(c) >= 127 and c[-127]:
        f["ret_6m"] = c[-1] / c[-127] - 1.0
    if len(c) >= 253 and c[-253] and c[-22]:        # 12-month return ending ~1 month ago
        f["ret_12_1m"] = c[-22] / c[-253] - 1.0
    return f or None


def _pctile(value, pool):
    if not pool:
        return 50.0
    below = sum(1 for x in pool if x < value)
    eq = sum(1 for x in pool if x == value)
    return 100.0 * (below + 0.5 * eq) / len(pool)


def score_universe(conn, symbols, as_of_ep, sector_of):
    raw = {s: momentum_raw(conn, s, as_of_ep) for s in symbols}
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
