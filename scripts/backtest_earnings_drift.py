"""Post-earnings-announcement drift, conditioned on a LARGE reaction (the live thread).

P1 found no edge across ALL results. The move-cause work hinted that earnings-driven
BIG moves keep drifting (+3.9%/10d, but n=15–20). This tests that directly and at
scale: take every result announcement (real broadcast time), measure the EARNINGS
REACTION from daily candles, keep only the big ones (|reaction| ≥ threshold), and
measure whether the move CONTINUES (momentum/PEAD) net of cost — on the CLEAN
core+tradeable universe only (C volatile excluded — that's where the last artifact hid).

  reaction:  intraday disclosure → event-day open→close; entry = event close.
             after-hours        → gap to next open;      entry = next open.
  direction = sign(reaction) (momentum). Positive net drift ⇒ continuation edge;
  significantly negative ⇒ the pop reverses (a fade/short).
  Excess vs benchmark ETF, minus round-trip cost, bootstrap 95% CI, min-n gate.

Uses daily candles (history back to 2023-06) — NOT minute — so it is not limited to
the recent move-event window. Needs announcement coverage for the window (backfill).

    PYTHONPATH=src .venv/bin/python -u scripts/backtest_earnings_drift.py \
        --min-reaction 5 --since 2023-06-13 --grades "A core,B tradeable"
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_MKT_OPEN, _MKT_CLOSE = (9, 15), (15, 30)
_DEFAULT_GRADES = "A core,B tradeable"


def _event_dt(s):
    if not s:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip()[:len(fmt) + 4].strip(), fmt)
        except ValueError:
            continue
    return None


def _kind(dt):
    if dt is None:
        return "after_hours"
    return "intraday" if _MKT_OPEN <= (dt.hour, dt.minute) <= _MKT_CLOSE else "after_hours"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--since", default="2023-06-13")
    ap.add_argument("--until", default="9999-12-31")
    ap.add_argument("--min-reaction", type=float, default=5.0,
                    help="abs %% earnings-day reaction to qualify as a 'big' move")
    ap.add_argument("--horizons", default="1,2,3,5,10")
    ap.add_argument("--primary-horizon", type=int, default=10)
    ap.add_argument("--cost-pct", type=float, default=0.20)
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--bootstrap", type=int, default=10000)
    ap.add_argument("--grades", default=_DEFAULT_GRADES)
    ap.add_argument("--dedup-days", type=int, default=45,
                    help="collapse multi-part result filings: one event per symbol per window")
    args = ap.parse_args()
    horizons = [int(x) for x in args.horizons.split(",")]
    ph = args.primary_horizon
    grades = tuple(g.strip() for g in args.grades.split(",") if g.strip())

    from nse_data.storage.db import open_db
    from nse_data.research import edge_stats
    from nse_data.fundamentals.sectors import sector_class_for
    from nse_data.fundamentals.from_results import is_result_subject

    conn = open_db(args.db)
    bench_for, series, avail = edge_stats.bench_resolver(conn)
    universe = {s: g for s, g in conn.execute("SELECT symbol, grade FROM tradeable_universe")}

    anns = conn.execute(
        "SELECT symbol, subject, broadcast_dt FROM raw_announcements "
        "WHERE broadcast_dt IS NOT NULL ORDER BY broadcast_dt").fetchall()

    cells: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    last_kept: dict[str, str] = {}
    n_events = n_big = n_priced = 0
    react_dir = [0, 0]   # [up, down] among big reactions

    for symbol, subject, bdt in anns:
        if not is_result_subject(subject):
            continue
        if universe.get(symbol) not in grades:        # clean universe hard-filter
            continue
        dt = _event_dt(bdt)
        ed = dt.date().isoformat() if dt else None
        if not ed or ed < args.since or ed > args.until:
            continue
        # collapse multi-part filings into one event per symbol per dedup window
        prev = last_kept.get(symbol)
        if prev and (datetime.fromisoformat(ed) - datetime.fromisoformat(prev)).days < args.dedup_days:
            continue
        last_kept[symbol] = ed
        n_events += 1

        bars = conn.execute(
            "SELECT date(ts,'unixepoch','+05:30') d, open, close FROM raw_intraday_candles "
            "WHERE symbol=? AND interval='day' AND date(ts,'unixepoch','+05:30') >= ? "
            "AND close IS NOT NULL ORDER BY ts LIMIT ?",
            (symbol, ed, max(horizons) + 4)).fetchall()
        if len(bars) < 2 or bars[0][0] != ed or not bars[0][1] or not bars[0][2]:
            continue
        kind = _kind(dt)
        if kind == "intraday":
            reaction = (bars[0][2] / bars[0][1] - 1.0) * 100.0    # open→close
            entry_idx, entry = 0, bars[0][2]
        else:
            if not bars[1][1]:
                continue
            reaction = (bars[1][1] / bars[0][2] - 1.0) * 100.0     # gap to next open
            entry_idx, entry = 1, bars[1][1]
        if abs(reaction) < args.min_reaction or not entry:
            continue
        n_big += 1
        react_dir[0 if reaction > 0 else 1] += 1

        entry_date = bars[entry_idx][0]
        sign = 1.0 if reaction > 0 else -1.0
        sector = sector_class_for(symbol).value
        bser, bdates = series[bench_for(sector)]
        b0 = edge_stats.close_on_or_before(bser, bdates, entry_date)
        priced = False
        for h in horizons:
            j = entry_idx + h
            if j < len(bars) and bars[j][2]:
                exit_date, ret = bars[j][0], (bars[j][2] / entry - 1.0) * 100.0
                b1 = edge_stats.close_on_or_before(bser, bdates, exit_date)
                bench = (b1 / b0 - 1.0) * 100.0 if (b0 and b1) else 0.0
                net = (ret - bench) * sign - args.cost_pct
                cells["ALL"][h].append(net)
                cells[(kind)][h].append(net)
                cells[sector][h].append(net)
                priced = True
        if priced:
            n_priced += 1

    conn.close()

    print(f"\n=== EARNINGS BIG-REACTION DRIFT (|reaction|≥{args.min_reaction}%) ===")
    print(f"window {args.since}→{args.until} grades={list(grades)} cost={args.cost_pct}% "
          f"min_n={args.min_n} primary_h={ph}d")
    print(f"result events={n_events} big(|reaction|≥{args.min_reaction}%)={n_big} "
          f"(up={react_dir[0]} down={react_dir[1]}) priced={n_priced}")

    print(f"\n=== DRIFT VERDICT @ {ph}d (momentum; net-of-cost excess; "
          f"CI excl 0 ⇒ TRADE, neg ⇒ reaction REVERSES) ===")
    print(f"{'bucket':<14}{'n':>6}{'net%':>8}{'hit%':>6}{'95% CI':>18}  verdict")
    print("-" * 60)
    keys = ["ALL", "intraday", "after_hours"] + sorted(
        k for k in cells if k not in ("ALL", "intraday", "after_hours"))
    for k in keys:
        rets = cells[k].get(ph, [])
        if not rets:
            continue
        mean = sum(rets) / len(rets)
        hit = sum(1 for r in rets if r > 0) / len(rets) * 100
        ci = edge_stats.bootstrap_ci(rets, args.bootstrap)
        verd = edge_stats.verdict(len(rets), ci, args.min_n)
        ci_txt = f"[{ci[0]:+.2f},{ci[1]:+.2f}]" if ci else "—"
        print(f"{str(k):<14}{len(rets):>6}{mean:>7.2f}%{hit:>5.0f}%{ci_txt:>18}  {verd}")

    print(f"\n{'bucket':<14}{'n':>6} " + "".join(f"{'net@'+str(h):>9}" for h in horizons))
    print("-" * (21 + 9 * len(horizons)))
    for k in keys:
        n0 = len(cells[k].get(horizons[0], []))
        if n0 < args.min_n:
            continue
        line = f"{str(k):<14}{n0:>6} "
        for h in horizons:
            r = cells[k].get(h, [])
            line += f"{(sum(r)/len(r)):>8.2f}%" if r else f"{'—':>9}"
        print(line)
    print(f"\n(net@h = reaction-direction EXCESS over benchmark ETF minus {args.cost_pct}% "
          "cost, h sessions after entry. + = drift continues; − = reverses.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
