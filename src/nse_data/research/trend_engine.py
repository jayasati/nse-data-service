"""Engine 5 — Trend Score. Cross-sectional, point-in-time. "Does the market agree
with the thesis?" — richer than the pure relative-strength Momentum engine: it adds
trend STRUCTURE (above 20/50/200-DMA), distance from the 52-week high, and a volume
trend, all from candles (deep history, fully backtestable).

Factors (percentile-ranked WITHIN sector; higher = stronger trend):
  rs_20 / rs_50 / rs_100 — stock return − NIFTYBEES return over 20/50/100 sessions
  above_dma              — % of {20,50,200}-DMA the close sits above (trend structure)
  neg_drawdown           — distance from the trailing-252d high (0 at the high, negative
                           below; nearer the high = stronger, not yet a value trap)
  vol_trend              — recent-20d avg volume / prior-60d (participation building)

Built alongside (not replacing) momentum_engine; validated by --engine trend.
"""
from __future__ import annotations

FACTORS = ("rs_20", "rs_50", "rs_100", "above_dma", "neg_drawdown", "vol_trend")


def _bars(conn, symbol, as_of_ep, n=260):
    rows = conn.execute(
        "SELECT close, volume FROM raw_intraday_candles WHERE symbol=? AND interval='day' "
        "AND close IS NOT NULL AND ts<=? ORDER BY ts DESC LIMIT ?", (symbol, as_of_ep, n)).fetchall()
    return rows[::-1]


def _ret(closes, w):
    return (closes[-1] / closes[-1 - w] - 1) * 100 if (len(closes) > w and closes[-1 - w]) else None


def trend_raw(bars, bench_ret: dict):
    closes = [c for c, _ in bars]
    vols = [v for _, v in bars]
    if len(closes) < 60:
        return None
    f = {}
    for w in (20, 50, 100):
        r = _ret(closes, w)
        if r is not None and bench_ret.get(w) is not None:
            f[f"rs_{w}"] = r - bench_ret[w]
    c = closes[-1]
    dmas = [k for k in (20, 50, 200) if len(closes) >= k]
    if dmas:
        f["above_dma"] = sum(1 for k in dmas if c > sum(closes[-k:]) / k) / len(dmas) * 100.0
    hi = max(closes[-252:])
    if hi:
        f["neg_drawdown"] = (c / hi - 1) * 100.0
    rv = [v for v in vols if v]
    if len(rv) >= 80:
        prior = sum(rv[-80:-20]) / 60
        if prior:
            f["vol_trend"] = sum(rv[-20:]) / 20 / prior - 1
    return f or None


def _pctile(value, pool):
    if not pool:
        return 50.0
    below = sum(1 for x in pool if x < value)
    eq = sum(1 for x in pool if x == value)
    return 100.0 * (below + 0.5 * eq) / len(pool)


def score_universe(conn, symbols, as_of_ep, sector_of):
    bcl = [c for c, _ in _bars(conn, "NIFTYBEES", as_of_ep)]
    bench_ret = {w: _ret(bcl, w) for w in (20, 50, 100)}
    raw = {s: trend_raw(_bars(conn, s, as_of_ep), bench_ret) for s in symbols}
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
