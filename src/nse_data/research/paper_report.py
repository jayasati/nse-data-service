"""Expectancy / R-metric reporting over the paper_book forward track record (P4 · plan R1).

Turns raw closed-trade % returns into the numbers that actually decide whether a signal
makes money — expectancy, profit factor, payoff ratio, win rate, max drawdown — per
strategy and per exit_reason. Win% alone cannot separate a positive-expectancy signal
from a coin flip; this is the measurement the promote/shelve decision (P4) needs.

R-multiples: true R (profit ÷ initial risk) needs a per-trade stop/risk, which the sizing
engine (plan R4/B1) will add. Until then expectancy is reported in PERCENT — exactly the
mean net trade return — which is decision-grade on its own. When `paper_book` later carries
an `entry`/`stop` per row, `trade_metrics` can be re-expressed in R with no shape change.

Pure `trade_metrics` / `max_drawdown` are unit-tested; `report`/`format_report` glue them
to SQLite (paper_book, and paper_observations when populated).
"""
from __future__ import annotations

import datetime as _dt
import sqlite3


def _d(s: str) -> _dt.date:
    return _dt.date.fromisoformat(s[:10])


def trade_metrics(returns: list[float]) -> dict:
    """Expectancy stats for a list of per-trade % returns (net of cost).

    avg_loss is the mean of losing trades (a NEGATIVE number); payoff_ratio and
    profit_factor use magnitudes. profit_factor/payoff_ratio are None when there are
    no losses (an undefined ratio — reported as '∞' by the formatter).
    """
    n = len(returns)
    if n == 0:
        return {"n": 0, "win_rate": None, "avg_win": None, "avg_loss": None,
                "payoff_ratio": None, "profit_factor": None, "expectancy": None,
                "total": 0.0, "best": None, "worst": None, "gross_win": 0.0,
                "gross_loss": 0.0, "n_win": 0, "n_loss": 0}
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]      # 0 counts as a non-win
    gross_win = sum(wins)
    gross_loss = -sum(losses)                     # positive magnitude
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0   # negative
    return {
        "n": n,
        "n_win": len(wins),
        "n_loss": len(losses),
        "win_rate": len(wins) / n,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff_ratio": (avg_win / -avg_loss) if avg_loss < 0 else None,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
        "expectancy": sum(returns) / n,           # = mean net trade return (%)
        "total": sum(returns),
        "best": max(returns),
        "worst": min(returns),
        "gross_win": gross_win,
        "gross_loss": gross_loss,
    }


def max_drawdown(returns_in_order: list[float]) -> float:
    """Peak-to-trough drawdown (%) of the compounded equity curve, trades in time order."""
    eq = peak = 1.0
    mdd = 0.0
    for r in returns_in_order:
        eq *= (1 + r / 100.0)
        peak = max(peak, eq)
        if peak > 0:
            mdd = max(mdd, (peak - eq) / peak)
    return mdd * 100.0


