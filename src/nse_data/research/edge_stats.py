"""Shared rigor primitives for net-of-cost edge backtests (P1 + cause-drift).

The same statistics layer behind every edge verdict: benchmark-ETF excess returns
(removes market/sector beta), round-trip cost, percentile-bootstrap CI, and the
min-n / CI gate that turns a mean into a TRADE / NO-EDGE / AVOID / INSUFFICIENT
verdict. Kept framework-free so both scripts/backtest_result_effect.py and
scripts/backtest_move_causes.py share one source of truth.
"""
from __future__ import annotations

import bisect
import random

# Sector class -> benchmark ETF proxy (daily returns track the index). Sectors
# without a liquid sector ETF fall back to the market (NIFTYBEES).
MARKET_BENCH = "NIFTYBEES"
SECTOR_BENCH = {
    "bfsi": "BANKBEES", "it": "ITBEES", "pharma": "PHARMABEES",
    "fmcg": "FMCGIETF", "metals": "METALIETF",
}


def load_series(conn, symbol: str):
    """(date->close dict, sorted dates) for a daily-candle symbol."""
    rows = conn.execute(
        "SELECT date(ts,'unixepoch','+05:30') d, close FROM raw_intraday_candles "
        "WHERE symbol=? AND interval='day' AND close IS NOT NULL ORDER BY ts",
        (symbol,),
    ).fetchall()
    series = {d: c for d, c in rows}
    return series, sorted(series)


def close_on_or_before(series, dates, d):
    """Latest close on or before date `d` (nearest-prior fill for holidays)."""
    if d in series:
        return series[d]
    i = bisect.bisect_right(dates, d)
    return series[dates[i - 1]] if i else None


def bench_resolver(conn, avail_cache: dict | None = None):
    """Build a (sector -> (series, dates)) loader that caches ETF series and falls
    back to NIFTYBEES when a sector ETF is missing. Returns (bench_for, series_of)."""
    benches = {MARKET_BENCH, *SECTOR_BENCH.values()}
    series = {b: load_series(conn, b) for b in benches}
    avail = {b: len(series[b][0]) for b in benches}
    if avail_cache is not None:
        avail_cache.update(avail)

    def bench_for(sector: str) -> str:
        b = SECTOR_BENCH.get(sector, MARKET_BENCH)
        return b if avail.get(b) else MARKET_BENCH

    return bench_for, series, avail


def bootstrap_ci(samples, iters: int = 10000, seed: int = 12345):
    """(lo, hi) percentile-bootstrap 95% CI on the mean. None if <2 samples."""
    n = len(samples)
    if n < 2:
        return None
    rnd = random.Random(seed)
    means = []
    for _ in range(iters):
        s = 0.0
        for _ in range(n):
            s += samples[rnd.randrange(n)]
        means.append(s / n)
    means.sort()
    return means[int(0.025 * iters)], means[min(int(0.975 * iters), iters - 1)]


def verdict(n: int, ci, min_n: int) -> str:
    """TRADE (CI excludes 0, positive) / AVOID (CI excludes 0, negative) /
    NO-EDGE / INSUFFICIENT (n below the gate)."""
    if n < min_n:
        return "INSUFFICIENT"
    if ci is None:
        return "NO-EDGE"
    lo, hi = ci
    if lo > 0:
        return "TRADE"
    if hi < 0:
        return "AVOID(backwards)"
    return "NO-EDGE"
