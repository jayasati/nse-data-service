"""Detect significant single-day intraday moves, measured FROM THE OPEN (gap
excluded), from 1-minute candles. See plans/INTRADAY_MOVE_RESEARCH_PLAN.md.

For each (symbol, trading-day) we read the day's 1-min bars and, relative to the
opening price (the first bar's open — so the overnight gap is excluded):

  up_move_pct   = (day high  − open) / open × 100      how far it ROSE above open
  down_move_pct = (day low   − open) / open × 100      how far it FELL below open
  net_pct       = (day close − open) / open × 100      where it closed vs open
  gap_pct       = (open − prev_close) / prev_close × 100   (reference only)

We keep days with up_move_pct ≥ 2 OR down_move_pct ≤ −2, and for each record:
  * the dominant direction + the time RANGE of that move leg (trough→peak / peak→trough)
  * the SEQUENCE (did the dip or the pop come first)
  * a CONSISTENCY score — path efficiency of the dominant leg (1.0 = straight,
    one-way move; lower = choppy) plus max retracement — so "constant rise /
    constant fall (slight variation OK)" days rank above whipsaws
  * a PATTERN label: constant_up | constant_down | dip_then_rally | pop_then_fade | whipsaw

Writes the `intraday_move_events` table.

    PYTHONPATH=src .venv/bin/python scripts/build_intraday_move_events.py
    ... --symbols RELIANCE,TCS         # subset (validation)
    ... --interval minute --min-move 2
"""
from __future__ import annotations

