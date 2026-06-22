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
from ..indicators.intraday_ohlcv import read_intraday_5m
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


def participant_divergence(conn) -> dict:
    """FII (smart money) vs Client (retail) index-futures positioning — NSE's structural edge.

    Disaggregates participant_oi by client_type (FII/DII/Client/Pro), not the FII monolith. The
    signal is the DIVERGENCE: FII more long than retail = institutions accumulating vs retail
    distributing (bullish); FII short while retail long = retail chasing / institutions hedged
    (bearish fade). Returns a −1..+1 directional `signal` for wiring into positioning + macro.
    """
    d = conn.execute("SELECT MAX(report_date) FROM raw_participant_oi").fetchone()[0]
    if not d:
        return {"status": "DATA_GAP"}
    leans = {}
    for ct, lng, sht in conn.execute("SELECT client_type, fut_idx_long, fut_idx_short FROM "
                                     "raw_participant_oi WHERE report_date=?", (d,)):
        if (lng or 0) + (sht or 0):
            leans[ct] = lng / (lng + sht)            # long fraction 0–1
    fii, client = leans.get("FII"), leans.get("Client")
    if fii is None or client is None:
        return {"status": "DATA_GAP", "note": "FII/Client index-fut OI missing"}
    div = fii - client                                # >0 FII more long than retail, <0 retail chasing
    read = ("INSTITUTIONAL ACCUMULATION (FII > retail) — bullish" if div > 0.15 else
            "RETAIL CHASING (retail long, FII short) — bearish fade" if div < -0.15 else
            "aligned (no divergence)")
    return {"status": "ok", "date": d, "fii_long_pct": round(fii * 100),
            "client_long_pct": round(client * 100),
            "dii_long_pct": round(leans["DII"] * 100) if "DII" in leans else None,
            "pro_long_pct": round(leans["Pro"] * 100) if "Pro" in leans else None,
            "divergence": round(div, 2), "read": read,
            "signal": max(-1.0, min(1.0, div * 2))}   # −1..+1 directional


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


def _trend_dir(df) -> int:
    """+1 up / −1 down / 0 range — EMA20 vs EMA50 with price confirmation. Works on any timeframe."""
    if df is None or len(df) < 50:
        return 0
    c = df["close"].astype(float)
    e20, e50 = ta.ema(c, 20), ta.ema(c, 50)
    if e20 is None or e50 is None or pd.isna(e20.iloc[-1]) or pd.isna(e50.iloc[-1]):
        return 0
    a, b, last = float(e20.iloc[-1]), float(e50.iloc[-1]), float(c.iloc[-1])
    if a > b and last > a:
        return 1
    if a < b and last < a:
        return -1
    return 0


def _recent_5m_bos(m5, k=3, lookback=78) -> int:
    """Most recent 5M Break-of-Structure direction over ~one session (+1 bull / −1 bear / 0 none).

    BOS = latest close beyond the most recent confirmed swing (pivot) high/low — the SMC entry
    trigger. A pivot high/low is a bar higher/lower than the k bars on each side.
    """
    d = m5.tail(lookback)
    if len(d) < 2 * k + 6:
        return 0
    h = d["high"].to_numpy(dtype=float); l = d["low"].to_numpy(dtype=float)
    c = float(d["close"].iloc[-1])
    sh = [h[i] for i in range(k, len(d) - k) if h[i] == max(h[i - k:i + k + 1])]
    sl = [l[i] for i in range(k, len(d) - k) if l[i] == min(l[i - k:i + k + 1])]
    bull = bool(sh) and c > sh[-1]      # closed above the last swing high → bullish BOS
    bear = bool(sl) and c < sl[-1]      # closed below the last swing low → bearish BOS
    return 1 if bull and not bear else -1 if bear and not bull else 0


