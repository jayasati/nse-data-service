"""Intraday conviction board — a real-time momentum/breakout SCANNER, ranked on the live intraday
signal stack already computed every minute in `indicator_live` (VWAP · ORB · 5M-supertrend · RVOL ·
RSI · structure), plus the dealer-gamma regime.

HONEST FRAMING — this is NOT a validated edge. Intraday round-trips have repeatedly tested
net-NEGATIVE after costs on this universe (ORB+VWAP, 11,648 trades, PF 0.67 — see the intraday-no-edge
verdict). This board is a DISCRETIONARY tool: it floats the cleanest aligned-momentum setups to the
top so a human can act selectively (e.g. with options leverage on the strongest ones). It makes no
claim of a backtested edge, and it is deliberately separate from the swing /conviction board (which is
a 1–5 day positioning tool that, by design, won't confidently catch a one-day momentum pop).
"""
from __future__ import annotations

_COLS = ("symbol", "updated_at", "ltp", "vwap", "price_vs_vwap", "vwap_slope", "orb_break",
         "orb_high", "orb_low", "rvol_5m", "structure_5m", "supertrend_5m_dir", "rsi_5m",
         "atr_14_daily", "adx")


def _st_dir(v):
    s = str(v)
    return 1 if s in ("1", "1.0", "up") else -1 if s in ("-1", "-1.0", "down") else 0


def _int0(v):
    try:
        return int(float(v)) if v not in (None, "") else 0
    except (ValueError, TypeError):
        return 0


def _dir_score(r) -> tuple:
    """Net directional alignment across the live intraday stack. VWAP (intraday control) + ORB
    (opening-range break) are the heavy signals; supertrend/structure/RSI/vwap-slope confirm."""
    above = r.get("price_vs_vwap") == "above"
    below = r.get("price_vs_vwap") == "below"
    orb = r.get("orb_break") or 0
    st = _st_dir(r.get("supertrend_5m_dir"))
    struct = _int0(r.get("structure_5m"))
    rsi = r.get("rsi_5m")
    slope = r.get("vwap_slope") or 0
    bull = (1.5 * above + 1.5 * (orb == 1) + 1.0 * (st == 1) + 0.5 * (struct == 1)
            + 0.5 * (slope > 0) + 0.5 * (rsi is not None and rsi > 55))
    bear = (1.5 * below + 1.5 * (orb == -1) + 1.0 * (st == -1) + 0.5 * (struct == -1)
            + 0.5 * (slope < 0) + 0.5 * (rsi is not None and rsi < 45))
    det = {"vwap": r.get("price_vs_vwap"), "orb": orb, "st_5m": st, "struct_5m": struct,
           "rsi_5m": round(rsi) if rsi is not None else None,
           "vwap_slope_up": (slope or 0) > 0}
    return bull - bear, det


def run_intraday_conviction(conn, limit: int = 30) -> list:
    if not conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='indicator_live'"
                        ).fetchone():
        return []
    rows = conn.execute(
        f"SELECT {', '.join(_COLS)} FROM indicator_live WHERE ltp IS NOT NULL AND vwap IS NOT NULL"
    ).fetchall()
    gamma = {r[0]: r[1] for r in conn.execute(
        "SELECT symbol, gex_sign FROM options_metrics WHERE as_of=(SELECT MAX(as_of) FROM "
        "options_metrics o2 WHERE o2.symbol=options_metrics.symbol)")}
    out = []
    for row in rows:
        r = dict(zip(_COLS, row))
        net, det = _dir_score(r)
        direction = "LONG" if net > 0.75 else "SHORT" if net < -0.75 else None
        if direction is None:
            continue                                     # only show names with a clear intraday lean
        rvol = r.get("rvol_5m") or 1.0
        # conviction = alignment strength + a participation BONUS (rvol>1 adds, low rvol doesn't
        # punish — the move may already have happened). gamma-amplifying = momentum-friendly bonus.
        conv = 5 + abs(net) * 0.9 + min(1.0, max(0.0, rvol - 1) * 0.5)
        if gamma.get(r["symbol"]) == "negative":         # amplifying regime → trends extend
            conv += 0.3
        conv = round(min(10.0, conv), 2)
        ltp = r["ltp"]
        atr = r.get("atr_14_daily") or (ltp * 0.02)
        sign = 1 if direction == "LONG" else -1
        entry = round(ltp, 1)
        vs_vwap = round((ltp - r["vwap"]) / r["vwap"] * 100, 2) if r["vwap"] else None
        out.append({
            "symbol": r["symbol"], "direction": direction, "conviction": conv,
            "ltp": ltp, "vs_vwap_pct": vs_vwap, "rvol": round(float(rvol), 2),
            "gamma": "amplifying" if gamma.get(r["symbol"]) == "negative"
                     else "pinning" if gamma.get(r["symbol"]) == "positive" else None,
            "orb_high": r.get("orb_high"), "orb_low": r.get("orb_low"),
            "entry": entry, "stop": round(entry - sign * 0.6 * atr, 1),
            "t1": round(entry + sign * 0.6 * atr, 1), "t2": round(entry + sign * 1.2 * atr, 1),
            "t3": round(entry + sign * 1.8 * atr, 1), "net": round(net, 2), **det})
    out.sort(key=lambda x: -x["conviction"])
    return out[:limit]
