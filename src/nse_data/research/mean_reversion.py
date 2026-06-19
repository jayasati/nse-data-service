"""Mean-reversion timing overlay — the opposite philosophy to the momentum Buy Score.
Buy a name when it's OVERSOLD (RSI low and stretched below its 50-DMA) *and* its
fundamental Quality is still decent (buy quality cheap); sell on the bounce to OVERBOUGHT
(RSI high), or on a stop / max-hold. This is what catches swing tops/bottoms the
momentum score structurally misses (the score peaks at tops). All point-in-time from
daily candles + factor_snapshot.quality. Cost-aware — every trade is net of `cost`%.
"""
from __future__ import annotations

import datetime as _dt


def _d(s):
    return _dt.date.fromisoformat(s)


def rsi(closes: list[float], period: int = 14) -> list:
    """Wilder-smoothed RSI aligned to `closes` (None until enough history)."""
    out = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):                           # seed average gain/loss
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0.0)
        losses += max(-ch, 0.0)
    ag, al = gains / period, losses / period
    out[period] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(period + 1, len(closes)):                 # Wilder smoothing
        ch = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(ch, 0.0)) / period
        al = (al * (period - 1) + max(-ch, 0.0)) / period
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def _sma(closes: list[float], n: int) -> list:
    out = [None] * len(closes)
    s = 0.0
    for i, c in enumerate(closes):
        s += c
        if i >= n:
            s -= closes[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def backtest_mr(conn, symbol: str, rsi_buy: float = 35.0, rsi_sell: float = 68.0,
                dma_below: float = 0.0, quality_min: float = 50.0, stop: float = -12.0,
                max_hold: int = 60, cost: float = 1.0) -> list[dict]:
    """Buy when RSI ≤ rsi_buy AND price ≤ (1+dma_below) × 50-DMA AND Quality ≥ quality_min
    (oversold quality). Exit on RSI ≥ rsi_sell (bounce), −stop% stop, or max_hold days."""
    sym = symbol.upper()
    try:
        rows = sorted(conn.execute(
            "SELECT date(ts,'unixepoch','+05:30'), close FROM raw_intraday_candles "
            "WHERE symbol=? AND interval='day' AND close IS NOT NULL", (sym,)).fetchall())
        qrows = conn.execute(
            "SELECT snapshot_date, quality FROM factor_snapshot WHERE symbol=? AND quality IS NOT NULL "
            "ORDER BY snapshot_date", (sym,)).fetchall()
    except Exception:  # noqa: BLE001 — tables optional
        return []
    if len(rows) < 60:
        return []
    dates = [r[0] for r in rows]
    closes = [r[1] for r in rows]
    rs = rsi(closes)
    dma = _sma(closes, 50)
    qdates = [q[0] for q in qrows]
    qvals = [q[1] for q in qrows]

    def quality_asof(d):                                     # latest Quality on/before d
        lo, hi, best = 0, len(qdates) - 1, None
        while lo <= hi:
            m = (lo + hi) // 2
            if qdates[m] <= d:
                best = qvals[m]
                lo = m + 1
            else:
                hi = m - 1
        return best

    trades = []
    held = False
    e_px = e_d = e_rsi = 0.0
    e_reason = ""
    for i, d in enumerate(dates):
        p, r, m = closes[i], rs[i], dma[i]
        if r is None or m is None:
            continue
        stretch = (p / m - 1) * 100                          # % vs 50-DMA
        if not held:
            q = quality_asof(d)
            if r <= rsi_buy and p <= m * (1 + dma_below / 100.0) and q is not None and q >= quality_min:
                held, e_px, e_d, e_rsi = True, p, d, r
                e_reason = (f"Oversold quality — RSI {r:.0f}, {stretch:+.0f}% vs 50-DMA, "
                            f"Quality {q:.0f}")
        else:
            gross = (p / e_px - 1) * 100
            hd = (_d(d) - _d(e_d)).days
            reason = None
            if r >= rsi_sell:
                reason = f"Bounce to overbought — RSI {r:.0f} (took the mean-reversion)"
            elif gross <= stop:
                reason = f"Stop-loss (down {gross:.0f}%)"
            elif hd >= max_hold:
                reason = f"Max holding {max_hold}d (no bounce)"
            if reason:
                trades.append({"entry_date": e_d, "exit_date": d, "holding_days": hd,
                               "entry_px": round(e_px, 1), "exit_px": round(p, 1),
                               "net_pct": round(gross - cost, 2), "entry_rsi": round(e_rsi),
                               "exit_rsi": round(r), "entry_reason": e_reason, "exit_reason": reason})
                held = False
    return trades


def summary(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0}
    nets = [t["net_pct"] for t in trades]
    wins = [x for x in nets if x > 0]
    return {"trades": len(trades), "win_rate": round(100 * len(wins) / len(trades), 1),
            "avg_net_pct": round(sum(nets) / len(nets), 2), "total_net_pct": round(sum(nets), 1),
            "avg_hold_days": round(sum(t["holding_days"] for t in trades) / len(trades))}
