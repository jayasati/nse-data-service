#!/usr/bin/env python
"""Forward, out-of-sample per-factor attribution for the conviction engine.

For every logged signal (conviction_factor_log), join the DIRECTIONAL forward N-day return from
bhavcopy (entry_close -> close N sessions later, signed +1 LONG / -1 SHORT so positive = the trade
worked), then report each factor's information coefficient (Spearman of factor score vs the trade
working) and whether higher conviction_adj actually produced better directional returns.

This accumulates OUT-OF-SAMPLE as days pass -- the only honest way to calibrate the weights from LIVE
signals, including options/positioning which have NO backtest history. Until ~N forward sessions exist
for the earliest signals it reports 'insufficient' (same discipline as the paper-trade verdict).

Usage: PYTHONPATH=src .venv/bin/python scripts/conviction_attribution.py [--horizon 5] [--db data/nse.db]
"""
from __future__ import annotations

import argparse
import sqlite3

FACTORS = ["options", "structure", "positioning", "volume", "rel_strength", "vol_expansion"]


def _ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0] * len(xs)
    for rank, i in enumerate(order):
        r[i] = rank
    return r


def spearman(xs, ys):
    n = len(xs)
    if n < 8:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    return num / (dx * dy) if dx and dy else None


def fwd_close(conn, sym, date, horizon):
    rows = conn.execute("SELECT close FROM raw_bhavcopy_cm WHERE symbol=? AND date>? "
                        "ORDER BY date ASC LIMIT ?", (sym, date, horizon)).fetchall()
    return rows[-1][0] if len(rows) >= horizon else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--db", default="data/nse.db")
    a = ap.parse_args()
    conn = sqlite3.connect(a.db)
    conn.execute("PRAGMA busy_timeout=30000")
    rows = conn.execute(
        "SELECT snapshot_date, symbol, direction, entry_close, options, structure, positioning, "
        "volume, rel_strength, vol_expansion, conviction_adj, conf_label FROM conviction_factor_log"
    ).fetchall()
    if not rows:
        print("conviction_factor_log empty — populates from the next 09:10 pre-market run.")
        return

    data = []
    for sd, sym, direction, ec, opt, struc, pos, vol, rs, ve, conv, conf in rows:
        fc = fwd_close(conn, sym, sd, a.horizon)
        if not ec or not fc:
            continue
        ret = (fc - ec) / ec * 100.0
        dret = ret * (1 if (direction or "LONG") == "LONG" else -1)   # directional: + = trade worked
        data.append(({"options": opt, "structure": struc, "positioning": pos, "volume": vol,
                      "rel_strength": rs, "vol_expansion": ve}, dret, conv, conf))

    ndates = len(set(r[0] for r in rows))
    n = len(data)
    print(f"conviction_factor_log: {len(rows)} signals over {ndates} day(s); "
          f"{n} have a full {a.horizon}d forward return.")
    if n < 30:
        print(f"\nINSUFFICIENT forward data (n={n}). Need the log to accumulate ~{a.horizon}+ "
              f"trading days. Check back in ~2 weeks — this is expected early (same as the paper "
              f"verdict).")
        return

    print(f"\n── Forward {a.horizon}-day DIRECTIONAL attribution (factor score vs 'trade worked') ──")
    print(f"{'FACTOR':16}{'IC':>8}{'n':>6}   (IC>0 = higher score → trade works; want the high-weight "
          f"factors highest)")
    ranked = []
    for f in FACTORS:
        xs = [d[0][f] for d in data if d[0][f] is not None]
        ys = [d[1] for d in data if d[0][f] is not None]
        ic = spearman(xs, ys)
        ranked.append((f, ic, len(xs)))
        print(f"{f:16}{('%+.3f' % ic) if ic is not None else 'n/a':>8}{len(xs):>6}")

    # Does higher conviction_adj actually deliver? Tertile the directional return by conviction_adj.
    cv = sorted([(d[2], d[1]) for d in data if d[2] is not None])
    if len(cv) >= 30:
        k = len(cv) // 3
        lo = sum(x[1] for x in cv[:k]) / k
        hi = sum(x[1] for x in cv[-k:]) / k
        print(f"\nconviction_adj tertiles → mean directional {a.horizon}d return: "
              f"bottom {lo:+.2f}% · top {hi:+.2f}% · spread {hi - lo:+.2f}%")
    al = [d[1] for d in data if d[3] == "ALIGNED"]
    if al:
        print(f"ALIGNED-only (n={len(al)}): mean directional {a.horizon}d return "
              f"{sum(al) / len(al):+.2f}%")
    print("\nNote: directional IC weights each factor's REAL forward edge on live signals. As n grows,"
          "\nthe high-weight factors (options 25% / structure 22% / positioning 22%) should show the"
          "\nhighest IC; if a low-weight factor out-ranks them, that's the signal to re-weight.")


if __name__ == "__main__":
    main()
