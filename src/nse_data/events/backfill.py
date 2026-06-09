"""Bootstrap earnings-reaction odds from history (Phase 5, E5).

The earnings_direction signal is new, so there are no past reactions to learn
odds from. This reconstructs them from daily bhavcopy: for each past quarterly
result, the reaction direction is the result-day close vs the prior close, and
the outcome (T+1/T+3 returns) comes from the existing outcome labeler. It writes
synthetic, already-``dispatched`` signals (never sent, never paper-traded — the
paper tracker only opens trades for *today's* signals) purely so
``earnings_odds`` has a population to aggregate.

Intentionally NOT registered as a job: it's a one-time, DB-writing bootstrap the
user runs deliberately. It reports coverage (results seen / backfilled / skipped)
and never silently caps.

    from nse_data.storage.db import open_db
    print(backfill_earnings_reactions(open_db("data/nse.db")))
"""
from __future__ import annotations

import sqlite3

import structlog

from .calendar import _parse_nse_date

log = structlog.get_logger()

# Minimum |result-day move| to count as a directional reaction (matches the live
# rule's EARNINGS_MOVE_MIN).
_MOVE_MIN_PCT = 1.5


def _reaction_prices(
    conn: sqlite3.Connection, symbol: str, filing_iso: str,
) -> tuple[str, float, float] | None:
    """(reaction_date, prior_close, reaction_close) around a filing, or None.

    Reaction day = first EQ session on/after the filing date; prior = the session
    before it. Returns None if either close is unavailable.
    """
    reaction = conn.execute(
        "SELECT date, close FROM raw_bhavcopy_cm "
        "WHERE symbol=? AND series='EQ' AND date >= ? AND close IS NOT NULL "
        "ORDER BY date ASC LIMIT 1",
        (symbol, filing_iso),
    ).fetchone()
    if not reaction:
        return None
    prior = conn.execute(
        "SELECT close FROM raw_bhavcopy_cm "
        "WHERE symbol=? AND series='EQ' AND date < ? AND close IS NOT NULL "
        "ORDER BY date DESC LIMIT 1",
        (symbol, reaction[0]),
    ).fetchone()
    if not prior or not prior[0]:
        return None
    return (reaction[0], float(prior[0]), float(reaction[1]))


def backfill_earnings_reactions(
    conn: sqlite3.Connection, *, now=None, limit: int | None = None,
    move_min_pct: float = _MOVE_MIN_PCT,
) -> dict:
    """Reconstruct historical earnings reactions and label their outcomes.

    Returns a coverage report: results seen, backfilled, and why others were
    skipped (no price data / move below threshold / already present)."""
    from ..scheduler import market_hours
    from ..signals.outcome_labeler import _upsert_outcome, label_signal

    now = now or market_hours.now_ist()
    results = conn.execute(
        "SELECT symbol, filing_date FROM raw_financial_results "
        "WHERE period='Quarterly' AND filing_date IS NOT NULL "
        "ORDER BY filing_date ASC"
    ).fetchall()

    seen = backfilled = skip_no_data = skip_flat = skip_dup = 0
    for symbol, filing_date in results:
        if limit is not None and backfilled >= limit:
            break
        seen += 1
        d = _parse_nse_date(filing_date)
        if d is None:
            skip_no_data += 1
            continue
        prices = _reaction_prices(conn, symbol, d.isoformat())
        if prices is None:
            skip_no_data += 1
            continue
        reaction_date, prior_close, reaction_close = prices
        move_pct = (reaction_close - prior_close) / prior_close * 100.0
        if abs(move_pct) < move_min_pct:
            skip_flat += 1
            continue
        direction = "long" if move_pct > 0 else "short"
        detected_at = f"{reaction_date}T15:30:00"

        # Idempotent: skip if this reaction was already backfilled.
        if conn.execute(
            "SELECT 1 FROM signals WHERE symbol=? AND signal_type='earnings_direction' "
            "AND detected_at=?", (symbol, detected_at),
        ).fetchone():
            skip_dup += 1
            continue

        cur = conn.execute(
            "INSERT INTO signals "
            "(symbol, signal_type, detected_at, price, price_change_pct, "
            " confidence, dispatched, horizon, direction) "
            "VALUES (?, 'earnings_direction', ?, ?, ?, NULL, 1, 'intraday', ?)",
            (symbol, detected_at, reaction_close, round(move_pct, 2), direction),
        )
        sid = int(cur.lastrowid or 0)
        outcome = label_signal(
            conn, sid, symbol, detected_at, reaction_close, None,
            now=now, direction=direction,
        )
        if outcome is not None:
            _upsert_outcome(conn, outcome)
        backfilled += 1

    conn.commit()
    report = {
        "results_seen": seen, "backfilled": backfilled,
        "skipped_no_price_data": skip_no_data, "skipped_flat": skip_flat,
        "skipped_already_present": skip_dup,
    }
    log.info("earnings_backfill", **report)
    return report
