"""The pre-buy conviction card (PROFITABILITY_PLAN Track C, R16).

Assembles, for ONE symbol, the "what to know before you buy" screen from everything the
conviction layer now produces — valuation, quality, cash quality, balance-sheet strength,
promoter pledge, ownership flows, catalyst, delivery, surveillance, the ATR risk plan, and
the paper-book track record of the strategy that would pick it. Every section degrades to
None / "n/a" when its source is missing, so the card renders on partial data.

This is the read/presentation layer — it computes nothing new, it gathers. Sizing reuses
`paper_trade._size_position` and the paper track reuses `paper_report.trade_metrics`, so
the card never disagrees with the engine. `build_card` returns a structured dict;
`format_card` renders the box; `scripts/pre_buy_card.py` is the CLI.
"""
from __future__ import annotations

import sqlite3

from ..collectors.fno_ban import is_fno_banned
from ..fundamentals import strength_scores as ss
from .paper_report import trade_metrics
from .ownership_flow import ownership_flow
from .paper_trade import PaperTradeParams, _size_position
from .valuation_history import valuation_percentile


def _has(conn, name) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _one(conn, sql, params=()):
    if not _has(conn, sql.split(" FROM ")[1].split()[0]):
        return None
    return conn.execute(sql, params).fetchone()


def _price(conn, sym):
    r = _one(conn, "SELECT close FROM raw_intraday_candles WHERE symbol=? AND interval='day' "
                   "AND close IS NOT NULL ORDER BY ts DESC LIMIT 1", (sym,))
    return r[0] if r else None


def _grade_atr(conn, sym):
    r = _one(conn, "SELECT grade, atr_pct FROM tradeable_universe WHERE symbol=?", (sym,))
    return (r[0], r[1]) if r else (None, None)


def _valuation(conn, sym):
    r = _one(conn, "SELECT valuation, sector_rank, sector_n, grade, composite FROM factor_snapshot "
                   "WHERE symbol=? ORDER BY snapshot_date DESC LIMIT 1", (sym,))
    f = _one(conn, "SELECT pe_ratio FROM stock_fundamentals WHERE symbol=?", (sym,))
    vh = valuation_percentile(conn, sym) if _has(conn, "extracted_financials") else None
    if not r and not f and not vh:
        return None
    return {"score": r[0] if r else None, "sector_rank": r[1] if r else None,
            "sector_n": r[2] if r else None, "composite": r[4] if r else None,
            "pe": f[0] if f else None,                              # R6: cheap vs own history
            "own_history_pctile": vh["pctile"] if vh else None,
            "own_history_span": vh["span_years"] if vh else None,
            "own_cheap": vh["cheap"] if vh else None,
            "own_expensive": vh["expensive"] if vh else None}


def _quality(conn, sym):
    f = _one(conn, "SELECT quality_score, roe, roce FROM stock_fundamentals WHERE symbol=?", (sym,))
    if not f:
        return None
    return {"score": f[0], "roe": f[1], "roce": f[2]}


def _promoter(conn, sym):
    f = _one(conn, "SELECT promoter_holding, promoter_pledge FROM stock_fundamentals WHERE symbol=?", (sym,))
    if not f:
        return None
    return {"holding": f[0], "pledge": f[1]}


def _delivery(conn, sym):
    r = _one(conn, "SELECT delivery_trend, delivery_ratio FROM delivery_conviction WHERE symbol=? "
                   "ORDER BY session_date DESC LIMIT 1", (sym,))
    return {"trend": r[0], "ratio": r[1]} if r else None


def _flows(conn, sym):
    return ownership_flow(conn, sym)        # R11: {block, insider}, sections None when absent


def _catalyst(conn, sym):
    out = {}
    res = _one(conn, "SELECT period_ending, broadcast_dt FROM extracted_financials WHERE symbol=? "
                     "ORDER BY period_ending DESC LIMIT 1", (sym,))
    if res:
        out["latest_result"] = res[0]
    rate = _one(conn, "SELECT broadcast_dt, worst_action, min_lt_grade, credit_quality_score, "
                      "outlook_negative FROM raw_rating_actions WHERE symbol=? "
                      "ORDER BY broadcast_dt DESC LIMIT 1", (sym,))
    if rate:
        out["credit"] = {"date": rate[0], "action": rate[1], "grade": rate[2],
                         "quality_score": rate[3], "outlook_negative": rate[4]}
    return out or None


