#!/usr/bin/env python
"""Forward-test: do FPI-headwind sector names underperform over the NEXT fortnight?

For each fortnightly NSDL sector report (date D), the headwind sectors (net FPI equity <= -HEADWIND
cr) map to member stocks (via NSE sectoral indices). We measure those names' return over the next
fortnight vs the liquid-universe benchmark — entry AFTER publication (D + LAG days, so the flows are
known: NO lookahead), exit at the next report date. Aggregated across all fortnights with price data.

    PYTHONPATH=src .venv/bin/python scripts/backtest_fpi_sector.py
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from nse_data.research.fpi_sector import _load_sector_map  # noqa: E402

HEADWIND_CR = 3000.0      # sector net FPI equity flow threshold (₹ cr)
PUB_LAG_DAYS = 3          # we only know a fortnight's flows ~3 days after it ends


def _close_on_or_after(conn, symbol, date):
    r = conn.execute("SELECT close FROM raw_bhavcopy_cm WHERE symbol=? AND series='EQ' AND date>=? "
                     "AND close>0 ORDER BY date LIMIT 1", (symbol, date)).fetchone()
    return r[0] if r else None


def _sector_members(conn, smap, sector):
    out = set()
    for idx in smap.get(sector, []):
        out.update(r[0] for r in conn.execute(
            "SELECT symbol FROM raw_index_members WHERE index_name=?", (idx,)))
    return out


def _avg_ret(conn, syms, entry_date, exit_date):
    rets = []
    for s in syms:
        e = _close_on_or_after(conn, s, entry_date)
        x = _close_on_or_after(conn, s, exit_date)
        if e and x:
            rets.append((x / e - 1) * 100)
    return (st.mean(rets), len(rets)) if rets else (None, 0)


def main():
    conn = sqlite3.connect("data/nse.db")
    smap = _load_sector_map()
    universe = set()
    for idxs in smap.values():
        for idx in idxs:
            universe.update(r[0] for r in conn.execute(
                "SELECT symbol FROM raw_index_members WHERE index_name=?", (idx,)))
    dates = [r[0] for r in conn.execute("SELECT DISTINCT as_of_date FROM raw_fpi_sector "
                                        "ORDER BY as_of_date")]
    print(f"fortnights with sector data: {len(dates)} ({dates[0]}..{dates[-1]}); "
          f"universe={len(universe)} liquid names\n")
    rows = []
    for D, D2 in zip(dates, dates[1:]):
        entry = (_dt.date.fromisoformat(D) + _dt.timedelta(days=PUB_LAG_DAYS)).isoformat()
        # headwind / tailwind sector members at D
        hw, tw = set(), set()
        for sector, net in conn.execute("SELECT sector, net_equity_cr FROM raw_fpi_sector "
                                        "WHERE as_of_date=? AND net_equity_cr IS NOT NULL", (D,)):
            if sector not in smap:
                continue
            if net <= -HEADWIND_CR:
                hw |= _sector_members(conn, smap, sector)
            elif net >= HEADWIND_CR:
                tw |= _sector_members(conn, smap, sector)
        hw_r, hw_n = _avg_ret(conn, hw, entry, D2)
        tw_r, tw_n = _avg_ret(conn, tw, entry, D2)
        uni_r, uni_n = _avg_ret(conn, universe, entry, D2)
        if uni_r is None or uni_n < 20:        # need a real universe benchmark + future prices
            continue
        rows.append({"D": D, "hw_excess": (hw_r - uni_r) if hw_r is not None else None,
                     "tw_excess": (tw_r - uni_r) if tw_r is not None else None,
                     "hw_n": hw_n, "uni_r": uni_r})

    def summarize(key, label):
        vals = [r[key] for r in rows if r[key] is not None]
        if not vals:
            print(f"{label}: no data"); return
        neg = sum(1 for v in vals if v < 0)
        print(f"{label}: n={len(vals)} fortnights | mean excess={st.mean(vals):+.2f}% | "
              f"median={st.median(vals):+.2f}% | underperformed {neg}/{len(vals)} "
              f"({100*neg/len(vals):.0f}%)")

    print(f"=== FPI sector forward-test (entry D+{PUB_LAG_DAYS}d, exit next report; "
          f"excess vs {len(universe)}-name liquid universe) ===")
    print(f"usable fortnights: {len(rows)}\n")
    summarize("hw_excess", "HEADWIND (FPI-outflow sector names) next-fortnight excess return")
    summarize("tw_excess", "TAILWIND (FPI-inflow sector names) next-fortnight excess return")
    print("\nper-fortnight headwind excess:")
    for r in rows:
        hw = f"{r['hw_excess']:+.2f}%" if r['hw_excess'] is not None else "  n/a"
        print(f"  {r['D']}  hw_excess={hw}  (n={r['hw_n']}, univ={r['uni_r']:+.1f}%)")
    conn.close()


if __name__ == "__main__":
    main()
