"""The formal 9-layer confidence engine (FEATURE_CHECKLIST Week 25, tasks 25.1–25.4).

`signals/confidence.py` grew ~28 ad-hoc contributions wired in over the phases. This is the
auditable rewrite: each layer is a single bounded pure function, and `compute_confidence`
returns the final score AND the per-layer breakdown so any number can be explained.

    base 0.50
    L1 market regime        −0.15 … +0.15   (incl. fragile-rally / internal-weakness flags)
    L2 sector alignment     −0.10 … +0.10
    L3 fundamental quality  −0.15 … +0.10
    L4 technical alignment  −0.15 … +0.15
    L5 signal-specific      0     … +0.10    (per signal type, 25.2)
    L6 confluence bonus     0     … +0.10    (multiple independent signals, 25.3)
    L7 psychological        −0.20 … +0.15    (psych_state × direction, 19.4)
    L8 institutional        −0.08 … +0.10    (smart_money_daily score, 22.7)
    L9 event proximity      −0.15 … +0.08    (buy-rumor / IV blow-off / spike-and-fade, 25.4)
    final = clip(base + ΣL, 0, 1)

Layer-0 hard gates stay in `detect.py` (binary kills); the engine assumes they've passed.
This feeds the gated dispatcher (G12) like the existing scorer — it's the structure + the
plug-in points for the new institutional (W22) and options (W23) layers, not a live change.
"""
from __future__ import annotations

BASE = 0.50


def _clip(x: float, lo: float, hi: float) -> float:
    return round(max(lo, min(hi, x)), 3)


def layer1_regime(regime: str | None, *, divergence: bool = False) -> float:
    base = {"risk_on": 0.12, "neutral": 0.0, "risk_off": -0.12, "panic": -0.15}.get(regime or "", 0.0)
    if divergence:                                   # fragile_rally / internal_weakness (25.5)
        base -= 0.05
    return _clip(base, -0.15, 0.15)


def layer2_sector(rank: int | None, n: int | None, trend: str | None) -> float:
    c = 0.0
    if rank and n:
        if rank <= 3:
            c += 0.08
        elif rank >= n - 2:
            c -= 0.08
    if trend == "improving":
        c += 0.03
    elif trend == "deteriorating":
        c -= 0.03
    return _clip(c, -0.10, 0.10)


def layer3_fundamental(quality: float | None) -> float:
    if quality is None:
        return 0.0
    if quality > 70:
        return 0.10
    if quality >= 50:
        return 0.05
    if quality < 30:
        return -0.15
    return 0.0


def layer4_technical(price_vs_vwap: str | None, vwap_slope: float | None,
                     rsi: float | None, trend_regime: str | None) -> float:
    c = 0.0
    if price_vs_vwap == "above" and (vwap_slope or 0) > 0:
        c += 0.10
    elif price_vs_vwap == "below":
        c -= 0.10
    if rsi is not None:
        if 50 <= rsi <= 65:
            c += 0.05
        elif rsi > 75:
            c -= 0.10
    if trend_regime in ("strong_uptrend", "uptrend"):
        c += 0.05
    elif trend_regime in ("downtrend", "strong_downtrend"):
        c -= 0.05
    return _clip(c, -0.15, 0.15)


def layer5_signal_quality(signal_type: str, m: dict) -> float:
    """Per-signal-type quality (task 25.2). 0 … +0.10."""
    c = 0.0
    if signal_type == "long_buildup":
        oi = m.get("oi_change_pct") or 0
        c += 0.08 if oi >= 10 else 0.05 if oi >= 5 else 0.0
        if (m.get("price_change_pct") or 0) >= 2:
            c += 0.03
        if (m.get("volume_ratio") or 0) >= 3:
            c += 0.05
    elif signal_type == "breakout_52wh":
        if not m.get("fake_breakout_risk"):
            c += 0.05
        if (m.get("volume_ratio") or 0) >= 2:
            c += 0.05
    elif signal_type == "credit_downgrade_junk":
        c += 0.10
    elif signal_type in ("result_beat", "result_quality_high"):
        c += 0.08 if m.get("earnings_quality") == "HIGH" else 0.02
    return _clip(c, 0.0, 0.10)


def layer6_confluence(confluent_signals: int) -> float:
    """Confluence bonus (task 25.3): independent signals on the same name within the window."""
    if confluent_signals >= 3:
        return 0.10
    if confluent_signals == 2:
        return 0.06
    return 0.0


_PSYCH = {
    ("NEUTRAL_TRENDING", "long"): 0.05, ("FOMO_EUPHORIA", "long"): -0.20,
    ("BUY_RUMOR", "long"): -0.10, ("CAPITULATION", "long"): 0.15,
    ("SELL_NEWS", "short"): 0.15, ("RELIEF_BOUNCE", "long"): 0.10,
    ("DEAD_CAT_BOUNCE", "long"): -0.15, ("FEAR_BUILDING", "long"): -0.08,
}


def layer7_psych(psych_state: str | None, direction: str) -> float:
    return _clip(_PSYCH.get((psych_state or "", direction), 0.0), -0.20, 0.15)


def layer8_institutional(smart_money: float | None) -> float:
    """Smart-money composite (task 22.7). > 0.70 accumulation, < 0.40 distribution."""
    if smart_money is None:
        return 0.0
    if smart_money > 0.70:
        return 0.10
    if smart_money < 0.40:
        return -0.08
    return 0.0


def layer9_event(pre_event_run_10d: float | None, days_to_event: int | None,
                 iv_vs_avg: float | None, spike_and_fade: bool, direction: str) -> float:
    c = 0.0
    if (pre_event_run_10d is not None and pre_event_run_10d > 8
            and days_to_event is not None and days_to_event <= 5):
        c -= 0.12                                    # exhausted run into the event
    if iv_vs_avg is not None and iv_vs_avg > 2.0:
        c -= 0.08                                    # uncertainty fully priced
    if spike_and_fade and direction == "short":
        c += 0.10
    return _clip(c, -0.15, 0.08)


def compute_confidence(ctx: dict, *, signal_type: str, direction: str = "long") -> dict:
    """Run all 9 layers over `ctx`; return final + the per-layer breakdown (auditable)."""
    layers = {
        "L1_regime": layer1_regime(ctx.get("regime"), divergence=bool(ctx.get("divergence"))),
        "L2_sector": layer2_sector(ctx.get("sector_rank"), ctx.get("sector_n"), ctx.get("sector_trend")),
        "L3_fundamental": layer3_fundamental(ctx.get("quality_score")),
        "L4_technical": layer4_technical(ctx.get("price_vs_vwap"), ctx.get("vwap_slope"),
                                         ctx.get("rsi_5m"), ctx.get("trend_regime")),
        "L5_signal": layer5_signal_quality(signal_type, ctx),
        "L6_confluence": layer6_confluence(ctx.get("confluent_signals", 0)),
        "L7_psych": layer7_psych(ctx.get("psych_state"), direction),
        "L8_institutional": layer8_institutional(ctx.get("smart_money")),
        "L9_event": layer9_event(ctx.get("pre_event_run_10d"), ctx.get("days_to_event"),
                                 ctx.get("iv_vs_avg"), bool(ctx.get("spike_and_fade")), direction),
    }
    final = _clip(BASE + sum(layers.values()), 0.0, 1.0)
    return {"final": final, "base": BASE, "layers": layers}
