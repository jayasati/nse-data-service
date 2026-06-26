#!/usr/bin/env python
"""Combined signal forward-test: FPI-headwind sector name AND in a technical breakdown.

Hypothesis: a stock that is BOTH in an FPI-outflow sector AND technically broken down (swing
downtrend: close < SMA20 and SMA20 < SMA50, from daily bhavcopy — NOT the shelved intraday TA)
underperforms more reliably over the next fortnight than headwind alone.

Same harness as backtest_fpi_sector.py: entry D+3 (post-publication, no lookahead), exit next
report, excess vs the liquid universe. Buckets the headwind names into breakdown / no-breakdown.

    PYTHONPATH=src .venv/bin/python scripts/backtest_fpi_breakdown.py
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from nse_data.research.fpi_sector import _load_sector_map  # noqa: E402

HEADWIND_CR = 3000.0
PUB_LAG_DAYS = 3


def _entry(conn, symbol, after_date):
    """(date, close) of the first trading day on/after `after_date` — our entry bar."""
    return conn.execute("SELECT date, close FROM raw_bhavcopy_cm WHERE symbol=? AND series='EQ' "
                        "AND date>=? AND close>0 ORDER BY date LIMIT 1", (symbol, after_date)).fetchone()


def _close_on_or_after(conn, symbol, date):
    r = conn.execute("SELECT close FROM raw_bhavcopy_cm WHERE symbol=? AND series='EQ' AND date>=? "
                     "AND close>0 ORDER BY date LIMIT 1", (symbol, date)).fetchone()
    return r[0] if r else None


def _breakdown(conn, symbol, entry_date) -> bool | None:
    """Swing downtrend at entry: close < SMA20 and SMA20 < SMA50. None if <50 days of history."""
    closes = [r[0] for r in conn.execute(
        "SELECT close FROM raw_bhavcopy_cm WHERE symbol=? AND series='EQ' AND date<=? AND close>0 "
        "ORDER BY date DESC LIMIT 50", (symbol, entry_date))]
    if len(closes) < 50:
        return None
    px, sma20, sma50 = closes[0], sum(closes[:20]) / 20, sum(closes) / 50
    return px < sma20 and sma20 < sma50


def _sector_members(conn, smap, sector):
    out = set()
    for idx in smap.get(sector, []):
        out.update(r[0] for r in conn.execute(
            "SELECT symbol FROM raw_index_members WHERE index_name=?", (idx,)))
    return out


def _avg_ret(conn, syms, after_date, exit_date):
    rets = []
    for s in syms:
        e = _entry(conn, s, after_date)
        x = _close_on_or_after(conn, s, exit_date)
        if e and x and e[1]:
            rets.append((x / e[1] - 1) * 100)
    return (st.mean(rets), len(rets)) if rets else (None, 0)


def main():
    conn = sqlite3.connect("data/nse.db")
    smap = _load_sector_map()
    universe = set()
    for idxs in smap.values():
        for idx in idxs:
            universe.update(r[0] for r in conn.execute(
                "SELECT symbol FROM raw_index_members WHERE index_name=?", (idx,)))
    dates = [r[0] for r in conn.execute("SELECT DISTINCT as_of_date FROM raw_fpi_sector ORDER BY as_of_date")]
    rows = []
    for D, D2 in zip(dates, dates[1:]):
        after = (_dt.date.fromisoformat(D) + _dt.timedelta(days=PUB_LAG_DAYS)).isoformat()
        hw = set()
        for sector, net in conn.execute("SELECT sector, net_equity_cr FROM raw_fpi_sector "
                                        "WHERE as_of_date=? AND net_equity_cr IS NOT NULL", (D,)):
            if sector in smap and net <= -HEADWIND_CR:
                hw |= _sector_members(conn, smap, sector)
        if not hw:
            continue
        # split headwind into breakdown / no-breakdown (needs 50d history at entry)
        brk, nobrk = set(), set()
        for s in hw:
            b = _breakdown(conn, s, after)
            if b is True:
                brk.add(s)
            elif b is False:
                nobrk.add(s)
        uni_r, uni_n = _avg_ret(conn, universe, after, D2)
        if uni_r is None or uni_n < 20:
            continue
        hw_r, _ = _avg_ret(conn, hw, after, D2)
        bk_r, bk_n = _avg_ret(conn, brk, after, D2)
        nb_r, nb_n = _avg_ret(conn, nobrk, after, D2)
        rows.append({"D": D, "uni_r": uni_r,
                     "hw": (hw_r - uni_r) if hw_r is not None else None,
                     "brk": (bk_r - uni_r) if bk_r is not None else None, "brk_n": bk_n,
                     "nobrk": (nb_r - uni_r) if nb_r is not None else None, "nobrk_n": nb_n})

    def summ(key, label):
        vals = [r[key] for r in rows if r[key] is not None]
        if not vals:
            print(f"  {label:42s} no data"); return
        neg = sum(1 for v in vals if v < 0)
        print(f"  {label:42s} n={len(vals):2d} | mean={st.mean(vals):+.2f}% | "
              f"median={st.median(vals):+.2f}% | underperf {100*neg/len(vals):.0f}%")

    print(f"=== Combined FPI-headwind + technical-breakdown forward-test ===")
    print(f"usable fortnights: {len(rows)} | avg breakdown names/fortnight: "
          f"{st.mean([r['brk_n'] for r in rows]):.0f}, no-breakdown: {st.mean([r['nobrk_n'] for r in rows]):.0f}\n")
    summ("hw", "HEADWIND (all)")
    summ("brk", "HEADWIND + BREAKDOWN  (combined)")
    summ("nobrk", "HEADWIND + no breakdown (control)")
    conn.close()


if __name__ == "__main__":
    main()
