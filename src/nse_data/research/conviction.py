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
# Catalyst (news) backtested NON-predictive (IC ≈ 0), so it carries ZERO positive weight — it is a
# RISK-VETO only (bad news penalises the composite + caps the tier; good news never lifts it).
WEIGHTS = {"options": 0.25, "structure": 0.22, "positioning": 0.22, "volume": 0.15,
           "rel_strength": 0.11, "vol_expansion": 0.05}

_SECTOR_IDX = {"it": "^CNXIT", "bfsi": "^NSEBANK", "nbfc": "^CNXFIN", "financial": "^CNXFIN",
               "pharma": "^CNXPHARMA", "auto": "^CNXAUTO", "fmcg": "^CNXFMCG",
               "metals": "^CNXMETAL", "energy": "^CNXENERGY", "realty": "^CNXREALTY",
               "capgoods": "^CNXINFRA", "infra": "^CNXINFRA", "media": "^CNXMEDIA"}


def news_veto(stages) -> tuple[float, str | None]:
    """Catalyst as a risk-veto: low news_risk (governance/insolvency/downgrade/pledge/penalty)
    penalises the composite and can cap the tier. Good news does NOT lift the score (IC ≈ 0)."""
    cat = stages.get("catalyst", {})
    risk = cat.get("news_risk")
    if not isinstance(risk, (int, float)):
        return 0.0, None
    if risk < 50:
        return 2.0, f"NEWS-VETO ({cat.get('top_neg')})"
    if risk < 70:
        return round((70 - risk) / 15.0, 1), f"news-caution ({cat.get('top_neg')})"
    return 0.0, None


def _sector_pct(conn, sym):
    """The stock's sector index day-change (IT/bank/fin only — the indices we collect)."""
    sec = conn.execute("SELECT sector FROM factor_snapshot WHERE symbol=? ORDER BY snapshot_date "
                       "DESC LIMIT 1", (sym,)).fetchone()
    if not sec or sec[0] not in _SECTOR_IDX:
        return None, None
    r = conn.execute("SELECT pct_change FROM raw_global_markets WHERE ticker=? ORDER BY as_of "
                     "DESC LIMIT 1", (_SECTOR_IDX[sec[0]],)).fetchone()
    return sec[0], (r[0] if r else None)


def _pre_open(conn, sym):
    """NSE pre-open indicative open (IEP) + gap% for this symbol (populated ~09:08). Returns
    (iep, gap_pct) only if read in the last ~14h, else (None, None) — the real per-stock open."""
    import time as _t
    r = conn.execute("SELECT iep, pct_change, as_of FROM raw_pre_open WHERE symbol=? ORDER BY "
                     "as_of DESC LIMIT 1", (sym,)).fetchone()
    if not r or not r[0] or (_t.time() - r[2]) / 3600.0 > 14:
        return None, None
    return r[0], r[1]


GAP = None   # sentinel: a stage returning GAP score is DATA_GAP


