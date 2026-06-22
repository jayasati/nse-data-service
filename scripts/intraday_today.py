#!/usr/bin/env python3
"""Single-day case study: how intraday timing would have played the conviction top-N, TODAY only.

For each top-N conviction name (with its direction + stop), reads today's 5m bars from 09:15 and
simulates: (a) NAIVE entry at the open vs (b) intraday-TIMED entry — ORB break (OR high/low after
09:30) or VWAP reclaim/loss in the conviction direction. Reports each outcome marked to the latest
print, whether the conviction stop was hit, and whether timing helped. Illustrative (n is tiny), not
a statistical edge — the net-of-cost verdict already showed intraday round-trips don't validate.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nse_data.indicators.intraday_ohlcv import read_intraday_5m  # noqa: E402 (merges live feed)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _today_bars(conn, sym):
    """Today's 5m bars (09:15→latest) from the live-merged source, columns lowercased."""
    df = read_intraday_5m(conn, sym)
    if df is None or df.empty:
        return None
    df = df.rename(columns=str.lower)
    today = dt.datetime.now(IST).date()
    mask = [dt.datetime.fromtimestamp(int(t), IST).date() == today for t in df.index]
    return df[mask].reset_index(drop=True)


def _vwap(df):
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    v = df["volume"].clip(lower=0)
    return ((tp * v).cumsum() / v.cumsum().replace(0, 1)).to_numpy()


def main() -> int:
    topn = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    conn = sqlite3.connect(str(ROOT / "data/nse.db"), timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    today = dt.datetime.now(IST).date().isoformat()
    rows = conn.execute(
        "SELECT symbol, direction, entry, stop, t2 FROM conviction_daily WHERE as_of_date="
        "(SELECT MAX(as_of_date) FROM conviction_daily) ORDER BY composite DESC LIMIT ?",
        (topn,)).fetchall()

    print(f"Conviction top-{topn} — intraday timing case study for {today} (from 09:15)\n")
    print(f"{'sym':11}{'dir':6}{'open':>8}{'OR(lo-hi)':>17}{'timed entry':>22}"
          f"{'now':>8}{'naive%':>8}{'timed%':>8}{'stop?':>7}")
    helped = naive_tot = timed_tot = took = 0
    for sym, dirn, entry, stop, t2 in rows:
        df = _today_bars(conn, sym)
        if df is None or len(df) < 4:
            print(f"{sym:11}{dirn:6}  no/short intraday data"); continue
        o = float(df["open"].iloc[0])
        hi = df["high"].to_numpy(); lo = df["low"].to_numpy(); cl = df["close"].to_numpy()
        vw = _vwap(df)
        orh = float(hi[:3].max()); orl = float(lo[:3].min())
        last = float(cl[-1])
        long = dirn == "LONG"
        # intraday-timed entry: ORB break in the conviction direction after the 3-bar opening range
        te = tei = None
        for i in range(3, len(df)):
            if long and hi[i] > orh and cl[i] > vw[i]:        # break OR-high + above VWAP
                te, tei = orh, i; break
            if not long and lo[i] < orl and cl[i] < vw[i]:    # break OR-low + below VWAP
                te, tei = orl, i; break
        naive_pct = (last - o) / o * 100 * (1 if long else -1)
        if te is None:
            tlabel = "no trigger (skipped)"; timed_pct = 0.0; stophit = "—"
        else:
            timed_pct = (last - te) / te * 100 * (1 if long else -1)
            after_lo = lo[tei:].min(); after_hi = hi[tei:].max()
            stophit = "STOP" if ((long and after_lo <= stop) or (not long and after_hi >= stop)) else "ok"
            tlabel = f"{te:.1f} @bar{tei}"
            took += 1; timed_tot += timed_pct; naive_tot += naive_pct
            helped += timed_pct > naive_pct
        print(f"{sym:11}{dirn:6}{o:>8.1f}{f'{orl:.1f}-{orh:.1f}':>17}{tlabel:>22}"
              f"{last:>8.1f}{naive_pct:>+8.2f}{timed_pct:>+8.2f}{stophit:>7}")
    if took:
        print(f"\nof {took} that triggered: timed-entry avg {timed_tot/took:+.2f}% vs "
              f"naive {naive_tot/took:+.2f}% · timing beat naive on {helped}/{took}")
        print(f"({len(rows)-took} names never triggered the ORB/VWAP filter → skipped, no trade)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