def _opening_drive(m5, prior_close, atr):
    """Gap-adjusted OPENING-DRIVE — a SCORED 5M trigger for the first ~45 min, where the swing-BOS is
    blind (no pivots have formed yet). The opening 30–45 min is where the largest moves initiate, so
    that window must not be a dead zone:
      • gap > 0.5·ATR and the first 2 bars HOLD beyond the gap-fill (prior close) → BOS-equivalent
        (+1 gap-up held / −1 gap-down held)
      • a gap that FILLS back through prior close → failed gap = reversal (opposite sign)
      • no significant gap → opening-range (first 3 bars) break (+1 high / −1 low)
    Returns (signal ∈ {−1,0,1}, note) for bars 2–9 of the session; None outside the window / pre-open."""
    if not prior_close or not atr or atr <= 0:
        return None
    today = m5.index[-1].date()
    td = m5[m5.index.date == today]
    nb = len(td)
    if nb < 2 or nb > 9:                              # opening window only (≈09:25–10:00 IST)
        return None
    op = float(td["open"].iloc[0]); price = float(td["close"].iloc[-1])
    gap_atr = (op - prior_close) / atr
    last2 = td.tail(2)["close"].astype(float)
    if gap_atr > 0.5:                                # GAP UP
        if (last2 > prior_close).all() and price > prior_close:
            return 1, f"gap-up {gap_atr:.1f}ATR held above fill → bullish drive"
        if price < prior_close:
            return -1, f"gap-up {gap_atr:.1f}ATR FILLED → failed-gap fade"
        return 0, f"gap-up {gap_atr:.1f}ATR testing fill"
    if gap_atr < -0.5:                               # GAP DOWN
        if (last2 < prior_close).all() and price < prior_close:
            return -1, f"gap-down {abs(gap_atr):.1f}ATR held below fill → bearish drive"
        if price > prior_close:
            return 1, f"gap-down {abs(gap_atr):.1f}ATR RECLAIMED → failed-breakdown reclaim"
        return 0, f"gap-down {abs(gap_atr):.1f}ATR testing fill"
    if nb < 4:
        return 0, "opening (range forming)"          # OR = first 3 bars; need a later bar to break it
    orr = td.head(3); orh = float(orr["high"].max()); orl = float(orr["low"].min())
    if price > orh:
        return 1, "opening-range high break"
    if price < orl:
        return -1, "opening-range low break"
    return 0, "inside opening range"


def _mtf_trends(conn, sym, prior_close=None, atr=None):
    """1H trend + 5M trigger from the live-merged bars. The 5M trigger is the gap-adjusted OPENING-
    DRIVE in the first ~45 min (swing-BOS is blind then), else the swing Break-of-Structure.
    Returns (h1, m5_trigger, note) ∈ ({−1,0,1}, {−1,0,1}, str|None)."""
    try:
        m5 = read_intraday_5m(conn, sym)
    except Exception:
        return None, None, None
    if m5 is None or m5.empty:
        return None, None, None
    m5 = m5.rename(columns=str.lower)
    m5.index = pd.to_datetime(m5.index, unit="s", utc=True).tz_convert(IST)
    h1 = m5.resample("1h").agg({"open": "first", "high": "max", "low": "min",
                                "close": "last", "volume": "sum"}).dropna()
    drive = _opening_drive(m5, prior_close, atr)
    if drive is not None:
        m5_trig, note = drive                        # opening window: scored opening-drive
    else:
        m5_trig, note = _recent_5m_bos(m5), "swing-BOS"   # mid-session: swing break-of-structure
    return _trend_dir(h1), m5_trig, note


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


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return None if not n else xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _liquid_otm_ivs(conn, sym):
    """Liquid, sane OTM IVs from the chain → (spot, otm_puts, otm_calls) as [(moneyness, iv), …].
    Filters to OI≥100 and IV in [5, 70] — NSE reports garbage IV for ITM/illiquid strikes (deep puts
    at 100% vol, near-ATM calls solving to 9%). OTM only: puts below spot, calls above."""
    a = conn.execute("SELECT MAX(as_of) FROM raw_option_chain WHERE symbol=?", (sym,)).fetchone()[0]
    if not a:
        return None
    e = conn.execute("SELECT expiry FROM raw_option_chain WHERE symbol=? AND as_of=? ORDER BY "
                     "expiry LIMIT 1", (sym, a)).fetchone()
    if not e:
        return None
    rows = conn.execute("SELECT strike, option_type, implied_volatility, open_interest, "
                        "underlying_value FROM raw_option_chain WHERE symbol=? AND as_of=? AND "
                        "expiry=?", (sym, a, e[0])).fetchall()
    spot = max((r[4] for r in rows if r[4]), default=0)
    if not spot:
        return None
    puts, calls = [], []
    for strike, ot, iv, oi, _ in rows:
        if not iv or iv < 5 or iv > 70 or (oi or 0) < 100:
            continue
        m = abs(strike - spot) / spot
        if ot == "PE" and strike < spot:
            puts.append((m, iv))
        elif ot == "CE" and strike > spot:
            calls.append((m, iv))
    return spot, puts, calls


def _robust_atm_iv(conn, sym):
    """ATM IV = mean of the near-money OTM put-median and call-median. Returns None when the two
    sides DIVERGE > 12 vol-points — a put-call-parity violation that means the chain IV is garbage
    (e.g. BAJAJ-AUTO's near-ATM call solving to 9% while puts price 49%)."""
    r = _liquid_otm_ivs(conn, sym)
    if not r:
        return None
    _, puts, calls = r
    pm = _median([iv for m, iv in puts if 0.005 < m < 0.06])
    cm = _median([iv for m, iv in calls if 0.005 < m < 0.06])
    if pm is None or cm is None or abs(pm - cm) > 12:    # inconsistent → unreliable, don't use
        return None
    return round((pm + cm) / 2, 1)


