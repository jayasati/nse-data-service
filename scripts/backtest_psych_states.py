"""Forward-return backtest of the Week-19 psych states' DAILY legs.

The full states need intraday inputs (RSI(5m), session VWAP) whose 1-min
history isn't on this machine, so this tests the daily-bar legs only — enough
to check the SIGN of each Layer-7 confidence adjustment:

    FOMO proxy        consecutive_up_days > 5 AND daily volume rising
                      (full state adds RSI>78, >3% over VWAP)        expect: underperform  (long −0.20)
    CAPIT proxy       consecutive_down_days > 4 AND 5d ret < −8%
                      (full state adds RSI<22, <−3% VWAP, delivery)  expect: outperform    (long +0.15)
    DEAD_CAT (exact)  5d ret < −8% AND day up AND day volume below
                      the prior down-days' average                   expect: underperform  (long −0.15)
    FEAR proxy        consecutive_down_days ≥ 3 AND volume rising
                      (full state adds RSI<40)                       expect: mild underperform (long −0.08)
    BASELINE          every other symbol-day

Forward return = close[t] → close[t+k], k ∈ {1, 3, 5}. Read-only on
data/nse.db, F&O universe, last ~500 sessions per symbol.

    python scripts/backtest_psych_states.py
"""
from __future__ import annotations

import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nse_data.psychology.state_classifier import (  # noqa: E402
    consecutive_moves,
    volume_rising,
)

DB = "file:data/nse.db?mode=ro"
SESSIONS = 500
HORIZONS = (1, 3, 5)


def _series(conn, symbol):
    rows = conn.execute(
        "SELECT close, volume FROM raw_bhavcopy_cm "
        "WHERE symbol=? AND series='EQ' AND close IS NOT NULL "
        "ORDER BY date DESC LIMIT ?",
        (symbol, SESSIONS),
    ).fetchall()
    rows.reverse()
    return [r[0] for r in rows], [float(r[1] or 0) for r in rows]


def _dead_cat(closes, volumes, i) -> bool:
    """Exact daily legs of DEAD_CAT_BOUNCE at index i (full-day volume)."""
    if i < 6 or closes[i - 5] == 0:
        return False
    ret5 = (closes[i] - closes[i - 5]) / closes[i - 5] * 100.0
    if ret5 >= -8.0 or closes[i] <= closes[i - 1]:
        return False
    down_vols = [volumes[j] for j in range(i - 5, i)
                 if closes[j] < closes[j - 1] and volumes[j] > 0]
    return bool(down_vols) and volumes[i] < sum(down_vols) / len(down_vols)


def classify_day(closes, volumes, i) -> str:
    window = closes[max(0, i - 14): i + 1]
    vols = volumes[max(0, i - 14): i + 1]
    up, down = consecutive_moves(window)
    vol_up = volume_rising(vols)
    ret5 = ((closes[i] - closes[i - 5]) / closes[i - 5] * 100.0
            if i >= 5 and closes[i - 5] else None)
    if _dead_cat(closes, volumes, i):
        return "DEAD_CAT (exact)"
    if down > 4 and ret5 is not None and ret5 < -8.0:
        return "CAPIT proxy"
    if up > 5 and vol_up:
        return "FOMO proxy"
    if down >= 3 and vol_up:
        return "FEAR proxy"
    return "BASELINE"


def main() -> int:
    conn = sqlite3.connect(DB, uri=True)
    symbols = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM raw_fno_list")]
    buckets: dict[str, dict[int, list[float]]] = defaultdict(lambda: {k: [] for k in HORIZONS})

    for symbol in symbols:
        closes, volumes = _series(conn, symbol)
        if len(closes) < 30:
            continue
        for i in range(15, len(closes) - max(HORIZONS)):
            state = classify_day(closes, volumes, i)
            for k in HORIZONS:
                buckets[state][k].append(
                    (closes[i + k] - closes[i]) / closes[i] * 100.0)
    conn.close()

    order = ("FOMO proxy", "CAPIT proxy", "DEAD_CAT (exact)", "FEAR proxy", "BASELINE")
    print(f"=== Week-19 psych-state daily-leg backtest "
          f"({len(symbols)} F&O symbols, ~{SESSIONS} sessions) ===")
    print(f"{'state':<18} {'N':>7}", end="")
    for k in HORIZONS:
        print(f" {'fwd%d mean' % k:>10} {'fwd%d med' % k:>9}", end="")
    print(f" {'fwd3 win%':>9}")
    base3 = buckets["BASELINE"][3]
    for state in order:
        b = buckets[state]
        if not b[1]:
            continue
        print(f"{state:<18} {len(b[1]):>7}", end="")
        for k in HORIZONS:
            print(f" {statistics.mean(b[k]):>10.2f} {statistics.median(b[k]):>9.2f}", end="")
        win3 = sum(1 for v in b[3] if v > 0) / len(b[3]) * 100.0
        print(f" {win3:>9.1f}")
    if base3:
        print(f"\n(baseline fwd3 mean {statistics.mean(base3):+.2f}% — compare each "
              f"state against this, the absolute level carries the period's drift)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
