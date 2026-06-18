"""P4 — Ownership engine (grand-prompt ranking system). Cross-sectional,
point-in-time. Scores institutional/promoter ACCUMULATION: the QoQ change in
promoter / FII / DII / MF holding from the latest shareholding pattern DISCLOSED
as-of the date, percentile-ranked within sector → Ownership score [0,100].
Higher = stronger accumulation by smart money vs peers.

Point-in-time is critical and subtle: a quarter's pattern is FILED ~1–3 weeks
after quarter-end (SHP is due within 21 days), so we must gate on the DISCLOSURE
date, not the quarter-end — using quarter-end would leak ~3 weeks of future info
into the backtest. NSE doesn't give us the disclosure date in the row, but it's
embedded in the XBRL filename we store (`SHP_<id>_DDMMYYYYHHMMSS_WEB.xml`), so we
parse it from there (verified across the universe: lags 1–20d). Fallback:
quarter-end + 25d.

Factors (all higher-is-better = accumulation): Δpromoter, Δfii, Δdii, Δmf, the
latest disclosed quarter minus the prior. Feeds raw_shareholding_quarterly (built
by scripts/backfill_shareholding_history.py). Validated by
scripts/backtest_ownership.py before it earns any composite weight.
"""
from __future__ import annotations

import datetime as _dt
import re

_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
_TS = re.compile(r"_(\d{14})_")
# higher-is-better factors averaged into the engine score
FACTORS = ("d_promoter", "d_fii", "d_dii", "d_mf")


def _disclosed_ep(xbrl_url: str | None, qe_date: str | None) -> int | None:
    """Disclosure epoch: the XBRL filename timestamp, else quarter-end + 25d."""
    if xbrl_url:
        m = _TS.search(xbrl_url)
        if m:
            try:
                return int(_dt.datetime.strptime(m.group(1), "%d%m%Y%H%M%S")
                           .replace(tzinfo=_IST).timestamp())
            except ValueError:
                pass
    if not qe_date:
        return None
    try:
        d = _dt.datetime.strptime(qe_date, "%d-%b-%Y").replace(tzinfo=_IST)
        return int((d + _dt.timedelta(days=25)).timestamp())
    except ValueError:
        return None


def _qe_key(qe: str | None) -> _dt.datetime:
    if not qe:
        return _dt.datetime.min
    try:
        return _dt.datetime.strptime(qe, "%d-%b-%Y")
    except ValueError:
        return _dt.datetime.min


def ownership_raw(conn, symbol: str, as_of_ep: int) -> dict | None:
    """Raw ownership-change factors from the two most recent quarters DISCLOSED
    on/before as_of_ep (point-in-time). None if < 2 disclosed quarters."""
    rows = conn.execute(
        "SELECT qe_date, promoter_pct, fii_pct, dii_pct, mf_pct, xbrl_url "
        "FROM raw_shareholding_quarterly WHERE symbol=?", (symbol,)).fetchall()
    disclosed = []
    for qe, prom, fii, dii, mf, url in rows:
        ep = _disclosed_ep(url, qe)
        if ep is None or ep > as_of_ep:
            continue
        disclosed.append((_qe_key(qe), prom, fii, dii, mf))
    if len(disclosed) < 2:
        return None
    disclosed.sort(key=lambda r: r[0])          # oldest → newest quarter-end
    latest, prior = disclosed[-1], disclosed[-2]

    def delta(i):
        a, b = latest[i], prior[i]
        return (a - b) if (a is not None and b is not None) else None

    f = {"d_promoter": delta(1), "d_fii": delta(2), "d_dii": delta(3), "d_mf": delta(4)}
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
    raw = {s: ownership_raw(conn, s, as_of_ep) for s in symbols}
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
                pool = [x for (_s, fk), lst in pools.items() if fk == fac for x in lst] or pool
            comps[fac] = round(_pctile(v, pool), 1)
        if comps:
            out[s] = {"score": round(sum(comps.values()) / len(comps), 1), "components": comps}
    return out