import argparse
import datetime as dt
import sqlite3

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _ist(ts: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(ts, IST)


def _leg_stats(closes: list[float], a: int, b: int) -> tuple[float, float]:
    """Efficiency and max-retracement of the close path over bars [a..b].

    efficiency = |net| / sum|bar-to-bar move|   (1.0 = perfectly one-way)
    retrace    = largest counter-move against the net direction, as % of |net|
    """
    if b <= a:
        return 0.0, 0.0   # zero-duration leg = instantaneous spike, NOT a constant move
    seg = closes[a:b + 1]
    net = seg[-1] - seg[0]
    path = sum(abs(seg[i + 1] - seg[i]) for i in range(len(seg) - 1))
    eff = abs(net) / path if path > 0 else 0.0
    # max retracement: deepest pullback against the move direction
    if net >= 0:                       # upward leg → worst peak-to-trough dip
        peak = seg[0]; worst = 0.0
        for p in seg:
            peak = max(peak, p)
            worst = max(worst, peak - p)
    else:                              # downward leg → worst trough-to-peak bounce
        trough = seg[0]; worst = 0.0
        for p in seg:
            trough = min(trough, p)
            worst = max(worst, p - trough)
    retrace = (worst / abs(net) * 100) if net else 0.0
    return round(eff, 3), round(retrace, 1)


def _classify(up: float, down: float, low_t_idx: int, high_t_idx: int,
              min_move: float) -> str:
    DIP = 0.7   # a "meaningful" cross of the open the other way
    went_up = up >= min_move
    went_down = down <= -min_move
    dipped = down <= -DIP
    popped = up >= DIP
    if went_up and not dipped:
        return "constant_up"
    if went_down and not popped:
        return "constant_down"
    if went_up and dipped and low_t_idx < high_t_idx:
        return "dip_then_rally"
    if went_down and popped and high_t_idx < low_t_idx:
        return "pop_then_fade"
    return "whipsaw"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--interval", default="minute")
    ap.add_argument("--symbols", help="comma list (default: all with this interval)")
    ap.add_argument("--min-move", type=float, default=2.0, help="threshold %% from open")
    args = ap.parse_args()
    MIN = args.min_move

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA busy_timeout=60000")   # coexist with the live collector's writes
    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        syms = [s for (s,) in conn.execute(
            "SELECT DISTINCT symbol FROM raw_intraday_candles WHERE interval=?",
            (args.interval,))]

    conn.execute("DROP TABLE IF EXISTS intraday_move_events")
    conn.execute("""
        CREATE TABLE intraday_move_events (
            symbol TEXT, date TEXT, direction TEXT, move_pct REAL,
            up_move_pct REAL, down_move_pct REAL, net_pct REAL, gap_pct REAL,
            open REAL, day_high REAL, day_low REAL, day_close REAL,
            high_time TEXT, low_time TEXT, move_start TEXT, move_end TEXT,
            consistency REAL, efficiency REAL, leg_minutes REAL,
            max_retrace_pct REAL, pattern TEXT, n_bars INTEGER,
            source_interval TEXT,
            PRIMARY KEY (symbol, date)
        )""")

    cols = ("symbol", "date", "direction", "move_pct", "up_move_pct", "down_move_pct",
            "net_pct", "gap_pct", "open", "day_high", "day_low", "day_close",
            "high_time", "low_time", "move_start", "move_end", "consistency",
            "efficiency", "leg_minutes", "max_retrace_pct", "pattern", "n_bars",
            "source_interval")
    ins = f"INSERT OR REPLACE INTO intraday_move_events ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})"

    n_events = 0
    for si, sym in enumerate(syms, 1):
        bars = conn.execute(
            "SELECT ts, open, high, low, close FROM raw_intraday_candles "
            "WHERE symbol=? AND interval=? ORDER BY ts",
            (sym, args.interval)).fetchall()
        # group by IST date, preserving order
        days: dict[str, list] = {}
        order: list[str] = []
        for ts, o, h, l, c in bars:
            if o is None or h is None or l is None or c is None:
                continue
            d = _ist(ts).date().isoformat()
            if d not in days:
                days[d] = []; order.append(d)
            days[d].append((ts, o, h, l, c))

        prev_close = None
        rows = []
        for d in order:
            day = days[d]
            day_close = day[-1][4]
            if len(day) < 5:
                prev_close = day_close
                continue
            opn = day[0][1]
            if not opn:
                prev_close = day_close
                continue
            highs = [b[2] for b in day]; lows = [b[3] for b in day]; closes = [b[4] for b in day]
            hi = max(highs); lo = min(lows)
            hi_idx = highs.index(hi); lo_idx = lows.index(lo)
            up = (hi - opn) / opn * 100
            down = (lo - opn) / opn * 100
            net = (day_close - opn) / opn * 100
            gap = ((opn - prev_close) / prev_close * 100) if prev_close else None
            prev_close = day_close

            if up < MIN and down > -MIN:
                continue   # no qualifying move from open

            up_dom = up >= abs(down)
            direction = "up" if up_dom else "down"
            move_pct = round(up if up_dom else down, 2)
            # leg window: for up → trough-before-high .. high; for down → peak-before-low .. low
            if up_dom:
                pre = lows[:hi_idx + 1]
                start_idx = pre.index(min(pre)) if pre else 0
                end_idx = hi_idx
            else:
                pre = highs[:lo_idx + 1]
                start_idx = pre.index(max(pre)) if pre else 0
                end_idx = lo_idx
            a, b = min(start_idx, end_idx), max(start_idx, end_idx)
            eff, retr = _leg_stats(closes, a, b)
            leg_min = round((day[b][0] - day[a][0]) / 60.0, 1)
            # consistency = smoothness × duration: a "constant" move must be both
            # one-way AND sustained. A 2-min monotonic spike (eff 1.0 but ~2 min)
            # is down-weighted; a steady 30-min+ grind keeps full credit.
            consistency = round(eff * min(1.0, leg_min / 30.0), 3)
            pattern = _classify(up, down, lo_idx, hi_idx, MIN)
            rows.append((
                sym, d, direction, move_pct, round(up, 2), round(down, 2),
                round(net, 2), round(gap, 2) if gap is not None else None,
                round(opn, 2), round(hi, 2), round(lo, 2), round(day_close, 2),
                _ist(day[hi_idx][0]).strftime("%H:%M"), _ist(day[lo_idx][0]).strftime("%H:%M"),
                _ist(day[start_idx][0]).strftime("%H:%M"), _ist(day[end_idx][0]).strftime("%H:%M"),
                consistency, eff, leg_min, retr, pattern, len(day), args.interval,
            ))
        if rows:
            conn.executemany(ins, rows)
            n_events += len(rows)
        if si % 50 == 0:
            conn.commit()
            print(f"  [{si}/{len(syms)}] {sym}: {n_events} events so far", flush=True)

    conn.commit()
    from collections import Counter
    dist = Counter(r[0] for r in conn.execute("SELECT pattern FROM intraday_move_events"))
    print(f"\nDONE: {n_events} move events across {len(syms)} symbols")
    print("patterns:", dict(dist))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