def _put_skew(conn, sym):
    """Put skew = median OTM-put IV − median OTM-call IV (≈3–8% OTM band, 25-delta proxy). Median of
    a band is robust to single-strike garbage. Returns None when |skew| > 6 — a real equity skew is
    1–4 vol-points; a larger gap is a put-call-parity violation (bad NSE chain data), not signal."""
    r = _liquid_otm_ivs(conn, sym)
    if not r:
        return None
    _, puts, calls = r
    pb = [iv for m, iv in puts if 0.03 < m < 0.08]
    cb = [iv for m, iv in calls if 0.03 < m < 0.08]
    if len(pb) < 2 or len(cb) < 2:
        return None
    skew = _median(pb) - _median(cb)
    return round(skew, 2) if abs(skew) <= 6 else None    # |skew|>6 = bad data, not a real signal


def _iv_term_structure(conn, sym):
    """ATM IV term structure: slope = far-expiry IV − near-expiry IV. Contango (slope>0, far richer)
    = calm; BACKWARDATION (slope<0, near richer) = near-term event/stress priced in. None (DATA_GAP)
    until the chain collects ≥2 expiries (n_expiries≥2)."""
    a = conn.execute("SELECT MAX(as_of) FROM raw_option_chain WHERE symbol=?", (sym,)).fetchone()[0]
    if not a:
        return None
    exps = [r[0] for r in conn.execute("SELECT DISTINCT expiry FROM raw_option_chain WHERE "
            "symbol=? AND as_of=? ORDER BY expiry", (sym, a))]
    if len(exps) < 2:
        return None
    def atm(expiry):
        rows = conn.execute("SELECT strike, implied_volatility, underlying_value FROM "
            "raw_option_chain WHERE symbol=? AND as_of=? AND expiry=? AND option_type='CE' AND "
            "implied_volatility>0", (sym, a, expiry)).fetchall()
        spot = max((r[2] for r in rows if r[2]), default=0)
        return min(rows, key=lambda r: abs(r[0] - spot))[1] if (spot and rows) else None
    near, far = atm(exps[0]), atm(exps[1])
    if near is None or far is None:
        return None
    slope = round(far - near, 2)
    return {"near_iv": round(near, 1), "far_iv": round(far, 1), "slope": slope,
            "regime": "contango (calm)" if slope > 0.5 else
                      "BACKWARDATION (near-term event/stress)" if slope < -0.5 else "flat"}


def stage_options(conn, sym) -> tuple:
    r = conn.execute("SELECT spot,max_pain,pcr,call_wall,put_wall,gex_sign FROM options_metrics "
                     "WHERE symbol=? ORDER BY as_of DESC LIMIT 1", (sym,)).fetchone()
    if not r:
        return GAP, {"src": "options_metrics=null", "note": "not in F&O options set"}
    spot, mp, pcr, cw, pw, gex = r
    mp_dir = (mp - spot) / spot * 100 if (mp and spot) else 0.0   # drift toward max-pain
    # bullish: max-pain above spot + balanced/high PCR; bearish: below + low PCR
    s = 5 + min(2.5, max(-2.5, mp_dir)) * 0.8 + ((pcr or 1) - 1) * 2.0
    # IV SKEW (directional) — puts-richer = protective-bid/institutions-long (bullish), calls-richer
    # = call demand / short-hedging (bearish). A modest tilt; NEW factor, pending forward validation.
    skew = _put_skew(conn, sym)
    if skew is not None:
        s += max(-1.0, min(1.0, skew / 3.0))
    return round(max(0.0, min(10.0, s)), 1), {"src": "options_metrics + IV skew", "spot": spot,
            "max_pain": mp, "pcr": pcr, "call_wall": cw, "put_wall": pw, "gex": gex,
            "max_pain_drift_pct": round(mp_dir, 2), "put_skew": skew}


def stage_positioning(conn, sym, sm_score, divergence=None) -> tuple:
    # NSE doesn't publish per-stock participant OI — only aggregate FII stock-futures + the
    # index-level FII-vs-RETAIL divergence (the real institutional read). Both are MARKET-WIDE.
    fii = conn.execute("SELECT fut_stk_long, fut_stk_short FROM raw_participant_oi WHERE "
                       "client_type='FII' ORDER BY report_date DESC LIMIT 1").fetchone()
    stk_lean = fii[0] / (fii[0] + fii[1]) if (fii and (fii[0] + fii[1])) else None
    parts = [p for p in (sm_score, stk_lean) if p is not None]
    detail = {"src": "smart_money + FII-stk-fut + FII-vs-retail divergence",
              "smart_money_score": sm_score,
              "fii_stk_long_frac": round(stk_lean, 3) if stk_lean is not None else None}
    # FII-vs-Client divergence (institutional vs retail) — NSE's structural edge. Maps the −1..+1
    # signal to a 0–1 bullishness and blends it in: FII>retail lifts positioning, retail-chasing cuts.
    if divergence and divergence.get("status") == "ok":
        parts.append(0.5 + divergence["signal"] * 0.5)
        detail["fii_long_pct"] = divergence["fii_long_pct"]
        detail["client_long_pct"] = divergence["client_long_pct"]
        detail["divergence_read"] = divergence["read"]
        detail["divergence_signal"] = round(divergence["signal"], 2)   # −1..+1 for confluence
    if not parts:
        return GAP, {"src": "smart_money + participant_oi = null"}
    detail["note"] = "market-wide (per-stock FII = DATA_GAP); divergence = real institutional-vs-retail read"
    return round(sum(parts) / len(parts) * 10, 1), detail


