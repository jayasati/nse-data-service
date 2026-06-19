"""Per-stock explainable backtest of the Buy-Score dynamic strategy. Replays the stock's
factor_snapshot Buy Score + daily closes through the buy-high / sell-on-decline state
machine, recording for every trade: entry & exit dates, prices, holding period, net P&L,
and a plain-English WHY for both the entry (top contributing factors) and the exit (which
rule fired). Powers the cockpit's Signals & Trades tab so a decision can be assessed.
"""
from __future__ import annotations

import datetime as _dt

from . import buy_score as bs
from . import macro_engine

_KEYS = ["quality", "valuation", "momentum", "surprise", "catalyst", "turnaround", "liquidity", "risk"]
_IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
_MACRO_CACHE: dict = {}                                      # max_date -> {date: (state, geo)}


def _is_shock(state, geo, geo_floor=30.0):
    """A genuine macro SHOCK worth overriding a position for — not every mild 'Risk Off'.
    Panic, or geopolitical safety collapsed to the extreme bucket (GPR ≳ 200, e.g. a war).
    Exiting on ordinary Risk-Off over-trades and chops winners (verified: +157%→+33%)."""
    return state == "Panic" or (geo is not None and geo <= geo_floor)


def _d(s):
    return _dt.date.fromisoformat(s)


def _eod_ep(date_iso: str) -> int:
    d = _d(date_iso)
    return int(_dt.datetime(d.year, d.month, d.day, 15, 35, tzinfo=_IST).timestamp())


def macro_states(conn) -> dict:
    """{snapshot_date: (macro_state, geopolitical_safety)} for every snapshot date —
    computed once and cached (macro is symbol-independent), so a Risk-Off/Panic tape can
    drive exits across all backtests. Keyed on the latest date so it refreshes on new data."""
    try:
        dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT snapshot_date FROM factor_snapshot ORDER BY snapshot_date")]
    except Exception:  # noqa: BLE001 — table optional (tableless/fresh DB)
        return {}
    if not dates:
        return {}
    if dates[-1] in _MACRO_CACHE:
        return _MACRO_CACHE[dates[-1]]
    out = {}
    for d in dates:
        try:
            m = macro_engine.macro_risk(conn, _eod_ep(d))
            out[d] = (m["state"], m["components"].get("geopolitical"))
        except Exception:  # noqa: BLE001
            out[d] = (None, None)
    _MACRO_CACHE.clear()
    _MACRO_CACHE[dates[-1]] = out
    return out


def backtest_symbol(conn, symbol: str, t_in: float = 80.0, t_out: float = 60.0,
                    trail: float = 15.0, max_hold: int = 120, stop: float = -15.0,
                    cost: float = 1.0, macro_exit: bool = True, lean: bool = True) -> list[dict]:
    """Buy when the score ≥ t_in (and the macro tape isn't Risk-Off); exit on the first of:
    a MACRO SHOCK (tape flips Risk-Off/Panic), score < t_out (faded), score falls `trail`
    from its in-trade peak, −`stop`% stop-loss, or `max_hold` days. `lean=True` (default)
    uses the OOS-validated Lean score (Valuation+Surprise); lean=False = the old Buy Score."""
    sym = symbol.upper()
    W = bs.REGIME_WEIGHTS["neutral"]
    label = "Lean Score" if lean else "Buy Score"
    macro = macro_states(conn) if macro_exit else {}
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
        f = dict(zip(_KEYS, r[1:]))
        sc, contrib = bs.lean_raw(f) if lean else bs.buy_raw(f, W)
        series.append((r[0], sc, contrib))

    def reason_in(contrib, sc):
        top = sorted(((k, v) for k, v in contrib.items() if k != "risk" and v is not None),
                     key=lambda kv: -kv[1])[:3]
        drivers = ", ".join(f"{k} {v:.0f}" for k, v in top)
        return f"{label} {sc:.0f} ≥ {t_in:.0f} — led by {drivers}"

    trades = []
    held = False
    re_armed = False                                         # macro-exited; awaiting re-buy
    e_px = e_d = e_sc = peak = 0.0
    e_reason = ""
    for d, sc, contrib in series:
        p = px.get(d)
        if p is None:
            continue
        macro_st, geo = macro.get(d, (None, None))
        shock = _is_shock(macro_st, geo)
        if not held:
            if shock:
                continue                                      # never buy into an active shock
            # "sell on shock, buy back on recovery": after a macro exit, re-arm and re-enter
            # at the lower hold-bar (t_out) the moment the shock lifts — catch the bounce
            # instead of waiting for the score to re-cross t_in.
            bar = t_out if re_armed else t_in
            if sc is not None and sc >= bar:
                held, e_px, e_d, e_sc, peak = True, p, d, sc, sc
                e_reason = (f"Re-entry after macro pause — Buy Score {sc:.0f} (shock lifted)"
                            if re_armed else reason_in(contrib, sc))
                re_armed = False
        else:
            if sc is not None:
                peak = max(peak, sc)
            gross = (p / e_px - 1) * 100
            hd = (_d(d) - _d(e_d)).days
            reason = None
            arm = False
            if shock:                                         # macro shock overrides thesis
                gtxt = f", geopolitical {geo:.0f}" if geo is not None else ""
                reason = f"Macro shock — tape {macro_st}{gtxt} (sell; re-buy on recovery)"
                arm = True
            elif sc is None:
                reason = f"{label} no longer computable"
            elif gross <= stop:
                reason = f"Stop-loss hit (down {gross:.0f}%)"
            elif sc < t_out:
                reason = f"{label} {sc:.0f} fell below exit {t_out:.0f} (signal faded / re-rated)"
            elif (peak - sc) >= trail:
                reason = f"{label} dropped {peak - sc:.0f} from peak {peak:.0f} (trail {trail:.0f})"
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
                re_armed = arm                                # only a macro exit re-arms a re-buy
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
