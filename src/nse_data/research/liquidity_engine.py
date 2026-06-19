"""P6 — Liquidity engine. Cross-sectional, point-in-time. Avoid illiquid traps.

Per stock, from daily candles REPORTED on/before the date (point-in-time, trailing
~60 sessions), compute liquidity factors; percentile-rank each WITHIN sector and
average → Liquidity score [0,100]. Higher = more liquid / easier to trade out of.

Factors (all higher-is-better after transform, candle-derived so always PIT):
  * turnover   — log10 of MEDIAN daily traded value (₹cr = close×volume/1e7) over the
                 trailing window. The core depth measure; log so a few ₹000-cr names
                 don't crush the percentile pool.
  * neg_amihud — −Amihud illiquidity = −mean(|daily return| / daily turnover_cr).
                 Price-impact per ₹cr traded; lower impact = more liquid → negated.
  * turn_trend — recent-20d median turnover / prior-40d median − 1. RISING liquidity
                 (institutional footprints building) scores higher than a dried-up book.

NOT a pure return-predictor — liquidity is mainly a RISK/eligibility gate (the arch
doc: "penalise low-liquidity stocks"), reported alongside the composite and usable as
a veto on thin names. Validated by scripts/backtest_engine.py --engine liquidity (does
the bottom bucket underperform / carry worse drawdown?) before it earns any weight.

`tradeable_universe.med_turnover_cr` exists but is a single CURRENT snapshot (not
PIT), so it is unusable for a backtest — we derive everything from candles instead.
Delivery % (arch doc) needs a delivery feed candles don't carry; added when that lands.
"""
from __future__ import annotations

WINDOW = 60                       # trailing sessions for the liquidity window
FACTORS = ("turnover", "neg_amihud", "turn_trend")


def _rows(conn, symbol, as_of_ep, n=WINDOW):
    """Trailing (close, volume) on/before as_of, oldest→newest. PIT via ts<=as_of."""
    rows = conn.execute(
        "SELECT close, volume FROM raw_intraday_candles WHERE symbol=? AND interval='day' "
        "AND close IS NOT NULL AND volume IS NOT NULL AND ts <= ? ORDER BY ts DESC LIMIT ?",
        (symbol, as_of_ep, n)).fetchall()
    return rows[::-1]


def _median(xs):
    s = sorted(xs)
    n = len(s)
    if not n:
        return None
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _log10(x):
    # tiny dependency-free log10; x>0 guaranteed by callers
    import math
    return math.log10(x)


def liquidity_raw(conn, symbol, as_of_ep):
    """Raw liquidity factors from the trailing candle window (PIT). None if too thin
    a price history to measure."""
    rows = _rows(conn, symbol, as_of_ep)
    if len(rows) < 20:                                  # need a real window
        return None
    turn = [c * v / 1e7 for c, v in rows if c and v and v > 0]   # daily ₹cr traded
    if len(turn) < 15:
        return None
    f = {}
    med = _median(turn)
    if med and med > 0:
        f["turnover"] = _log10(med)
    # Amihud: mean over days of |ret| / turnover_cr (price impact per ₹cr).
    imp = []
    for i in range(1, len(rows)):
        c0, c1 = rows[i - 1][0], rows[i][0]
        t = rows[i][0] * rows[i][1] / 1e7
        if c0 and c0 > 0 and c1 and t and t > 0:
            imp.append(abs(c1 / c0 - 1.0) / t)
    if imp:
        f["neg_amihud"] = -(sum(imp) / len(imp))
    # turnover trend: recent 20d vs prior 40d median turnover.
    if len(turn) >= 40:
        recent, prior = _median(turn[-20:]), _median(turn[-40:-20])
        if recent is not None and prior and prior > 0:
            f["turn_trend"] = recent / prior - 1.0
    return f or None


def _pctile(value, pool):
    if not pool:
        return 50.0
    below = sum(1 for x in pool if x < value)
    eq = sum(1 for x in pool if x == value)
    return 100.0 * (below + 0.5 * eq) / len(pool)


def score_universe(conn, symbols, as_of_ep, sector_of):
    """{symbol: {'score','components'}} — cross-sectional percentile WITHIN sector,
    averaged across factors. Missing factor → neutral (skipped in the mean)."""
    raw = {s: liquidity_raw(conn, s, as_of_ep) for s in symbols}
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
