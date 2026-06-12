"""Historical validation for Week 18.2/18.3 + Week 19 threshold sanity.

Read-only against data/nse.db. Two sections:

1. PRE-EVENT GATE BACKTEST — for every quarterly filing on record, compute the
   10-session run-up INTO the filing (ending the session before it), classify
   it with events.pre_event_risk.classify_pre_event_run, and measure the
   forward return a LONG taken just before the result would have earned
   (close[F-1] → close[F+1] and → close[F+3]). The 18.3 gate is justified if
   the BUY_RUMOR_IN_PLAY bucket shows materially worse forward returns than
   NORMAL.

2. PSYCH STREAK BASE RATES — across all symbol-days in the last ~250 sessions,
   how often do the Week-19 streak conditions occur (consecutive up days > 5,
   consecutive down days > 4, 5d return < −8%)? Extremes should be rare
   single-digit-% tails, or the states would be noise.

    python scripts/validate_pre_event_gate.py
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nse_data.events.calendar import _parse_nse_date          # noqa: E402
from nse_data.events.pre_event_risk import classify_pre_event_run  # noqa: E402
from nse_data.psychology.state_classifier import consecutive_moves  # noqa: E402

DB = "file:data/nse.db?mode=ro"


def _closes(conn, symbol) -> tuple[list[str], list[float]]:
    rows = conn.execute(
        "SELECT date, close FROM raw_bhavcopy_cm "
        "WHERE symbol=? AND series='EQ' AND close IS NOT NULL ORDER BY date",
        (symbol,),
    ).fetchall()
    return [r[0] for r in rows], [r[1] for r in rows]


def section_pre_event_gate(conn) -> None:
    filings = conn.execute(
        "SELECT symbol, filing_date FROM raw_financial_results "
        "WHERE period='Quarterly' AND filing_date IS NOT NULL",
    ).fetchall()
    by_symbol: dict[str, list[dt.date]] = defaultdict(list)
    for symbol, filing_date in filings:
        d = _parse_nse_date(filing_date)
        if d is not None:
            by_symbol[symbol].append(d)

    buckets: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"f1": [], "f3": []})
    used = skipped = 0
    for symbol, dates in by_symbol.items():
        dts, closes = _closes(conn, symbol)
        if len(closes) < 30:
            skipped += len(dates)
            continue
        index = {d: i for i, d in enumerate(dts)}
        for fd in set(dates):
            # session index of the last close BEFORE the filing date
            iso = fd.isoformat()
            prior = [i for d, i in index.items() if d < iso]
            if not prior:
                skipped += 1
                continue
            i0 = max(prior)
            if i0 < 11 or i0 + 3 >= len(closes):
                skipped += 1
                continue
            run10 = (closes[i0] - closes[i0 - 10]) / closes[i0 - 10] * 100.0
            state = classify_pre_event_run(run10)
            if state is None:     # only possible for a None run; run10 is always set
                continue
            f1 = (closes[i0 + 1] - closes[i0]) / closes[i0] * 100.0
            f3 = (closes[i0 + 3] - closes[i0]) / closes[i0] * 100.0
            buckets[state]["f1"].append(f1)
            buckets[state]["f3"].append(f3)
            used += 1

    print(f"\n=== 18.2/18.3 pre-event gate backtest "
          f"({used} filings used, {skipped} skipped) ===")
    print(f"{'class':<20} {'N':>5} {'fwd1 mean':>10} {'fwd1 med':>9} "
          f"{'fwd3 mean':>10} {'fwd3 med':>9} {'fwd3 win%':>9}")
    order = ("BUY_RUMOR_IN_PLAY", "MILD_ANTICIPATION", "NORMAL",
             "MILD_FEAR", "FEAR_PRICED", "SELL_RUMOR_IN_PLAY")
    for state in order:
        f1, f3 = buckets[state]["f1"], buckets[state]["f3"]
        if not f1:
            continue
        win = sum(1 for v in f3 if v > 0) / len(f3) * 100.0
        print(f"{state:<20} {len(f1):>5} {statistics.mean(f1):>10.2f} "
              f"{statistics.median(f1):>9.2f} {statistics.mean(f3):>10.2f} "
              f"{statistics.median(f3):>9.2f} {win:>9.1f}")


def section_streak_base_rates(conn, sessions: int = 250) -> None:
    symbols = [r[0] for r in conn.execute(
        "SELECT DISTINCT symbol FROM raw_fno_list",
    ).fetchall()]
    total = up_gt5 = down_gt4 = drop8 = 0
    for symbol in symbols:
        _, closes = _closes(conn, symbol)
        closes = closes[-sessions:]
        for i in range(15, len(closes)):
            window = closes[: i + 1]
            up, down = consecutive_moves(window[-15:])
            ret5 = ((window[-1] - window[-6]) / window[-6] * 100.0
                    if len(window) >= 6 and window[-6] else None)
            total += 1
            up_gt5 += up > 5
            down_gt4 += down > 4
            drop8 += ret5 is not None and ret5 < -8.0

    print(f"\n=== Week-19 streak base rates "
          f"({len(symbols)} F&O symbols × ~{sessions} sessions = {total} symbol-days) ===")
    if not total:
        print("no F&O symbols on file — skipped")
        return
    print(f"consecutive_up_days > 5 (FOMO leg):        {up_gt5 / total * 100:6.2f}%")
    print(f"consecutive_down_days > 4 (CAPIT leg):     {down_gt4 / total * 100:6.2f}%")
    print(f"5d return < -8% (DEAD_CAT leg):            {drop8 / total * 100:6.2f}%")


def main() -> int:
    conn = sqlite3.connect(DB, uri=True)
    try:
        section_pre_event_gate(conn)
        section_streak_base_rates(conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