def _tod_rvol(conn, sym):
    """Time-of-day-normalized RVOL: today's cumulative volume so far ÷ the 20-day average cumulative
    volume up to the SAME time of day. Corrects the intraday U-shape (a 2× at 09:20 means something
    very different from 2× at 14:30) — usable from the first bars, not a lagged full-day measure.
    None pre-open / when no today bars exist."""
    try:
        m5 = read_intraday_5m(conn, sym)
    except Exception:
        return None
    if m5 is None or m5.empty:
        return None
    m5 = m5.rename(columns=str.lower)
    m5.index = pd.to_datetime(m5.index, unit="s", utc=True).tz_convert(IST)
    today = m5.index[-1].date()
    td = m5[m5.index.date == today]
    if td.empty:
        return None
    cutoff = td.index[-1].time()                       # current time-of-day
    today_cum = float(td["volume"].sum())
    hist = m5[m5.index.date < today]
    days = sorted(set(hist.index.date))[-20:]
    cums = [float(hist[(hist.index.date == d) & (hist.index.time <= cutoff)]["volume"].sum())
            for d in days]
    cums = [x for x in cums if x > 0]
    if not cums:
        return None
    avg = sum(cums) / len(cums)
    return round(today_cum / avg, 2) if avg else None


def stage_volume(conn, sym) -> tuple:
    df = _daily(conn, sym)
    if df is None:
        return GAP, {"src": "candles=null"}
    daily_rvol = (df["volume"].iloc[-1] / df["volume"].iloc[-21:-1].mean()
                  if df["volume"].iloc[-21:-1].mean() else 1)
    # TIME-OF-DAY normalised RVOL (today's pace vs the 20d avg at the SAME time) when the session is
    # live — corrects the intraday U-shape and is usable from the open. Falls back to daily pre-open.
    tod = _tod_rvol(conn, sym)
    rvol = tod if tod is not None else daily_rvol
    rvol_src = "time-of-day-normalised" if tod is not None else "daily 20d"
    sd = conn.execute("SELECT MAX(date) FROM raw_bhavcopy_cm WHERE symbol=?", (sym,)).fetchone()[0]
    dlv = compute_symbol_delivery(conn, sym, sd) if sd else None
    conv = (dlv or {}).get("delivery_conviction_score")
    cv = conv if conv is not None else 0.5
    # delivery conviction is the core (real money). RVOL amplifies its SIGN — high RVOL on LOW
    # delivery is distribution/churn (penalise), not accumulation. (Fixes RVOL-only over-reward.)
    s = cv * 10 + (cv - 0.5) * (min(rvol, 4) - 1) * 2 if rvol > 1.5 else cv * 10
    return round(max(0.0, min(10.0, s)), 1), {"src": f"delivery-conviction × rvol ({rvol_src})",
            "rvol": round(float(rvol), 2), "rvol_basis": rvol_src,
            "daily_rvol": round(float(daily_rvol), 2), "delivery_conviction": conv,
            "delivery_trend": (dlv or {}).get("delivery_trend")}


def rel_strength_ranks(conn, symbols, nifty_pct) -> dict:
    """Cross-sectional 5-day relative-strength PERCENTILE across the F&O universe. Returns
    {symbol: (percentile_0_1, rel_pct, ret5_pct)}. Percentile normalises RS across vol regimes — a
    +3% week ranks differently when the whole universe is up 3% vs flat — making the factor
    comparable day to day instead of a raw return that drifts with market-wide volatility."""
    import bisect
    rels = {}
    for sym in symbols:
        df = _daily(conn, sym)
        if df is None or len(df) < 6:
            continue
        ret5 = (df["close"].iloc[-1] - df["close"].iloc[-6]) / df["close"].iloc[-6] * 100
        rels[sym] = (round(float(ret5 - (nifty_pct or 0)), 2), round(float(ret5), 2))
    if not rels:
        return {}
    vals = sorted(r[0] for r in rels.values())
    n = len(vals)
    return {s: (bisect.bisect_left(vals, rel) / max(1, n - 1), rel, ret5)
            for s, (rel, ret5) in rels.items()}


