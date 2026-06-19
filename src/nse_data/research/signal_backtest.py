"""Per-stock explainable backtest of the Buy-Score dynamic strategy. Replays the stock's
factor_snapshot Buy Score + daily closes through the buy-high / sell-on-decline state
machine, recording for every trade: entry & exit dates, prices, holding period, net P&L,
and a plain-English WHY for both the entry (top contributing factors) and the exit (which
rule fired). Powers the cockpit's Signals & Trades tab so a decision can be assessed.
"""
from __future__ import annotations

import datetime as _dt

from . import buy_score as bs

_KEYS = ["quality", "valuation", "momentum", "surprise", "catalyst", "turnaround", "liquidity", "risk"]


def _d(s):
    return _dt.date.fromisoformat(s)


def backtest_symbol(conn, symbol: str, t_in: float = 80.0, t_out: float = 60.0,
                    trail: float = 15.0, max_hold: int = 120, stop: float = -15.0,
                    cost: float = 1.0) -> list[dict]:
    """Buy when Buy Score ≥ t_in; exit on the first of: score < t_out (signal faded),
    score falls `trail` from its in-trade peak, −`stop`% stop-loss, or `max_hold` days."""
    sym = symbol.upper()
    W = bs.REGIME_WEIGHTS["neutral"]
    try:
        rows = conn.execute(
            "SELECT snapshot_date, quality, valuation, momentum, surprise, catalyst, turnaround, "
            "liquidity, risk FROM factor_snapshot WHERE symbol=? ORDER BY snapshot_date", (sym,)).fetchall()
        px = {dd: c for dd, c in conn.execute(
            "SELECT date(ts,'unixepoch','+05:30'), close FROM raw_intraday_candles "
            "WHERE symbol=? AND interval='day' AND close IS NOT NULL", (sym,))}
    except Exception:  # noqa: BLE001 — tables optional (tableless/fresh DB)
        return []
    if not rows:
        return []
    series = []                                              # (date, score, contrib)
    for r in rows:
        sc, contrib = bs.buy_raw(dict(zip(_KEYS, r[1:])), W)
        series.append((r[0], sc, contrib))

    def reason_in(contrib, sc):
        top = sorted(((k, v) for k, v in contrib.items() if k != "risk" and v is not None),
                     key=lambda kv: -kv[1])[:3]
        drivers = ", ".join(f"{k} {v:.0f}" for k, v in top)
        return f"Buy Score {sc:.0f} ≥ {t_in:.0f} — led by {drivers}"

    trades = []
    held = False
    e_px = e_d = e_sc = peak = 0.0
    e_reason = ""
    for d, sc, contrib in series:
        p = px.get(d)
        if p is None:
            continue
        if not held:
            if sc is not None and sc >= t_in:
                held, e_px, e_d, e_sc, peak = True, p, d, sc, sc
                e_reason = reason_in(contrib, sc)
        else:
            if sc is not None:
                peak = max(peak, sc)
            gross = (p / e_px - 1) * 100
            hd = (_d(d) - _d(e_d)).days
            reason = None
            if sc is None:
                reason = "Buy Score no longer computable"
            elif gross <= stop:
                reason = f"Stop-loss hit (down {gross:.0f}%)"
            elif sc < t_out:
                reason = f"Buy Score {sc:.0f} fell below exit {t_out:.0f} (signal faded)"
            elif (peak - sc) >= trail:
                reason = f"Buy Score dropped {peak - sc:.0f} from peak {peak:.0f} (trail {trail:.0f})"
            elif hd >= max_hold:
                reason = f"Max holding period {max_hold}d reached"
            if reason:
                trades.append({
                    "entry_date": e_d, "exit_date": d, "holding_days": hd,
                    "entry_px": round(e_px, 1), "exit_px": round(p, 1),
                    "net_pct": round(gross - cost, 2),
                    "entry_score": round(e_sc, 1),
                    "exit_score": round(sc, 1) if sc is not None else None,
                    "entry_reason": e_reason, "exit_reason": reason})
                held = False
    if held:                                                 # still open at series end
        d, sc, _ = series[-1]
        p = px.get(d)
        if p is not None:
            gross = (p / e_px - 1) * 100
            trades.append({
                "entry_date": e_d, "exit_date": d, "holding_days": (_d(d) - _d(e_d)).days,
                "entry_px": round(e_px, 1), "exit_px": round(p, 1),
                "net_pct": round(gross - cost, 2), "entry_score": round(e_sc, 1),
                "exit_score": round(sc, 1) if sc is not None else None,
                "entry_reason": e_reason, "exit_reason": "still open (marked to last close)",
                "open": True})
    return trades


def summary(trades: list[dict]) -> dict:
    """Aggregate stats for the trade list (closed + open)."""
    if not trades:
        return {"trades": 0}
    nets = [t["net_pct"] for t in trades]
    wins = [x for x in nets if x > 0]
    return {"trades": len(trades), "wins": len(wins),
            "win_rate": round(100.0 * len(wins) / len(trades), 1),
            "avg_net_pct": round(sum(nets) / len(nets), 2),
            "total_net_pct": round(sum(nets), 1),
            "avg_hold_days": round(sum(t["holding_days"] for t in trades) / len(trades))}