def _surveillance(conn, sym):
    hits = []
    for tbl, label in (("raw_surveillance_gsm", "GSM"),
                       ("raw_surveillance_asm_lt", "ASM-LT"),
                       ("raw_surveillance_asm_st", "ASM-ST")):
        if not _has(conn, tbl):
            continue
        r = conn.execute(f"SELECT stage FROM {tbl} WHERE symbol=? ORDER BY as_on DESC LIMIT 1",
                         (sym,)).fetchone()
        if r:
            hits.append(f"{label} {r[0]}")
    # any table present means we could check; absence of hits = clean
    checked = any(_has(conn, t) for t in
                  ("raw_surveillance_gsm", "raw_surveillance_asm_lt", "raw_surveillance_asm_st"))
    banned = is_fno_banned(conn, sym)        # R13 — F&O ban (risk flag for a delivery book)
    if banned:
        hits.append("F&O-BAN")
    return {"checked": checked or _has(conn, "raw_fno_ban"), "hits": hits, "fno_banned": banned}


def _risk_plan(conn, sym, price, atr_pct, params):
    if not price:
        return None
    stop, qty, risk = _size_position(price, atr_pct, params)
    if stop is None:
        return None
    per_share = round(price - stop, 2)
    return {"entry": round(price, 2), "stop": stop, "qty": qty, "risk_rupees": risk,
            "risk_pct": params.risk_pct, "stop_pct": round((stop / price - 1) * 100, 1),
            "target_1r": round(price + per_share, 2), "target_2r": round(price + 2 * per_share, 2),
            "rr_to_2r": 2.0}


def _paper(conn, strategy="lean"):
    if not _has(conn, "paper_book"):
        return None
    rows = conn.execute(
        "SELECT net_pct, r_multiple FROM paper_book WHERE status='closed' AND net_pct IS NOT NULL "
        "AND strategy=?", (strategy,)).fetchall()
    if not rows:
        return {"strategy": strategy, "n": 0}
    m = trade_metrics([r[0] for r in rows])
    rs = [r[1] for r in rows if r[1] is not None]
    return {"strategy": strategy, "n": m["n"], "expectancy_pct": m["expectancy"],
            "profit_factor": m["profit_factor"], "win_rate": m["win_rate"],
            "avg_r": round(sum(rs) / len(rs), 2) if rs else None}


def build_card(conn: sqlite3.Connection, symbol: str, *,
               params: PaperTradeParams | None = None) -> dict:
    """Gather every conviction section for `symbol` into one structured card."""
    params = params or PaperTradeParams()
    grade, atr_pct = _grade_atr(conn, symbol)
    price = _price(conn, symbol)
    now, prior = ss.load_periods(conn, symbol)
    strength = ss.compute_strength(now, prior)
    cfo, pat = (now or {}).get("cfo_cr"), (now or {}).get("pat_cr")
    cash = ({"cfo_cr": cfo, "pat_cr": pat,
             "cfo_to_pat": round(cfo / pat, 2) if (cfo is not None and pat) else None}
            if now else None)
    return {
        "symbol": symbol, "grade": grade, "price": price,
        "valuation": _valuation(conn, symbol),
        "quality": _quality(conn, symbol),
        "strength": strength if (strength.get("bs_score") is not None
                                 or strength.get("f_score") is not None
                                 or strength.get("distress")) else None,
        "cash": cash,
        "promoter": _promoter(conn, symbol),
        "flows": _flows(conn, symbol),
        "catalyst": _catalyst(conn, symbol),
        "delivery": _delivery(conn, symbol),
        "surveillance": _surveillance(conn, symbol),
        "risk_plan": _risk_plan(conn, symbol, price, atr_pct, params),
        "paper": _paper(conn),
    }


# ---- formatting ------------------------------------------------------------

def _g(ok):
    return {True: "✓", False: "✗", None: "·"}[ok]


def _line(label, glyph, text):
    return f" {glyph} {label:11s} {text}"