def stage_rel_strength(conn, sym, nifty_pct, rs_rank=None) -> tuple:
    if rs_rank is not None:
        pct, rel, ret5 = rs_rank                       # cross-sectional percentile (preferred)
        return round(pct * 10, 1), {"src": "5d RS percentile (F&O universe)",
                "percentile": round(pct * 100), "rel_strength": rel, "stock_5d_pct": ret5}
    df = _daily(conn, sym)                              # fallback: single-symbol linear (no universe)
    if df is None:
        return GAP, {"src": "candles=null"}
    ret5 = (df["close"].iloc[-1] - df["close"].iloc[-6]) / df["close"].iloc[-6] * 100
    rel = ret5 - (nifty_pct or 0)
    return round(max(0.0, min(10.0, 5 + rel * 0.5)), 1), {"src": "stock 5d vs nifty (no-universe)",
            "stock_5d_pct": round(float(ret5), 2), "nifty_pct": nifty_pct,
            "rel_strength": round(float(rel), 2)}


def _hv_iv_ratio(conn, sym):
    """IV / HV20 — implied vs realized vol premium. IV = current ATM IV (iv_daily); HV20 = annualised
    20-day realized vol. ratio > 1.2 = options EXPENSIVE (overpaying for the move; caution buying
    breakouts, favour short-vol). ratio < 1.0 = options CHEAP (breakouts attractive — gamma in your
    favour). Needs only current IV + HV, not IV history — usable immediately."""
    import numpy as np
    df = _daily(conn, sym)
    if df is None or len(df) < 21:
        return None
    rets = np.diff(np.log(df["close"].to_numpy(dtype=float)[-21:]))
    hv20 = float(rets.std() * (252 ** 0.5) * 100)
    iv = _robust_atm_iv(conn, sym)                     # median of liquid OTM strikes (chain), not iv_daily
    if hv20 <= 0 or iv is None:
        return None
    ratio = iv / hv20
    # IV/HV > ~3 with no known event is almost always bad chain data, not a real premium → flag it.
    return {"hv20": round(hv20, 1), "iv": round(iv, 1), "ratio": round(ratio, 2),
            "reliable": ratio <= 3.0}


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
    # COILED-SPRING NEUTRALISED 2026-06-22: the "low BB-width pctile → high score" rule tested
    # WRONG-SIGNED cross-sectionally (compressed names underperformed ~1–2% over 5–10d; IC −0.016/
    # −0.027 across 200 liquid names, 2025-05→2026-06). It no longer drives the score — start from a
    # neutral 5.0 and let only the conceptually-distinct, untested-but-sound signals move it:
    # term-structure (contango/backwardation) and HV/IV premium. BB/IV pctiles kept for context only.
    s = 5.0
    # IV TERM STRUCTURE: backwardation (near IV > far) = near-term event/vol already priced → the
    # spring is releasing, not coiled → cut the score; contango (calm) leaves it. DATA_GAP if 1 expiry.
    ts = _iv_term_structure(conn, sym)
    detail = {"src": "vol-regime: term_structure + HV/IV (coiled-spring NEUTRALISED — tested wrong-signed)",
              "bb_width_pctile": round(float(pctile), 0), "atr_pct": round(float(atr_pct), 2),
              "iv_pctile": iv_pctile, "coiled_note": "BB-width compression no longer scored (IC<0)",
              "iv_term_structure": ts if ts else "DATA_GAP (need n_expiries≥2)"}
    if ts:
        if ts["slope"] < -0.5:
            s -= 1.5                                    # backwardation = near-term event, not coiled
        elif ts["slope"] > 0.5:
            s += 0.5                                    # contango = genuinely calm/coiled
        detail["term_slope"] = ts["slope"]
    # HV/IV — realized vs implied premium. Cheap IV (ratio<1) = breakouts favourable (gamma works);
    # expensive IV (>1.2) = overpaying for the move → caution. Makes vol-expansion actionable.
    hv_iv = _hv_iv_ratio(conn, sym)
    if hv_iv:
        detail["hv_iv"] = hv_iv
        if not hv_iv.get("reliable"):
            detail["vol_premium"] = "DATA_GAP (IV/HV implausible — noisy chain, not scored)"
        else:
            r = hv_iv["ratio"]
            if r < 1.0:
                s += 0.8; detail["vol_premium"] = "CHEAP (breakout-favourable, gamma in your favour)"
            elif r > 1.2:
                s -= 0.8; detail["vol_premium"] = "EXPENSIVE (overpaying — caution on breakouts)"
            else:
                detail["vol_premium"] = "fair"
    return round(max(0.0, min(10.0, s)), 1), detail


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
    # MULTI-TIMEFRAME alignment: does the 1H (and 5M) confirm the daily structure's lean? A daily
    # that looks bullish while the 1H has rolled over is a false signal — pull the score toward
    # neutral. Confirmation across timeframes is the highest-quality structure read.
    lean = 1 if s > 5 else -1 if s < 5 else 0            # daily structure's directional lean
    atr = float(ta.atr(df["high"], df["low"], df["close"], 14).iloc[-1])
    h1_t, m5_t, m5_note = _mtf_trends(conn, sym, prior_close=float(close), atr=atr)
    if lean != 0 and h1_t is not None:
        if h1_t == lean:
            s += 1.0 * lean; detail["mtf"] = "1H aligned"
        elif h1_t == -lean:
            s -= 1.8 * lean; detail["mtf"] = "1H CONFLICTS (false-signal risk)"
        else:
            s -= 0.4 * lean; detail["mtf"] = "1H ranging (no confirmation)"
        if m5_t == lean:
            s += 0.4 * lean; detail["mtf"] += " · 5M trigger"
        detail["h1_trend"] = h1_t; detail["m5_trend"] = m5_t
        if m5_note:
            detail["m5_note"] = m5_note                  # opening-drive read or "swing-BOS"
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
    # Targets scaled to the stock's OWN ATR (realistic for a 1–5 day swing): T1 1.2·ATR, T2 2·ATR,
    # T3 3·ATR. A structural level (call-wall long / max-pain short) snaps T2 ONLY if it genuinely
    # sits between T1 and T3 — a far OTM wall (e.g. +18%) is ignored, not used as a fantasy target.
    entry = round(close, 1)
    sign = 1 if direction == "LONG" else -1
    stop = round(entry - sign * 1.3 * atr, 1)
    t1 = round(entry + sign * 1.2 * atr, 1)
    t2 = round(entry + sign * 2.0 * atr, 1)
    t3 = round(entry + sign * 3.0 * atr, 1)
    magnet = cw if direction == "LONG" else mp
    if magnet and sign * (magnet - t1) > 0 and sign * (magnet - t3) < 0:
        t2 = round(magnet, 1)                         # a real level within reach → use it for T2
    risk = abs(entry - stop)
    rr = round(abs(t2 - entry) / risk, 1) if risk else None
    # INTRADAY level set — fractions of the daily ATR (a single session captures ≲1 ATR net, not the
    # multi-day swing range). Tighter stop (0.6·ATR) + targets at 0.6/1.2/1.8 ATR.
    istop = round(entry - sign * 0.6 * atr, 1)
    it1 = round(entry + sign * 0.6 * atr, 1)
    it2 = round(entry + sign * 1.2 * atr, 1)
    it3 = round(entry + sign * 1.8 * atr, 1)
    irisk = abs(entry - istop)
    irr = round(abs(it2 - entry) / irisk, 1) if irisk else None
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
            "intraday_stop": istop, "intraday_t1": it1, "intraday_t2": it2, "intraday_t3": it3,
            "intraday_rr": irr,
            "basis": f"levels: call_wall {cw} / put_wall {pw} / max_pain {mp} / 20dH-L {hi20}-{lo20}, ATR ₹{round(atr,1)}"}