# ---- STAGE 1: macro regime --------------------------------------------------
def macro_regime(conn) -> dict:
    g = {t: (last, pct) for t, last, pct in conn.execute(
        "SELECT ticker, last, pct_change FROM raw_global_markets "
        "WHERE as_of=(SELECT MAX(as_of) FROM raw_global_markets)")}
    if not g:
        return {"status": "DATA_GAP", "note": "global macro collector not active. Regime withheld."}
    import time as _t
    gn = conn.execute("SELECT pct_change, as_of FROM raw_gift_nifty ORDER BY as_of DESC "
                      "LIMIT 1").fetchone()
    iv = conn.execute("SELECT vix, vix_pct_change FROM raw_india_vix ORDER BY as_of DESC "
                      "LIMIT 1").fetchone()
    spx = g.get("^GSPC"); vix = g.get("^VIX"); nifty = g.get("^NSEI")
    # FRESHNESS GUARD: only trust GIFT for the gap call if it was read in the last ~14h (the
    # pre-market window). A stale (prior-session) reading is a DATA_GAP, not a confident gap call.
    gift_gap, gift_stale = (None, False)
    if gn:
        if (_t.time() - gn[1]) / 3600.0 <= 14:
            gift_gap = gn[0]
        else:
            gift_stale = True
    # PRE-OPEN IEP market gap (median of the NSE pre-open indicative-open gaps, ~09:08) — the REAL
    # market gap, not the GIFT index proxy. Prefer it when fresh.
    po_as_of = conn.execute("SELECT MAX(as_of) FROM raw_pre_open").fetchone()[0]
    preopen_gap = None
    if po_as_of and (_t.time() - po_as_of) / 3600.0 <= 14:
        vals = sorted(r[0] for r in conn.execute("SELECT pct_change FROM raw_pre_open WHERE "
                      "as_of=? AND pct_change IS NOT NULL", (po_as_of,)))
        if vals:
            preopen_gap = round(vals[len(vals) // 2], 2)
    gap_val = preopen_gap if preopen_gap is not None else gift_gap
    gap_src = ("pre-open IEP" if preopen_gap is not None else "GIFT" if gift_gap is not None
               else "DATA_GAP")
    # a real, tradeable index gap is ~0.4%+; below that the open is FLAT (noise, not a gap).
    gap_bias = ("DATA_GAP (no fresh pre-open/GIFT read)" if gap_val is None else
                "GAP-UP" if gap_val > 0.4 else "GAP-DOWN" if gap_val < -0.4 else "FLAT")
    risk_on = bool(spx and spx[1] is not None and spx[1] > 0 and vix and vix[1] is not None
                   and vix[1] < 0)
    return {"status": "ok", "nifty_last": nifty[0] if nifty else None,
            "nifty_pct": nifty[1] if nifty else None, "us_spx_pct": spx[1] if spx else None,
            "us_vix": vix[0] if vix else None, "us_vix_pct": vix[1] if vix else None,
            "india_vix": iv[0] if iv else None, "india_vix_pct": iv[1] if iv else None,
            "gift_gap_pct": gift_gap, "gift_stale": gift_stale, "preopen_gap_pct": preopen_gap,
            "gap_source": gap_src,
            "regime": "RISK-ON" if risk_on else "MIXED/RISK-OFF", "gap_bias": gap_bias}


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
    # NSE doesn't publish per-stock participant OI — only aggregate FII stock-futures + index-level
    # smart-money. Both are MARKET-WIDE proxies (same for every symbol); true per-stock = DATA_GAP.
    fii = conn.execute("SELECT fut_stk_long, fut_stk_short FROM raw_participant_oi WHERE "
                       "client_type='FII' ORDER BY report_date DESC LIMIT 1").fetchone()
    stk_lean = fii[0] / (fii[0] + fii[1]) if (fii and (fii[0] + fii[1])) else None
    parts = [p for p in (sm_score, stk_lean) if p is not None]
    if not parts:
        return GAP, {"src": "smart_money + participant_oi = null"}
    return round(sum(parts) / len(parts) * 10, 1), {
        "src": "smart_money + FII-stk-fut(aggregate)", "smart_money_score": sm_score,
        "fii_stk_long_frac": round(stk_lean, 3) if stk_lean is not None else None,
        "note": "market-wide proxy; per-stock FII = DATA_GAP (NSE doesn't publish per-symbol)"}


def stage_volume(conn, sym) -> tuple:
    df = _daily(conn, sym)
    if df is None:
        return GAP, {"src": "candles=null"}
    rvol = df["volume"].iloc[-1] / df["volume"].iloc[-21:-1].mean() if df["volume"].iloc[-21:-1].mean() else 1
    sd = conn.execute("SELECT MAX(date) FROM raw_bhavcopy_cm WHERE symbol=?", (sym,)).fetchone()[0]
    dlv = compute_symbol_delivery(conn, sym, sd) if sd else None
    conv = (dlv or {}).get("delivery_conviction_score")
    cv = conv if conv is not None else 0.5
    # delivery conviction is the core (real money). RVOL amplifies its SIGN — high RVOL on LOW
    # delivery is distribution/churn (penalise), not accumulation. (Fixes RVOL-only over-reward.)
    s = cv * 10 + (cv - 0.5) * (min(rvol, 4) - 1) * 2 if rvol > 1.5 else cv * 10
    return round(max(0.0, min(10.0, s)), 1), {"src": "delivery-conviction × rvol",
            "rvol": round(float(rvol), 2), "delivery_conviction": conv,
            "delivery_trend": (dlv or {}).get("delivery_trend")}


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
    # IV percentile from the iv_daily history (Stage 9) — needs ~30+ days to be meaningful.
    ivh = [r[0] for r in conn.execute("SELECT atm_iv FROM iv_daily WHERE symbol=? AND atm_iv "
           "IS NOT NULL ORDER BY as_of_date DESC LIMIT 90", (sym,))]
    iv_pctile = round(sum(1 for x in ivh if x < ivh[0]) / len(ivh) * 100, 0) if len(ivh) >= 30 \
        else f"DATA_GAP (accumulating n={len(ivh)})"
    # coiled spring = LOW bandwidth pctile (and low IV pctile when available) → high score.
    pcts = [pctile] + ([iv_pctile] if isinstance(iv_pctile, (int, float)) else [])
    s = 10 - (sum(pcts) / len(pcts)) / 10.0
    return round(max(0.0, min(10.0, s)), 1), {"src": "bollinger_bandwidth_pctile + atr_pct + iv",
            "bb_width_pctile": round(float(pctile), 0), "atr_pct": round(float(atr_pct), 2),
            "iv_pctile": iv_pctile}


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
    # near support (low pos) = higher base; but the SMC DIRECTION it detected matters — a bearish
    # FVG/sweep is a warning that cuts the score (not ignored as before).
    s = 5 + (50 - pos) / 20.0
    detail = {"src": "bars_daily SMC", "range_pos_pct": round(float(pos), 0),
              "20d_high": round(float(hi20), 1), "20d_low": round(float(lo20), 1)}
    if not last_sweep.empty:
        sd = last_sweep["sweep_dir"].iloc[0]
        s += 1.0 if sd == "bull" else -1.5
        detail["last_sweep"] = f"{sd} of {last_sweep['swept_level'].iloc[0]:.1f}"
    if not last_fvg.empty:
        fd = last_fvg["fvg_dir"].iloc[0]
        s += 0.5 if fd == "bull" else -1.0
        detail["last_fvg"] = f"{fd} {last_fvg['gap_low'].iloc[0]:.1f}-{last_fvg['gap_high'].iloc[0]:.1f}"
    sector, sec_pct = _sector_pct(conn, sym)
    if sec_pct is not None and sec_pct < -1.5:           # weak sector = structural headwind
        s -= 1.5
        detail["sector_weak"] = f"{sector} {sec_pct}%"
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


def tier_of(comp, scores, veto_flag=None) -> str:
    if comp is None:
        return "DATA_GAP"
    if veto_flag and "NEWS-VETO" in veto_flag:
        return "C (news-veto)"
    gaps_critical = any(scores.get(s, (GAP,))[0] is GAP for s in ("positioning", "options"))
    pos = scores.get("positioning", (GAP,))[0]
    opt = scores.get("options", (GAP,))[0]
    # A+ now needs strong positioning + options + composite + clean news (no veto) — not a
    # high catalyst score (catalyst is veto-only).
    if (not gaps_critical and pos is not GAP and pos >= 7 and opt is not GAP and opt >= 7
            and comp >= 7.5 and not veto_flag):
        return "A+"
    if gaps_critical and comp >= 7.0:
        return "B (capped — DATA_GAP in positioning/options)"
    return "A" if comp >= 6.5 else "B" if comp >= 5.0 else "C"


def trade_plan(close, atr, stages, macro, iep=None, stock_gap=None) -> dict:
    """STAGE 11/12 — direction + entry/stop/targets from liquidity levels (options walls, 20d H/L)
    + ATR, plus the pre-market setup bucket. All levels trace to Stage 3/5/6 fields, not round
    numbers. When the NSE pre-open IEP is present, the setup uses the stock's OWN gap (real, not the
    market proxy) and the indicative open is reported. Probability capped (edges are modest)."""
    if not close or not atr:
        return {"direction": "DATA_GAP", "note": "no candle/atr"}
    opt = stages.get("options", {})
    st = stages.get("structure", {})
    mp_drift = opt.get("max_pain_drift_pct", 0) or 0
    cw, pw, mp = opt.get("call_wall"), opt.get("put_wall"), opt.get("max_pain")
    pos = st.get("range_pos_pct", 50)
    hi20, lo20 = st.get("20d_high"), st.get("20d_low")
    rs = stages.get("rel_strength", {}).get("rel_strength", 0) or 0
    bull = (mp_drift > 0) + (rs > 0) + (pos < 55)
    bear = (mp_drift < 0) + (rs < 0) + (pos > 75)
    direction = "LONG" if bull > bear else "SHORT" if bear > bull else ("LONG" if rs >= 0 else "SHORT")
    # consistent ATR-based risk (1.3·ATR) → sane R:R; walls/max-pain/20d levels used as targets
    # ONLY when they sit the correct side of entry (else fall back to ATR multiples).
    entry = round(close, 1)
    if direction == "LONG":
        stop = round(close - 1.3 * atr, 1)
        t1 = round(close + 1.5 * atr, 1)
        t2 = round(cw, 1) if (cw and cw > close + 0.5 * atr) else round(close + 2.5 * atr, 1)
        t3 = round(max(hi20 or 0, close + 3.5 * atr), 1)
    else:
        stop = round(close + 1.3 * atr, 1)
        t1 = round(close - 1.5 * atr, 1)
        t2 = round(mp, 1) if (mp and mp < close - 0.5 * atr) else round(close - 2.5 * atr, 1)
        t3 = round(min(lo20 or 1e12, close - 3.5 * atr), 1)
    risk = abs(entry - stop)
    rr = round(abs(t2 - entry) / risk, 1) if risk else None
    # setup bucket (Stage 12) — prefer the stock's OWN pre-open gap (real) over the market proxy.
    # A real per-stock gap is ~0.7%+; smaller opens are FLAT and get a non-gap (trend) setup.
    if stock_gap is not None:
        is_up, is_down = stock_gap > 0.7, stock_gap < -0.7
    else:
        gb = (macro or {}).get("gap_bias", "")
        is_up, is_down = "GAP-UP" in gb, "GAP-DOWN" in gb
    if direction == "SHORT" and is_up and pos > 70:
        setup = "Gap-up fade"
    elif direction == "LONG" and is_down and pos < 40:
        setup = "Gap-down reversal"
    elif direction == "LONG" and pos > 80:
        setup = "Breakout continuation"
    elif direction == "SHORT" and pos < 25:
        setup = "Breakdown continuation"
    elif direction == "LONG":
        setup = "Pullback / VWAP-reclaim long"
    else:
        setup = "Resistance fade short"
    return {"direction": direction, "entry": entry, "stop": stop, "t1": t1, "t2": t2, "t3": t3,
            "rr": rr, "setup": setup, "expected_move_pct": round(atr / close * 100, 1),
            "open_iep": round(iep, 1) if iep else None, "gap_pct": stock_gap,
            "basis": f"levels: call_wall {cw} / put_wall {pw} / max_pain {mp} / 20dH-L {hi20}-{lo20}, ATR ₹{round(atr,1)}"}


def score_stock(conn, sym, *, sm_score, nifty_pct, macro=None) -> dict:
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
    stages = {s: {"score": ("DATA_GAP" if v is GAP else v), **d} for s, (v, d) in scores.items()}
    veto_pen, veto_flag = news_veto(stages)          # catalyst = risk-veto, not a positive factor
    if comp is not None:
        comp = round(max(0.0, comp - veto_pen), 2)
    df = _daily(conn, sym)
    close = float(df["close"].iloc[-1]) if df is not None else None
    atr = float(ta.atr(df["high"], df["low"], df["close"], 14).iloc[-1]) if df is not None else None
    iep, stock_gap = _pre_open(conn, sym)        # real per-stock indicative open + gap (~09:08)
    plan = trade_plan(close, atr, stages, macro, iep=iep, stock_gap=stock_gap)
    prob = round(min(68, 45 + (comp or 5) * 2.6)) if comp else None
    return {"symbol": sym, "composite": comp, "tier": tier_of(comp, scores, veto_flag),
            "renorm_weights": renorm, "data_gaps": gaps, "trade": plan, "probability_pct": prob,
            "news_flag": veto_flag, "stages": stages}


def run_conviction(conn, symbols=None) -> dict:
    macro = macro_regime(conn)
    nifty_pct = macro.get("nifty_pct") if macro.get("status") == "ok" else None
    sm = conn.execute("SELECT score FROM smart_money_daily ORDER BY as_of_date DESC LIMIT 1").fetchone()
    sm_score = sm[0] if sm else None
    if symbols is None:
        symbols = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM options_metrics "
                   "WHERE symbol NOT IN ('NIFTY','BANKNIFTY','FINNIFTY')")]
    scored = [score_stock(conn, s, sm_score=sm_score, nifty_pct=nifty_pct, macro=macro)
              for s in symbols]
    scored = [x for x in scored if x["composite"] is not None]
    scored.sort(key=lambda x: -x["composite"])
    return {"macro": macro, "smart_money_score": sm_score, "n": len(scored), "ranked": scored}


