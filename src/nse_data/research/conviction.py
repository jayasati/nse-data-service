"""NSE Intraday High-Conviction Synthesis Engine (plans/high_conviction_prompt.md).

Synthesis ONLY — every score traces to a collected field; missing inputs become DATA_GAP, are
excluded from the weighted composite (weights renormalise over present stages), and cap the tier.
Nothing is invented. Reuses the collectors/engines built across the desk: global_markets (macro),
options_metrics (flow), participant_oi/smart_money (positioning), news_daily (catalyst), delivery
+ candles (volume/structure), daily_sweep primitives (SMC).
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pandas_ta_classic as ta

from ..indicators.delivery_tracker import compute_symbol_delivery
from ..strategy.daily_sweep.fvg import detect_fvgs
from ..strategy.daily_sweep.sweep import detect_sweeps

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
WEIGHTS = {"catalyst": 0.25, "positioning": 0.20, "options": 0.15, "structure": 0.15,
           "volume": 0.10, "rel_strength": 0.10, "vol_expansion": 0.05}
GAP = None   # sentinel: a stage returning GAP score is DATA_GAP


# ---- STAGE 1: macro regime --------------------------------------------------
def macro_regime(conn) -> dict:
    g = {t: (last, pct) for t, last, pct in conn.execute(
        "SELECT ticker, last, pct_change FROM raw_global_markets "
        "WHERE as_of=(SELECT MAX(as_of) FROM raw_global_markets)")}
    if not g:
        return {"status": "DATA_GAP", "note": "global macro collector not active. Regime withheld."}
    gn = conn.execute("SELECT pct_change FROM raw_gift_nifty ORDER BY as_of DESC LIMIT 1").fetchone()
    iv = conn.execute("SELECT vix, vix_pct_change FROM raw_india_vix ORDER BY as_of DESC "
                      "LIMIT 1").fetchone()
    spx = g.get("^GSPC"); vix = g.get("^VIX"); nifty = g.get("^NSEI")
    gift_gap = gn[0] if gn else None
    risk_on = bool(spx and spx[1] is not None and spx[1] > 0 and vix and vix[1] is not None
                   and vix[1] < 0)
    return {"status": "ok", "nifty_last": nifty[0] if nifty else None,
            "nifty_pct": nifty[1] if nifty else None, "us_spx_pct": spx[1] if spx else None,
            "us_vix": vix[0] if vix else None, "us_vix_pct": vix[1] if vix else None,
            "india_vix": iv[0] if iv else None, "india_vix_pct": iv[1] if iv else None,
            "gift_gap_pct": gift_gap,
            "regime": "RISK-ON" if risk_on else "MIXED/RISK-OFF",
            "gap_bias": ("GAP-UP" if (gift_gap or 0) > 0.2 else "GAP-DOWN"
                         if (gift_gap or 0) < -0.2 else "FLAT")}


def _daily(conn, sym, n=60):
    rows = conn.execute("SELECT ts,open,high,low,close,volume FROM raw_intraday_candles WHERE "
                        "symbol=? AND interval='day' ORDER BY ts DESC LIMIT ?", (sym, n)).fetchall()[::-1]
    if len(rows) < 25:
        return None
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])


# ---- per-stock stages (each → (score 0-10 | GAP, source dict)) --------------
def stage_catalyst(conn, sym) -> tuple:
    r = conn.execute("SELECT news_score, news_risk, top_pos, top_neg FROM news_daily WHERE symbol=? "
                     "ORDER BY as_of_date DESC LIMIT 1", (sym,)).fetchone()
    if not r:
        return GAP, {"src": "news_daily=null", "note": "no announcement/news data for symbol"}
    score, risk, tp, tn = r
    # positive flow lifts, negative-news risk pulls down; centre 5
    s = max(0.0, min(10.0, (score - 50) / 10.0 + 5 - (100 - risk) / 12.0))
    return round(s, 1), {"src": "news_daily", "news_score": score, "news_risk": risk,
                         "top_pos": tp, "top_neg": tn}


def stage_options(conn, sym) -> tuple:
    r = conn.execute("SELECT spot,max_pain,pcr,call_wall,put_wall,gex_sign FROM options_metrics "
                     "WHERE symbol=? ORDER BY as_of DESC LIMIT 1", (sym,)).fetchone()
    if not r:
        return GAP, {"src": "options_metrics=null", "note": "not in F&O options set"}
    spot, mp, pcr, cw, pw, gex = r
    mp_dir = (mp - spot) / spot * 100 if (mp and spot) else 0.0   # drift toward max-pain
    # bullish: max-pain above spot + balanced/high PCR; bearish: below + low PCR
    s = 5 + min(2.5, max(-2.5, mp_dir)) * 0.8 + ((pcr or 1) - 1) * 2.0
    return round(max(0.0, min(10.0, s)), 1), {"src": "options_metrics", "spot": spot,
            "max_pain": mp, "pcr": pcr, "call_wall": cw, "put_wall": pw, "gex": gex,
            "max_pain_drift_pct": round(mp_dir, 2)}


def stage_positioning(conn, sym, sm_score) -> tuple:
    # per-stock FII futures not in participant_oi (index-level only) → use the index smart-money lean
    if sm_score is None:
        return GAP, {"src": "smart_money=null"}
    return round(sm_score * 10, 1), {"src": "smart_money_daily (index-level FII deriv+cash)",
            "smart_money_score": sm_score, "note": "per-stock FII futures = DATA_GAP (index proxy)"}


def stage_volume(conn, sym) -> tuple:
    df = _daily(conn, sym)
    if df is None:
        return GAP, {"src": "candles=null"}
    rvol = df["volume"].iloc[-1] / df["volume"].iloc[-21:-1].mean() if df["volume"].iloc[-21:-1].mean() else 1
    sd = conn.execute("SELECT MAX(date) FROM raw_bhavcopy_cm WHERE symbol=?", (sym,)).fetchone()[0]
    dlv = compute_symbol_delivery(conn, sym, sd) if sd else None
    conv = (dlv or {}).get("delivery_conviction_score")
    s = min(10.0, rvol * 3.0 + (conv or 0.5) * 4.0)
    return round(s, 1), {"src": "ohlcv.rvol + delivery", "rvol": round(float(rvol), 2),
            "delivery_conviction": conv, "delivery_trend": (dlv or {}).get("delivery_trend")}


def stage_rel_strength(conn, sym, nifty_pct) -> tuple:
    df = _daily(conn, sym)
    if df is None:
        return GAP, {"src": "candles=null"}
    ret5 = (df["close"].iloc[-1] - df["close"].iloc[-6]) / df["close"].iloc[-6] * 100
    rel = ret5 - (nifty_pct or 0)
    return round(max(0.0, min(10.0, 5 + rel * 0.5)), 1), {"src": "stock 5d vs nifty",
            "stock_5d_pct": round(float(ret5), 2), "nifty_pct": nifty_pct,
            "rel_strength": round(float(rel), 2)}


def stage_vol_expansion(conn, sym) -> tuple:
    df = _daily(conn, sym)
    if df is None:
        return GAP, {"src": "candles=null"}
    bb = ta.bbands(df["close"], 20)
    if bb is None or bb.iloc[-1].isna().any():
        return GAP, {"src": "bbands=null"}
    width = (bb.iloc[-1, 2] - bb.iloc[-1, 0]) / bb.iloc[-1, 1] * 100
    hist = ((bb.iloc[:, 2] - bb.iloc[:, 0]) / bb.iloc[:, 1] * 100).dropna()
    pctile = (hist < width).mean() * 100
    atr_pct = ta.atr(df["high"], df["low"], df["close"], 14).iloc[-1] / df["close"].iloc[-1] * 100
    # coiled spring = LOW bandwidth percentile → high score (expansion pending). IV pctile = GAP.
    s = 10 - pctile / 10.0
    return round(max(0.0, min(10.0, s)), 1), {"src": "bollinger_bandwidth_pctile + atr_pct",
            "bb_width_pctile": round(float(pctile), 0), "atr_pct": round(float(atr_pct), 2),
            "iv_pctile": "DATA_GAP"}


def stage_structure(conn, sym) -> tuple:
    df = _daily(conn, sym)
    if df is None:
        return GAP, {"src": "candles=null"}
    df = df.set_index(pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(IST))
    close = df["close"].iloc[-1]
    fv = detect_fvgs(df)
    sw = detect_sweeps(df, swing_k=3)
    last_fvg = fv[fv["fvg_dir"].notna()].tail(1)
    last_sweep = sw[sw["sweep_dir"].notna()].tail(1)
    hi20, lo20 = df["high"].iloc[-20:].max(), df["low"].iloc[-20:].min()
    pos = (close - lo20) / (hi20 - lo20) * 100 if hi20 > lo20 else 50
    # near support (low pos) with a recent bullish sweep = high; near resistance = low
    s = 5 + (50 - pos) / 20.0
    detail = {"src": "bars_daily SMC", "range_pos_pct": round(float(pos), 0),
              "20d_high": round(float(hi20), 1), "20d_low": round(float(lo20), 1)}
    if not last_fvg.empty:
        detail["last_fvg"] = f"{last_fvg['fvg_dir'].iloc[0]} {last_fvg['gap_low'].iloc[0]:.1f}-{last_fvg['gap_high'].iloc[0]:.1f}"
    if not last_sweep.empty:
        detail["last_sweep"] = f"{last_sweep['sweep_dir'].iloc[0]} of {last_sweep['swept_level'].iloc[0]:.1f}"
    return round(max(0.0, min(10.0, s)), 1), detail


# ---- STAGE 10: composite (DATA_GAP renormalised) + STAGE 13: tier -----------
def composite(scores: dict) -> tuple:
    num = den = 0.0
    used = {}
    for stage, w in WEIGHTS.items():
        v = scores.get(stage, (GAP, {}))[0]
        if v is GAP:
            continue
        num += w * v; den += w; used[stage] = round(w, 3)
    if den == 0:
        return None, {}
    renorm = {k: round(v / den, 3) for k, v in used.items()}
    return round(num / den, 2), renorm


def tier_of(comp, scores) -> str:
    gaps_critical = any(scores.get(s, (GAP,))[0] is GAP for s in ("catalyst", "positioning", "options"))
    cat = scores.get("catalyst", (GAP,))[0]
    pos = scores.get("positioning", (GAP,))[0]
    if comp is None:
        return "DATA_GAP"
    if (not gaps_critical and cat is not GAP and cat >= 7 and pos is not GAP and pos >= 7
            and comp >= 7.5):
        return "A+"
    if gaps_critical and comp >= 7.0:
        return "B (capped — DATA_GAP in catalyst/positioning/options)"
    return "A" if comp >= 6.5 else "B" if comp >= 5.0 else "C"


def score_stock(conn, sym, *, sm_score, nifty_pct) -> dict:
    scores = {
        "catalyst": stage_catalyst(conn, sym),
        "positioning": stage_positioning(conn, sym, sm_score),
        "options": stage_options(conn, sym),
        "structure": stage_structure(conn, sym),
        "volume": stage_volume(conn, sym),
        "rel_strength": stage_rel_strength(conn, sym, nifty_pct),
        "vol_expansion": stage_vol_expansion(conn, sym),
    }
    comp, renorm = composite(scores)
    gaps = [s for s, (v, _) in scores.items() if v is GAP]
    return {"symbol": sym, "composite": comp, "tier": tier_of(comp, scores),
            "renorm_weights": renorm, "data_gaps": gaps,
            "stages": {s: {"score": ("DATA_GAP" if v is GAP else v), **d} for s, (v, d) in scores.items()}}


def run_conviction(conn, symbols=None) -> dict:
    macro = macro_regime(conn)
    nifty_pct = macro.get("nifty_pct") if macro.get("status") == "ok" else None
    sm = conn.execute("SELECT score FROM smart_money_daily ORDER BY as_of_date DESC LIMIT 1").fetchone()
    sm_score = sm[0] if sm else None
    if symbols is None:
        symbols = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM options_metrics "
                   "WHERE symbol NOT IN ('NIFTY','BANKNIFTY','FINNIFTY')")]
    scored = [score_stock(conn, s, sm_score=sm_score, nifty_pct=nifty_pct) for s in symbols]
    scored = [x for x in scored if x["composite"] is not None]
    scored.sort(key=lambda x: -x["composite"])
    return {"macro": macro, "smart_money_score": sm_score, "n": len(scored), "ranked": scored}