def confluence(stages, direction) -> dict:
    """CONFLUENCE CHECK — does the full factor stack actually support the setup's direction?

    The per-stock direction comes from a thin rule (max-pain drift + RS + position). This tallies
    every factor's bullish/bearish lean (options drift, PCR, range position, SMC, relative strength,
    sector, catalyst-risk) plus whether VOLUME confirms the move, and reports whether the confluence
    ALIGNS with, is MIXED on, or CONTRADICTS the direction — the correction the board was missing.
    """
    num = lambda x: x if isinstance(x, (int, float)) else None
    o = stages.get("options", {}); st = stages.get("structure", {})
    v = stages.get("volume", {}); rs = stages.get("rel_strength", {}); ca = stages.get("catalyst", {})
    pn = stages.get("positioning", {})
    drift = num(o.get("max_pain_drift_pct")) or 0
    pcr = num(o.get("pcr")); pos = num(st.get("range_pos_pct"))
    sw = str(st.get("last_sweep", "")); r = num(rs.get("rel_strength")) or 0
    rs_pctile = num(rs.get("percentile"))               # cross-sectional RS rank (0–100) if present
    risk = num(ca.get("news_risk")); divsig = num(pn.get("divergence_signal"))
    skew = num(o.get("put_skew"))
    flags = {
        "options_drift": 1 if drift > 0.5 else -1 if drift < -0.5 else 0,
        "pcr": 1 if pcr and pcr > 0.7 else -1 if pcr and pcr < 0.5 else 0,   # call-heavy = bearish
        "position": 1 if pos is not None and pos < 30 else -1 if pos is not None and pos > 80 else 0,
        "smc": 1 if "bull" in sw else -1 if "bear" in sw else 0,
        # cross-sectional RS percentile when available (top quartile = bullish, bottom = bearish)
        "rel_strength": (1 if rs_pctile > 70 else -1 if rs_pctile < 30 else 0) if rs_pctile is not None
                        else (1 if r > 1.5 else -1 if r < -1 else 0),
        "sector": -1 if st.get("sector_weak") else 0,
        "catalyst": -1 if risk is not None and risk < 70 else 0,   # negative-news drag
        "mtf_1h": 1 if num(st.get("h1_trend")) == 1 else -1 if num(st.get("h1_trend")) == -1 else 0,
        # FII-vs-retail divergence: institutions-more-long = bullish, retail-chasing = bearish
        "divergence": 1 if divsig is not None and divsig > 0.3 else -1 if divsig is not None and divsig < -0.3 else 0,
        # IV skew: puts-richer = protective bid (bullish), calls-richer = call/short-hedge (bearish)
        "iv_skew": 1 if skew is not None and skew > 1.0 else -1 if skew is not None and skew < -0.5 else 0,
    }
    bull = sum(flags.values())                            # net bullish lean across factors
    rvol = num(v.get("rvol")) or 1; conv = num(v.get("delivery_conviction"))
    vol = 1 if rvol > 1.3 and (conv or 0) >= 0.5 else -1 if rvol < 0.9 or (conv or 1) < 0.35 else 0
    want = 1 if direction == "LONG" else -1
    agree = bull * want                                   # >0 confluence supports the direction
    label = "ALIGNED" if agree >= 2 else "CONTRADICTED" if agree <= -2 else "MIXED"
    confirm = [k for k, f in flags.items() if f * want > 0]
    against = [k for k, f in flags.items() if f * want < 0]
    return {"net_bull": bull, "agreement": agree, "vol_confirm": vol, "label": label,
            "confirm": confirm, "against": against, "flags": flags}


