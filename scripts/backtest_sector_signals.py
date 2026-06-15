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


def _event_dt(broadcast_dt: str | None):
    """'19-May-2026 14:39:21' → datetime, or None."""
    if not broadcast_dt:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(broadcast_dt.strip()[:len(fmt) + 4].strip(), fmt)
        except ValueError:
            continue
    return None


def _event_date(broadcast_dt: str | None) -> str | None:
    dt = _event_dt(broadcast_dt)
    return dt.date().isoformat() if dt else None


_MKT_OPEN = (9, 15)
_MKT_CLOSE = (15, 30)


def _session_kind(dt) -> str:
    """'intraday' if disclosed within market hours (9:15–15:30) — reaction is
    tradeable in-session; else 'after_hours' (incl. pre-open) — the stock gaps at
    the next open and only the post-gap drift is capturable."""
    if dt is None:
        return "after_hours"
    return "intraday" if _MKT_OPEN <= (dt.hour, dt.minute) <= _MKT_CLOSE else "after_hours"


def _entry_forwards(conn, symbol: str, event_date: str, kind: str, horizons: list[int]):
    """Realistic entry + capturable forward returns from daily bars.

    intraday    → entry = event-day CLOSE (you reacted in-session), gap = None.
    after_hours → entry = NEXT session OPEN (you can only act post-gap);
                  gap% = next-open / event-day-close − 1 (the un-capturable part).
    Returns (entry_price, gap_pct, {h: pct_return entry→close h sessions later})
    or None when there aren't enough bars."""
    rows = conn.execute(
        "SELECT open, close FROM raw_intraday_candles "
        "WHERE symbol = ? AND interval = 'day' "
        "AND date(ts, 'unixepoch', '+05:30') >= ? "
        "ORDER BY ts LIMIT ?",
        (symbol, event_date, max(horizons) + 3),
    ).fetchall()
    bars = [(o, c) for o, c in rows if c]
    if not bars:
        return None
    if kind == "intraday":
        entry_idx, entry, gap = 0, bars[0][1], None
    else:
        if len(bars) < 2 or not bars[0][1]:
            return None
        entry_idx, entry = 1, bars[1][0]
        gap = (bars[1][0] / bars[0][1] - 1.0) * 100.0
    if not entry:
        return None
    out: dict[int, float] = {}
    for h in horizons:
        j = entry_idx + h
        if j < len(bars) and bars[j][1]:
            out[h] = (bars[j][1] / entry - 1.0) * 100.0
    return entry, gap, out


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
    # Event = a result announcement (the real disclosure time). raw_announcements.
    # period_ending is not populated by the XBRL pipeline, so we DON'T join on it;
    # instead we match each result announcement to the quarter it reports — the
    # latest extracted quarter that ENDED within ~100 days before the broadcast
    # (results disclose 1-2 months after quarter-end). Dedup to the EARLIEST
    # announcement per (symbol, period) = first disclosure.
    anns = conn.execute(
        "SELECT symbol, subject, broadcast_dt FROM raw_announcements "
        "WHERE broadcast_dt IS NOT NULL ORDER BY broadcast_dt",
    ).fetchall()

    min_rank = _CONF_RANK[args.min_confidence]
    groups: dict[tuple, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    gaps: dict[str, list[float]] = defaultdict(list)   # timing -> direction-adj gap%
    n_events = n_signals = n_priced = 0
    seen: set[tuple] = set()           # (symbol, period_ending) already counted

    for symbol, subject, bdt in anns:
        if not is_result_subject(subject):
            continue
        ed = _event_date(bdt)
        if not ed:
            continue
        # the quarter being reported: latest extracted period strictly before the
        # announcement, within 100 days, with growth.
        row = conn.execute(
            "SELECT period_ending, growth_json, narrative_json FROM extracted_financials "
            "WHERE symbol = ? AND scope = ? AND growth_json IS NOT NULL "
            "AND period_ending < ? AND julianday(?) - julianday(period_ending) <= 100 "
            "ORDER BY period_ending DESC LIMIT 1",
            (symbol, args.scope, ed, ed),
        ).fetchone()
        if row is None:
            continue
        period, gjson, njson = row
        if (symbol, period) in seen:
            continue                   # earliest disclosure wins (anns sorted asc)
        seen.add((symbol, period))
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
        kind = _session_kind(_event_dt(bdt))     # intraday vs after_hours (gap risk)
        res = _entry_forwards(conn, symbol, ed, kind, horizons)
        if res is None:
            continue   # no candle history for this name/date
        _, gap, fwd = res
        if not fwd:
            continue
        n_priced += 1
        sign = 1.0 if v.direction == "long" else -1.0
        if gap is not None:
            gaps[kind].append(gap * sign)
        for h in horizons:
            if h in fwd:
                r = fwd[h] * sign        # capturable return from the realistic entry
                groups[(sector, v.confidence)][h].append(r)
                groups[("ALL", "ALL")][h].append(r)
                groups[("TIMING", kind)][h].append(r)

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
        if sector == "TIMING":
            continue                      # reported separately in the gap section
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

    # Gap analysis: how much of the move was un-capturable (close→next-open) for
    # after-hours disclosures vs intraday, and the avg gap you'd have chased.
    print(f"\n{'timing':<12}{'n':>5}{'avg gap%':>10}"
          + "".join(f"{'cap%@'+str(h):>10}" for h in horizons))
    print("-" * (27 + 10 * len(horizons)))
    for kind in ("intraday", "after_hours"):
        key = ("TIMING", kind)
        n = len(groups[key][horizons[0]]) if key in groups else 0
        if not n:
            continue
        g = gaps.get(kind, [])
        gap_txt = f"{sum(g)/len(g):>9.2f}%" if g else f"{'—':>10}"
        line = f"{kind:<12}{n:>5}{gap_txt}"
        for h in horizons:
            rets = groups[key][h]
            line += f"{(sum(rets)/len(rets)):>9.2f}%" if rets else f"{'—':>10}"
        print(line)
    print("\n(cap% = capturable return from the realistic entry: event-day close "
          "for intraday, next-day OPEN for after-hours. A large after-hours gap% "
          "with small cap% = the move was priced before you could act.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