def _table_exists(conn, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _strategy_block(rows: list[tuple]) -> dict:
    """rows: (exit_reason, net_pct, entry_date, exit_date, r_multiple) for ONE strategy,
    exit-ordered."""
    rets = [r[1] for r in rows]
    m = trade_metrics(rets)
    m["max_drawdown"] = round(max_drawdown(rets), 2) if rets else 0.0
    holds = [(_d(r[3]) - _d(r[2])).days for r in rows if r[2] and r[3]]
    m["avg_hold_days"] = round(sum(holds) / len(holds), 1) if holds else None
    rs = [r[4] for r in rows if r[4] is not None]         # R-multiples (sized trades only)
    m["avg_r"] = round(sum(rs) / len(rs), 3) if rs else None
    m["n_with_r"] = len(rs)
    # by exit reason — tells you whether stops/trails are helping or bleeding
    by_reason: dict[str, list[float]] = {}
    for reason, net, _e, _x, _r in rows:
        by_reason.setdefault(reason or "?", []).append(net)
    m["by_reason"] = {
        k: {"n": len(v), "avg": round(sum(v) / len(v), 2),
            "win_rate": round(sum(1 for x in v if x > 0) / len(v), 2)}
        for k, v in sorted(by_reason.items())
    }
    return m


def report(conn: sqlite3.Connection) -> dict:
    """Per-strategy expectancy report over closed paper_book trades, + optional
    catalyst/grade buckets from paper_observations when that table is populated."""
    out: dict = {"strategies": {}, "open": {}, "observations": None}
    if not _table_exists(conn, "paper_book"):
        return out

    rows = conn.execute(
        "SELECT strategy, exit_reason, net_pct, entry_date, exit_date, r_multiple "
        "FROM paper_book WHERE status='closed' AND net_pct IS NOT NULL ORDER BY exit_date"
    ).fetchall()
    by_strat: dict[str, list[tuple]] = {}
    for strat, reason, net, ed, xd, r in rows:
        by_strat.setdefault(strat or "?", []).append((reason, net, ed, xd, r))
    for strat, srows in by_strat.items():
        out["strategies"][strat] = _strategy_block(srows)

    for strat, n in conn.execute(
        "SELECT strategy, COUNT(*) FROM paper_book WHERE status='open' GROUP BY strategy"
    ).fetchall():
        out["open"][strat or "?"] = n

    # paper_observations: catalyst-segmented forward returns (net of cost), if labelled
    if _table_exists(conn, "paper_observations"):
        obs = conn.execute(
            "SELECT catalyst, net_5d, net_10d FROM paper_observations "
            "WHERE net_5d IS NOT NULL"
        ).fetchall()
        if obs:
            buckets: dict[str, list[tuple]] = {}
            for cat, n5, n10 in obs:
                buckets.setdefault(cat or "none", []).append((n5, n10))
            out["observations"] = {
                cat: {"n": len(v),
                      "net_5d": trade_metrics([x[0] for x in v]),
                      "net_10d": trade_metrics([x[1] for x in v if x[1] is not None])}
                for cat, v in sorted(buckets.items())
            }
    return out


# ---- formatting ------------------------------------------------------------

def _f(x, suffix="%", nd=2):
    return "  —  " if x is None else f"{x:+.{nd}f}{suffix}"


def _ratio(x):
    return "  ∞ " if x is None else f"{x:.2f}"


def format_report(rep: dict) -> str:
    lines: list[str] = []
    strats = rep["strategies"]
    if not strats:
        return "paper_book has no closed trades yet — nothing to score.\n" \
               "(The 19:15 paper_trade job accumulates the track record; re-run after exits close.)"
    lines.append("=" * 72)
    lines.append("PAPER-BOOK EXPECTANCY REPORT  (closed trades, net of cost)")
    lines.append("=" * 72)
    for strat, m in strats.items():
        opn = rep["open"].get(strat, 0)
        lines.append(f"\n▶ {strat}    closed={m['n']}  open={opn}  "
                     f"avg_hold={m['avg_hold_days']}d")
        lines.append(f"    Expectancy : {_f(m['expectancy'])} / trade      "
                     f"Total: {_f(m['total'])}")
        if m.get("avg_r") is not None:
            lines.append(f"    Expectancy : {m['avg_r']:+.2f}R / trade     "
                         f"(sized trades: {m['n_with_r']})")
        lines.append(f"    Win rate   : {(m['win_rate'] or 0)*100:5.1f}%  "
                     f"({m['n_win']}W / {m['n_loss']}L)")
        lines.append(f"    Avg win    : {_f(m['avg_win'])}     Avg loss: {_f(m['avg_loss'])}")
        lines.append(f"    Payoff     : {_ratio(m['payoff_ratio'])}x      "
                     f"Profit factor: {_ratio(m['profit_factor'])}")
        lines.append(f"    Best/Worst : {_f(m['best'])} / {_f(m['worst'])}     "
                     f"Max DD: {m['max_drawdown']:.2f}%")
        verdict = ("POSITIVE expectancy" if (m["expectancy"] or 0) > 0 else "NEGATIVE expectancy")
        warn = "" if m["n"] >= 30 else "  ⚠ < 30 trades — not yet significant"
        lines.append(f"    → {verdict}{warn}")
        if m["by_reason"]:
            lines.append("    by exit reason:")
            for k, r in m["by_reason"].items():
                lines.append(f"      {k:10s} n={r['n']:<4d} avg={r['avg']:+.2f}%  "
                             f"win={r['win_rate']*100:.0f}%")
    obs = rep.get("observations")
    if obs:
        lines.append("\n" + "-" * 72)
        lines.append("paper_observations — forward net return by catalyst (net_5d)")
        lines.append("-" * 72)
        for cat, b in obs.items():
            m5 = b["net_5d"]
            lines.append(f"  {cat:14s} n={b['n']:<4d} exp={_f(m5['expectancy'])}  "
                         f"win={(m5['win_rate'] or 0)*100:.0f}%  PF={_ratio(m5['profit_factor'])}")
    lines.append("")
    return "\n".join(lines)