def score_stock(conn, sym, *, sm_score, nifty_pct, macro=None, divergence=None, rs_rank=None) -> dict:
    scores = {
        "catalyst": stage_catalyst(conn, sym),
        "positioning": stage_positioning(conn, sym, sm_score, divergence),
        "options": stage_options(conn, sym),
        "structure": stage_structure(conn, sym),
        "volume": stage_volume(conn, sym),
        "rel_strength": stage_rel_strength(conn, sym, nifty_pct, rs_rank),
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
    # CONFLUENCE CORRECTION: reward factor-agreement + volume confirmation, penalise contradiction.
    conf = confluence(stages, plan.get("direction"))
    conv_adj = comp
    if comp is not None:
        conv_adj = round(max(0.0, comp + conf["agreement"] * 0.25 + conf["vol_confirm"] * 0.4), 2)
    prob = round(min(68, 45 + (conv_adj or 5) * 2.6)) if conv_adj else None
    return {"symbol": sym, "composite": comp, "conviction_adj": conv_adj,
            "tier": tier_of(conv_adj, scores, veto_flag), "renorm_weights": renorm,
            "data_gaps": gaps, "trade": plan, "probability_pct": prob, "news_flag": veto_flag,
            "confluence": conf, "stages": stages}


def run_conviction(conn, symbols=None) -> dict:
    macro = macro_regime(conn)
    nifty_pct = macro.get("nifty_pct") if macro.get("status") == "ok" else None
    sm = conn.execute("SELECT score FROM smart_money_daily ORDER BY as_of_date DESC LIMIT 1").fetchone()
    sm_score = sm[0] if sm else None
    divergence = participant_divergence(conn)            # FII-vs-retail, once for the whole book
    macro["participant_divergence"] = divergence          # surface it in the market bias
    if symbols is None:
        symbols = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM options_metrics "
                   "WHERE symbol NOT IN ('NIFTY','BANKNIFTY','FINNIFTY')")]
    rs_ranks = rel_strength_ranks(conn, symbols, nifty_pct)   # cross-sectional RS percentile, once
    scored = [score_stock(conn, s, sm_score=sm_score, nifty_pct=nifty_pct, macro=macro,
                          divergence=divergence, rs_rank=rs_ranks.get(s)) for s in symbols]
    scored = [x for x in scored if x["composite"] is not None]
    scored.sort(key=lambda x: -(x.get("conviction_adj") or x["composite"]))   # confluence-ranked
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
    cf = lambda x, k: x.get("confluence", {}).get(k)
    conn.executemany(
        "INSERT OR REPLACE INTO conviction_daily (as_of_date, symbol, composite, tier, catalyst, "
        "positioning, options, structure, volume, rel_strength, vol_expansion, data_gaps, "
        "stages_json, direction, entry, stop, t1, t2, t3, rr, setup, probability, open_iep, "
        "gap_pct, conviction_adj, conf_label, conf_agreement, conf_confirm, conf_against, "
        "vol_confirm, intraday_stop, intraday_t1, intraday_t2, intraday_t3, intraday_rr, "
        "updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [(today, x["symbol"], x["composite"], x["tier"], g(x, "catalyst"), g(x, "positioning"),
          g(x, "options"), g(x, "structure"), g(x, "volume"), g(x, "rel_strength"),
          g(x, "vol_expansion"), ",".join(x["data_gaps"]), json.dumps(x["stages"]),
          t(x, "direction"), t(x, "entry"), t(x, "stop"), t(x, "t1"), t(x, "t2"), t(x, "t3"),
          t(x, "rr"), t(x, "setup"), x.get("probability_pct"), t(x, "open_iep"), t(x, "gap_pct"),
          x.get("conviction_adj"), cf(x, "label"), cf(x, "agreement"),
          ",".join(cf(x, "confirm") or []), ",".join(cf(x, "against") or []), cf(x, "vol_confirm"),
          t(x, "intraday_stop"), t(x, "intraday_t1"), t(x, "intraday_t2"), t(x, "intraday_t3"),
          t(x, "intraday_rr"), now)
         for x in r["ranked"]])
    conn.commit()
    return {"date": today, "persisted": len(r["ranked"])}


