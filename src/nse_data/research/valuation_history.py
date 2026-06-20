"""Valuation vs the stock's OWN history (PROFITABILITY_PLAN Track C, R6).

"Cheap" should mean cheap versus the stock itself, not only versus its sector. There is no
stored multi-year PE series, so we reconstruct one from data that IS deep:

    V_t = close_t / TTM-PAT(as of t)          (a PE proxy)

Under ~constant share count, V_t is proportional to PE_t, so the PERCENTILE of today's V
within the trailing window equals the PE percentile — which lets us skip share-count and the
noisy eps_basic semantics and use the cleanly-populated quarterly `pat_cr` (summed to TTM).

Point-in-time: a quarter's TTM only becomes "known" ~45 days after period end (filing lag),
so the series never uses earnings the market hadn't seen. TTM-PAT ≤ 0 (loss-making) periods
are skipped (a negative PE is meaningless). PB history is intentionally NOT reconstructed —
no deep book-value series exists. On-demand per symbol (the pre-buy card is the consumer).

SHORT-HISTORY CAVEAT (honest): the signal is only as good as the candle depth. Over a short
window during an earnings up-trend the denominator (TTM-PAT) rises steadily, so the PE-proxy
drifts monotonically down and *today is almost always the lowest* — i.e. nearly everything
looks "cheap". So `cheap`/`expensive` are asserted ONLY when the window spans ≥ `_SPAN_MIN`
years; on thinner history the percentile is still reported (with its span) but the booleans
stay False. Becomes genuinely discriminating once ~5y of candles (a full PE cycle) exist.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

_FILING_LAG_DAYS = 45        # a result is public ~45d after period end → no look-ahead
_MIN_QUARTERS = 4            # need 4 quarters for the first TTM
_SPAN_MIN = 3.0             # min window (years) before asserting cheap/expensive (5y ideal)


def _eff_date(period_ending: str) -> str | None:
    try:
        return (date.fromisoformat(period_ending[:10]) + timedelta(days=_FILING_LAG_DAYS)).isoformat()
    except (ValueError, TypeError):
        return None


def _pat_rows(conn: sqlite3.Connection, symbol: str) -> list[tuple[str, float]]:
    """(period_ending, pat_cr) for the symbol, one consistent scope (the one with the most
    quarters), deduped per period_ending, sorted ascending."""
    rows = conn.execute(
        "SELECT scope, period_ending, pat_cr FROM extracted_financials "
        "WHERE symbol=? AND pat_cr IS NOT NULL AND period_ending IS NOT NULL",
        (symbol,)).fetchall()
    by_scope: dict[str, dict[str, float]] = {}
    for scope, pe, pat in rows:
        by_scope.setdefault(scope, {})[pe] = pat        # dedupe per period_ending (last wins)
    if not by_scope:
        return []
    best = max(by_scope, key=lambda s: len(by_scope[s]))
    return sorted(by_scope[best].items())


def _ttm_steps(pat_rows: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Rolling 4-quarter TTM-PAT, stamped with its effective (public) date."""
    steps = []
    for i in range(_MIN_QUARTERS - 1, len(pat_rows)):
        ttm = sum(pat_rows[j][1] for j in range(i - _MIN_QUARTERS + 1, i + 1))
        eff = _eff_date(pat_rows[i][0])
        if eff:
            steps.append((eff, ttm))
    steps.sort()
    return steps


def valuation_percentile(conn: sqlite3.Connection, symbol: str, *,
                         years: int = 5, min_points: int = 60) -> dict | None:
    """Percentile of today's PE-proxy within the stock's own trailing `years`.

    Low percentile = cheap vs its own history. None when there isn't enough data
    (< 4 quarters of PAT, no candles, or < `min_points` valid daily observations).
    """
    pat_rows = _pat_rows(conn, symbol)
    if len(pat_rows) < _MIN_QUARTERS:
        return None
    steps = _ttm_steps(pat_rows)
    if not steps:
        return None

    candles = conn.execute(
        "SELECT date(ts,'unixepoch','+05:30') d, close FROM raw_intraday_candles "
        "WHERE symbol=? AND interval='day' AND close IS NOT NULL ORDER BY ts", (symbol,)).fetchall()
    if not candles:
        return None
    cutoff = (date.fromisoformat(candles[-1][0]) - timedelta(days=365 * years)).isoformat()

    si = 0
    cur_ttm: float | None = None
    while si < len(steps) and steps[si][0] <= cutoff:    # seed with the TTM known at window start
        cur_ttm = steps[si][1]
        si += 1

    vals: list[float] = []
    first_d = last_d = None
    for d, close in candles:
        if d < cutoff:
            continue
        while si < len(steps) and steps[si][0] <= d:
            cur_ttm = steps[si][1]
            si += 1
        if cur_ttm and cur_ttm > 0 and close:
            vals.append(close / cur_ttm)
            first_d = first_d or d
            last_d = d
    if len(vals) < min_points:
        return None

    span_years = round((date.fromisoformat(last_d) - date.fromisoformat(first_d)).days / 365, 1)
    enough = span_years >= _SPAN_MIN                     # short history → don't assert (see caveat)
    now_v = vals[-1]
    below = sum(1 for v in vals if v < now_v)
    pctile = round(100 * below / len(vals))
    return {"pctile": pctile, "n_points": len(vals), "span_years": span_years,
            "cheap": pctile <= 25 and enough, "expensive": pctile >= 75 and enough}