# ---- persistence + pre-market job ------------------------------------------
def run_and_persist(conn) -> dict:
    import json
    import time
    r = run_conviction(conn)
    today = dt.datetime.now(IST).date().isoformat()
    now = int(time.time())
    conn.execute("INSERT OR REPLACE INTO conviction_macro (as_of_date, macro_json, smart_money, "
                 "updated_at) VALUES (?,?,?,?)",
                 (today, json.dumps(r["macro"]), r["smart_money_score"], now))
    g = lambda x, s: (None if x["stages"][s]["score"] == "DATA_GAP" else x["stages"][s]["score"])
    t = lambda x, k: x.get("trade", {}).get(k)
    conn.executemany(
        "INSERT OR REPLACE INTO conviction_daily (as_of_date, symbol, composite, tier, catalyst, "
        "positioning, options, structure, volume, rel_strength, vol_expansion, data_gaps, "
        "stages_json, direction, entry, stop, t1, t2, t3, rr, setup, probability, open_iep, "
        "gap_pct, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(today, x["symbol"], x["composite"], x["tier"], g(x, "catalyst"), g(x, "positioning"),
          g(x, "options"), g(x, "structure"), g(x, "volume"), g(x, "rel_strength"),
          g(x, "vol_expansion"), ",".join(x["data_gaps"]), json.dumps(x["stages"]),
          t(x, "direction"), t(x, "entry"), t(x, "stop"), t(x, "t1"), t(x, "t2"), t(x, "t3"),
          t(x, "rr"), t(x, "setup"), x.get("probability_pct"), t(x, "open_iep"), t(x, "gap_pct"), now)
         for x in r["ranked"]])
    conn.commit()
    return {"date": today, "persisted": len(r["ranked"])}


def register_conviction_job(scheduler, db_path: str) -> str:
    """09:10 IST (post pre-open auction) — run the engine and persist to conviction_daily."""
    import structlog
    from apscheduler.triggers.cron import CronTrigger

    from ..scheduler import market_hours
    from ..storage.db import open_db
    log = structlog.get_logger()

    def _tick():
        conn = open_db(db_path)
        try:
            log.info("conviction", **run_and_persist(conn))
        except Exception:
            log.exception("conviction_failed")
        finally:
            conn.close()

    # 09:10 IST — just after the NSE pre-open auction (09:08) so the gap/entry use the real
    # per-stock indicative open (IEP) + final GIFT, ~5 min before the 09:15 open.
    scheduler.add_job(_tick, trigger=CronTrigger(hour=9, minute=10, timezone=market_hours.IST),
                      id="conviction", max_instances=1, coalesce=True, replace_existing=True)
    return "conviction"