def append_factor_log(conn) -> dict:
    """Append today's per-symbol factor SCORES (+ direction/confluence) to the append-only
    conviction_factor_log — the dataset for FORWARD, out-of-sample weight calibration. Captures the
    actual emitted scores including options/positioning (no backtest history), so over weeks we learn
    each factor's real forward IC from live signals. Forward returns are joined later from bhavcopy.
    Written once/day (pre-market run); idempotent on (snapshot_date, symbol)."""
    import time
    today = dt.datetime.now(IST).date().isoformat()
    rows = conn.execute(
        "SELECT symbol, direction, conf_label, conf_agreement, conviction_adj, composite, options, "
        "structure, positioning, volume, rel_strength, vol_expansion FROM conviction_daily "
        "WHERE as_of_date=?", (today,)).fetchall()
    if not rows:
        return {"logged": 0}
    now = int(time.time())
    out = []
    for r in rows:
        ec = conn.execute("SELECT close FROM raw_bhavcopy_cm WHERE symbol=? ORDER BY date DESC "
                          "LIMIT 1", (r[0],)).fetchone()
        out.append((today, *r, ec[0] if ec else None, now))
    conn.executemany(
        "INSERT OR REPLACE INTO conviction_factor_log (snapshot_date, symbol, direction, conf_label, "
        "conf_agreement, conviction_adj, composite, options, structure, positioning, volume, "
        "rel_strength, vol_expansion, entry_close, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        out)
    conn.commit()
    return {"logged": len(out)}


def register_conviction_job(scheduler, db_path: str) -> str:
    """Pre-market thesis at 09:10 IST + intraday refreshes every 5 min so the 1H/5M timeframes (and
    live options/volume/gap) update through the session. The 1-DAY structure stays fixed (daily bars
    don't change intraday) — only the lower-timeframe and live factors evolve."""
    import structlog
    from apscheduler.triggers.cron import CronTrigger

    from ..scheduler import market_hours
    from ..storage.db import open_db
    log = structlog.get_logger()

    def _run(tag):
        conn = open_db(db_path)
        try:
            log.info(tag, **run_and_persist(conn))
        except Exception:
            log.exception(f"{tag}_failed")
        finally:
            conn.close()

    # 09:10 IST — just after the NSE pre-open auction (09:08) so the gap/entry use the real
    # per-stock indicative open (IEP) + final GIFT, ~5 min before the 09:15 open. This run also
    # appends the day's factor scores to the append-only attribution log (once/day, pre-market).
    def _premarket():
        conn = open_db(db_path)
        try:
            log.info("conviction", **run_and_persist(conn))
            log.info("conviction_factor_log", **append_factor_log(conn))
        except Exception:
            log.exception("conviction_failed")
        finally:
            conn.close()

    scheduler.add_job(_premarket,
                      trigger=CronTrigger(hour=9, minute=10, timezone=market_hours.IST),
                      id="conviction", max_instances=1, coalesce=True, replace_existing=True)

    # Intraday refresh every 5 min while the market is open (first complete 5M bar at 09:20, first
    # complete 1H bar at 10:15, …) — re-runs the engine so the 1H/5M timeframes track today live.
    def _intraday():
        if market_hours.is_market_open():
            _run("conviction_intraday")

    scheduler.add_job(_intraday, trigger=CronTrigger(minute="*/5", timezone=market_hours.IST),
                      id="conviction_intraday", max_instances=1, coalesce=True, replace_existing=True)
    return "conviction"
