"""Backtest the sector signal engine: do its verdicts predict the move?

The roadmap's go-live gate (SECTOR_ENGINE_ROADMAP.md §"Backtest harness"): replay
stored result events, compute the engine's verdict for each, and measure the
direction-adjusted forward return (+1d, +10d) against the actual market reaction.
A sector rule or guard earns its place only if it lifts hit-rate vs the baseline.

Event timing is taken from ``raw_announcements`` (the real broadcast timestamp —
the market reacts when the result is disclosed, NOT at quarter-end), and the
numbers from ``extracted_financials`` joined on (symbol, period_ending, scope).
Forward returns come from the daily candles (``raw_intraday_candles`` interval
'day'); names without candle history (outside the top-500 set) are skipped.

    PYTHONPATH=src .venv/bin/python -u scripts/backtest_sector_signals.py
    .venv/bin/python -u scripts/backtest_sector_signals.py --sector metals
    .venv/bin/python -u scripts/backtest_sector_signals.py --min-confidence medium
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_CONF_RANK = {"low": 0, "medium": 1, "high": 2}


def _event_date(broadcast_dt: str | None) -> str | None:
    """'19-May-2026 14:39:21' → '2026-05-19' (the daily-candle date key)."""
    if not broadcast_dt:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(broadcast_dt.strip()[:len(fmt) + 4].strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _forward_closes(conn, symbol: str, event_date: str, horizons: list[int]) -> dict:
    """{0: entry_close, h: close h trading-days later} from daily candles."""
    rows = conn.execute(
        "SELECT close FROM raw_intraday_candles "
        "WHERE symbol = ? AND interval = 'day' "
        "AND date(ts, 'unixepoch', '+05:30') >= ? "
        "ORDER BY ts LIMIT ?",
        (symbol, event_date, max(horizons) + 2),
    ).fetchall()
    closes = [r[0] for r in rows if r[0]]
    out: dict[int, float] = {}
    if closes:
        out[0] = closes[0]
        for h in horizons:
            if h < len(closes):
                out[h] = closes[h]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--scope", default="standalone")
    ap.add_argument("--sector", help="restrict to one sector class (e.g. metals)")
    ap.add_argument("--min-confidence", default="low",
                    choices=("low", "medium", "high"),
                    help="confidence gate — drop signals below this tier")
    ap.add_argument("--horizons", default="1,10", help="forward trading-day horizons")
    args = ap.parse_args()
    horizons = [int(x) for x in args.horizons.split(",")]

    from nse_data.storage.db import open_db
    from nse_data.fundamentals.from_results import is_result_subject
    from nse_data.fundamentals.sectors import classify_result, sector_class_for

    conn = open_db(args.db)
    # Result events: an announcement with a known period, joined to its extracted
    # numbers. One row per (symbol, period) — the latest announcement wins.
    rows = conn.execute(
        "SELECT a.symbol, a.subject, a.broadcast_dt, "
        "       f.growth_json, f.narrative_json "
        "FROM raw_announcements a "
        "JOIN extracted_financials f "
        "  ON f.symbol = a.symbol AND f.period_ending = a.period_ending "
        "WHERE a.period_ending IS NOT NULL AND a.broadcast_dt IS NOT NULL "
        "  AND f.scope = ? AND f.growth_json IS NOT NULL",
        (args.scope,),
    ).fetchall()

    min_rank = _CONF_RANK[args.min_confidence]
    # group key -> list of direction-adjusted returns per horizon
    groups: dict[tuple, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    n_events = n_signals = n_priced = 0

    for symbol, subject, bdt, gjson, njson in rows:
        if not is_result_subject(subject):
            continue
        sector = sector_class_for(symbol).value
        if args.sector and sector != args.sector.lower():
            continue
        n_events += 1
        growth = json.loads(gjson) if gjson else None
        narrative = json.loads(njson) if njson else None
        v = classify_result(symbol, growth, None, narrative=narrative)
        if v.direction is None or _CONF_RANK.get(v.confidence, 0) < min_rank:
            continue
        n_signals += 1
        ed = _event_date(bdt)
        if not ed:
            continue
        closes = _forward_closes(conn, symbol, ed, horizons)
        if 0 not in closes:
            continue   # no candle history for this name/date
        n_priced += 1
        sign = 1.0 if v.direction == "long" else -1.0
        for h in horizons:
            if h in closes and closes[0]:
                ret = (closes[h] / closes[0] - 1.0) * 100.0 * sign
                groups[(sector, v.confidence)][h].append(ret)
                groups[("ALL", "ALL")][h].append(ret)

    conn.close()

    print(f"\nevents={n_events} signals(directional, gated)={n_signals} "
          f"priced={n_priced} | scope={args.scope} "
          f"min_conf={args.min_confidence} horizons={horizons}\n", flush=True)
    hdr = f"{'sector':<12}{'conf':<8}{'n':>5}"
    for h in horizons:
        hdr += f"{'hit%@'+str(h):>10}{'avg%@'+str(h):>10}"
    print(hdr)
    print("-" * len(hdr))
    for key in sorted(groups, key=lambda k: (k[0] != "ALL", k)):
        sector, conf = key
        n = len(groups[key][horizons[0]])
        if not n:
            continue
        line = f"{sector:<12}{conf:<8}{n:>5}"
        for h in horizons:
            rets = groups[key][h]
            if rets:
                hit = sum(1 for r in rets if r > 0) / len(rets) * 100
                avg = sum(rets) / len(rets)
                line += f"{hit:>9.0f}%{avg:>9.2f}%"
            else:
                line += f"{'—':>10}{'—':>10}"
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
