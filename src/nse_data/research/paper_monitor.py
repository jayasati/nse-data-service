"""Paper-book monitor — watch the forward track record fill (PROFITABILITY_PLAN, P4).

One dashboard over `paper_book`: per strategy it shows the current open positions
(mark-to-market unrealized, days held, score, the protective stop after the chandelier
ratchet), the closed-trade expectancy + R9 validation verdict (reused from `paper_report`),
and progress toward the ~100-trade significance threshold the promote/shelve decision needs.

The whole point of P4: the loop runs at 19:15 each session; this is how you check it's
working and whether any strategy is approaching a verdict — without reading SQL. Pure
`monitor_snapshot` (testable) + `format_monitor` (the text dashboard) + a thin CLI.
"""
from __future__ import annotations

import datetime as _dt
import sqlite3

from .paper_report import report

_SIG_TARGET = 100        # closed trades for a trustworthy expectancy at ~50% win [P]


def _d(s: str) -> _dt.date:
    return _dt.date.fromisoformat(s[:10])


def _as_of(conn) -> str | None:
    # query a reference symbol so the (symbol, interval, ts) index is used — scanning all
    # of raw_intraday_candles (tens of millions of rows) is far too slow.
    r = conn.execute(
        "SELECT date(MAX(ts),'unixepoch','+05:30') FROM raw_intraday_candles "
        "WHERE symbol='NIFTYBEES' AND interval='day' AND close IS NOT NULL").fetchone()
    return r[0] if r and r[0] else None


def _price(conn, sym):
    r = conn.execute(
        "SELECT close FROM raw_intraday_candles WHERE symbol=? AND interval='day' "
        "AND close IS NOT NULL ORDER BY ts DESC LIMIT 1", (sym,)).fetchone()
    return r[0] if r else None


def _table_exists(conn, name) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def monitor_snapshot(conn: sqlite3.Connection, *, target: int = _SIG_TARGET) -> dict:
    """Per-strategy open positions + closed expectancy/validation + progress-to-significance."""
    if not _table_exists(conn, "paper_book"):
        return {"as_of": None, "strategies": {}, "totals": {"open": 0, "closed": 0}}
    rep = report(conn)
    as_of = today = _as_of(conn)        # one indexed lookup, reused

    open_rows = conn.execute(
        "SELECT strategy, symbol, entry_date, entry_px, last_score, stop_px, trail_stop, "
        "qty, risk_rupees FROM paper_book WHERE status='open' ORDER BY strategy, entry_date"
    ).fetchall()
    opens_by: dict[str, list] = {}
    for strat, sym, ed, epx, sc, stop, trail, qty, risk in open_rows:
        px = _price(conn, sym)
        unreal = ((px / epx - 1) * 100) if (px and epx) else None
        eff_stop = max([s for s in (stop, trail) if s is not None], default=None)
        open_r = ((px - epx) / (epx - stop)) if (px and epx and stop and epx > stop) else None
        opens_by.setdefault(strat or "?", []).append({
            "symbol": sym, "entry_date": ed,
            "days": (_d(today) - _d(ed)).days if (today and ed) else None,
            "entry_px": epx, "price": px,
            "unrealized_pct": round(unreal, 1) if unreal is not None else None,
            "score": sc, "stop": stop, "trail_stop": trail, "eff_stop": eff_stop,
            "open_r": round(open_r, 2) if open_r is not None else None, "qty": qty})

    out_strats: dict[str, dict] = {}
    for name in sorted(set(rep["strategies"]) | set(opens_by) | set(rep.get("open", {}))):
        closed = rep["strategies"].get(name, {})
        n_closed = closed.get("n", 0)
        strat_opens = opens_by.get(name, [])
        out_strats[name] = {
            "closed": closed, "open": strat_opens, "n_open": len(strat_opens),
            "progress": {"closed": n_closed, "target": target,
                         "pct": round(100 * min(n_closed, target) / target)},
        }
    return {"as_of": as_of, "strategies": out_strats,
            "totals": {"open": len(open_rows),
                       "closed": sum(s["closed"].get("n", 0) for s in out_strats.values())}}


# ---- dashboard -------------------------------------------------------------

def _bar(pct: int, width: int = 20) -> str:
    fill = round(pct / 100 * width)
    return "[" + "█" * fill + "·" * (width - fill) + f"] {pct}%"


def _f(x, suf="%"):
    return "  —  " if x is None else f"{x:+.2f}{suf}"


def format_monitor(snap: dict) -> str:
    strats = snap["strategies"]
    if not strats:
        return ("PAPER-BOOK MONITOR — no positions yet.\n"
                "(The 19:15 paper_trade job opens the first sized/capped positions on the next "
                "trading session; re-run after it has fired.)")
    t = snap["totals"]
    L = ["═" * 70,
         f"PAPER-BOOK MONITOR   as-of {snap['as_of']}   "
         f"open={t['open']}  closed={t['closed']}",
         "═" * 70]
    for name, s in strats.items():
        prog = s["progress"]
        L.append(f"\n▶ {name}   open={s['n_open']}  closed={prog['closed']}   "
                 f"to significance: {_bar(prog['pct'])}")
        c = s["closed"]
        if c.get("n"):
            val = c.get("validation", {})
            L.append(f"    Expectancy {_f(c['expectancy'])}/trade"
                     + (f" · {c['avg_r']:+.2f}R" if c.get("avg_r") is not None else "")
                     + f" · PF {('∞' if c['profit_factor'] is None else round(c['profit_factor'], 2))}"
                     + f" · win {(c['win_rate'] or 0) * 100:.0f}%")
            if val:
                dsr = f"DSR {val['dsr']:.2f}" if val.get("dsr") is not None else "DSR n/a"
                L.append(f"    Verdict: {val['verdict'].upper()}  (Sharpe {val.get('sharpe')} · {dsr})")
        else:
            L.append("    (no closed trades yet — expectancy pending)")
        if s["open"]:
            L.append("    holdings:")
            for o in s["open"][:20]:
                stop_txt = (f"stop {o['eff_stop']}" if o["eff_stop"] is not None else "stop —")
                L.append(f"      {o['symbol']:12s} in {o['entry_date']} "
                         f"({(str(o['days']) + 'd') if o['days'] is not None else '—'}) "
                         f"{_f(o['unrealized_pct'])}"
                         + (f" ({o['open_r']:+.2f}R)" if o["open_r"] is not None else "")
                         + f"  score {o['score']}  {stop_txt}")
    L.append("")
    return "\n".join(L)