def format_card(c: dict) -> str:
    L = ["┌" + "─" * 62, f"│  {c['symbol']}"
         + (f"  [{c['grade']}]" if c["grade"] else "")
         + (f"   ₹{c['price']:.2f}" if c["price"] else "  (no price)"),
         "├" + "─" * 62]

    v = c["valuation"]
    if v:
        rank = (f"#{v['sector_rank']}/{v['sector_n']} in sector" if v["sector_rank"] else "n/a")
        own, span = v.get("own_history_pctile"), v.get("own_history_span")
        own_txt = (f"own-hist {own}th pctile ({span}y)" if own is not None else "own-history n/a")
        # glyph prefers the (span-gated) own-history verdict; falls back to sector rank
        if own is not None:
            glyph = "✓" if v.get("own_cheap") else ("✗" if v.get("own_expensive") else "·")
        elif v["sector_rank"]:
            glyph = _g(v["sector_n"] and v["sector_rank"] <= v["sector_n"] / 3)
        else:
            glyph = "·"
        L.append(_line("VALUATION", glyph, f"PE {v['pe'] or 'n/a'} · {rank} · {own_txt}"))

    q = c["quality"]
    if q and q["score"] is not None:
        L.append(_line("QUALITY", _g(q["score"] >= 50),
                       f"{q['score']:.0f}/100 · ROE {q['roe'] or 'n/a'} · ROCE {q['roce'] or 'n/a'}"))

    s = c["strength"]
    if s:
        if s["f_score"] is not None:
            enough = (s["f_signals"] or 0) >= 5            # F-score needs most signals to mean anything
            note = "" if enough else " (insufficient data)"
            L.append(_line("STRENGTH", _g(s["f_score"] >= 6) if enough else "·",
                           f"Piotroski F {s['f_score']}/{s['f_signals']} signals{note}"))
        if s["bs_score"] is not None:
            ic = s["interest_coverage"]
            L.append(_line("BALANCE", _g(s["bs_score"] >= 60),
                           f"BS {s['bs_score']:.0f}/100 · int.cover "
                           f"{(str(ic) + 'x') if ic is not None else 'n/a'} · D/E {s['debt_equity'] or 'n/a'}"))
        if s["distress"]:
            L.append(_line("DISTRESS", "✗", ", ".join(s["distress"])))

    ca = c["cash"]
    if ca and ca["cfo_to_pat"] is not None:
        L.append(_line("CASH", _g(ca["cfo_to_pat"] >= 0.8),
                       f"CFO/PAT {ca['cfo_to_pat']} (earnings {'cash-backed' if ca['cfo_to_pat'] >= 0.8 else 'low cash conversion'})"))

    p = c["promoter"]
    if p and p["holding"] is not None:
        pl = p["pledge"]
        if pl is None or pl <= 0:
            glyph = "✓"
        elif pl <= 25:
            glyph = "·"
        else:
            glyph = "✗"
        L.append(_line("PROMOTER", glyph,
                       f"holding {p['holding']:.0f}% · pledge {('%.0f%%' % pl) if pl is not None else 'n/a'}"))

    fl = c["flows"]
    b = fl["block"] if fl else None
    if b:
        net = b["net_value_cr"]
        bg = "✓" if net > 0 else ("✗" if net < 0 else "·")
        ins = fl["insider"]
        ins_txt = (f"insider net ₹{ins['net_value_cr']:+.0f}Cr" if ins else "insider n/a")
        t1 = b.get("tier1_net_cr") or 0
        tier_txt = f" · tier-1 ₹{t1:+.0f}Cr" if t1 else ""        # W24 institution tiering
        L.append(_line("FLOWS", bg,
                       f"block/bulk {b['days']}d: net ₹{net:+.0f}Cr "
                       f"({b['buy_deals']}B/{b['sell_deals']}S){tier_txt} · {ins_txt}"))

    cat = c["catalyst"]
    if cat:
        bits = []
        if cat.get("latest_result"):
            bits.append(f"result {cat['latest_result']}")
        if cat.get("credit"):
            cr = cat["credit"]
            bits.append(f"credit {cr['grade'] or '?'} ({cr['action'] or '?'})"
                        + (" ⚠outlook-neg" if cr.get("outlook_negative") else ""))
        L.append(_line("CATALYST", "·", " · ".join(bits) or "n/a"))

    d = c["delivery"]
    if d:
        dg = "✓" if d["trend"] == "rising" else ("✗" if d["trend"] == "falling" else "·")
        L.append(_line("DELIVERY", dg,
                       f"{d['trend'] or 'n/a'} ({(d['ratio'] or 0) * 100:.0f}%)"
                       if d["ratio"] is not None else f"{d['trend'] or 'n/a'}"))

    sv = c["surveillance"]
    if sv and sv["checked"]:
        L.append(_line("GOVERNANCE", _g(not sv["hits"]),
                       ", ".join(sv["hits"]) if sv["hits"] else "not in ASM/GSM/F&O-ban"))

    rp = c["risk_plan"]
    if rp:
        L.append("├" + "─" * 62)
        L.append(_line("RISK PLAN", " ",
                       f"entry ₹{rp['entry']} · stop ₹{rp['stop']} ({rp['stop_pct']}%) · "
                       f"{rp['risk_pct']}% risk"))
        L.append(_line("", " ",
                       f"qty {rp['qty']} (₹{rp['risk_rupees']:.0f} at risk) · "
                       f"T1 ₹{rp['target_1r']} · T2 ₹{rp['target_2r']} (R:R {rp['rr_to_2r']})"))

    pa = c["paper"]
    if pa:
        L.append("├" + "─" * 62)
        if pa["n"] == 0:
            L.append(_line("PAPER", " ", f"{pa['strategy']}: track record accruing (0 closed)"))
        else:
            exp = pa["expectancy_pct"]
            avgr = (f" · {pa['avg_r']:+.2f}R" if pa["avg_r"] is not None else "")
            pf = pa["profit_factor"]
            pf_s = "∞" if pf is None else f"{pf:.2f}"
            L.append(_line("PAPER", " ",
                           f"{pa['strategy']}: exp {exp:+.2f}%{avgr} · PF {pf_s} · "
                           f"win {(pa['win_rate'] or 0) * 100:.0f}% · n={pa['n']}"))
    L.append("└" + "─" * 62)
    return "\n".join(L)
