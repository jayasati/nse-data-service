"""Pre-buy synthesis card — the full picture on ONE stock, before you act.

Fuses everything the system already computes for a symbol (conviction, FPI sector flow, institutional
deals, promoter activity, upcoming events, options, technicals, news/cause, shareholding, any open
paper position) into one grounded view, then an LLM reads it into a bull/bear/watch synthesis.

This is SYNTHESIS of existing signals, not a new signal — and it's analysis, not a buy/sell call
(the LLM is told to frame it that way and to ground every claim in the data, no fabrication).
Served at /api/prebuy/{symbol}; the LLM read is opt-in (?synthesize=1) to control the $25/day cap.
"""
from __future__ import annotations

import sqlite3

import structlog

log = structlog.get_logger(__name__)

SYSTEM_PROMPT = (
    "You are a markets analyst writing a one-screen pre-trade brief on a single NSE stock for a "
    "swing/positional desk (1-10 day horizon). Using ONLY the structured signals provided (never "
    "invent a fact or number), write: BULL (what's supportive), BEAR (what's against), WATCH (key "
    "things/levels/events), and NET (a balanced one-line read). Cite the specific signals. This is "
    "analysis to inform a human, NOT a buy/sell recommendation — frame it that way. If signals are "
    "sparse or conflicting, say so plainly. <=180 words."
)


def gather(conn: sqlite3.Connection, symbol: str) -> dict:
    """Pull every per-symbol signal the system computes. Defensive — missing table/data → omitted."""
    sym = symbol.upper()

    def rows(sql, args=()):
        try:
            cur = conn.execute(sql, args)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        except Exception:  # noqa: BLE001
            return []

    def one(sql, args=()):
        r = rows(sql, args)
        return r[0] if r else None

    out: dict = {"symbol": sym}
    out["profile"] = one(
        "SELECT trend_regime, momentum_state, rsi_14, sma_50, sma_200, high_52w, low_52w, "
        "delivery_ratio, pe_ratio, market_cap FROM stock_profile_daily WHERE symbol=? "
        "ORDER BY session_date DESC LIMIT 1", (sym,))
    out["conviction"] = one(
        "SELECT conviction_adj, conf_label, direction, as_of_date FROM conviction_daily "
        "WHERE symbol=? ORDER BY as_of_date DESC LIMIT 1", (sym,))
    out["fpi_sector"] = one(
        "SELECT sector, net_equity_cr, signal FROM fpi_sector_stock WHERE symbol=? "
        "ORDER BY as_of_date DESC LIMIT 1", (sym,))
    out["deals"] = rows(
        "SELECT deal_date, entity_type, txn_type, value_cr, signal_type FROM large_deal_signals "
        "WHERE symbol=? AND created_at>=datetime('now','-20 day') ORDER BY deal_date DESC LIMIT 5",
        (sym,))
    out["promoter"] = rows(
        "SELECT filing_date, signal_type, holding_change_pct FROM promoter_signals WHERE symbol=? "
        "AND signal_type NOT IN ('NEUTRAL','SKIP') AND filing_date>=date('now','-120 day') "
        "ORDER BY filing_date DESC LIMIT 4", (sym,))
    out["events"] = rows(
        "SELECT event_type, expected_date FROM pending_events WHERE symbol=? AND status='upcoming' "
        "AND expected_date>=date('now') ORDER BY expected_date LIMIT 5", (sym,))
    out["options"] = one(
        "SELECT pcr, max_pain, gex_sign, spot FROM options_metrics WHERE symbol=? "
        "ORDER BY as_of DESC LIMIT 1", (sym,))
    out["cause"] = one(
        "SELECT date, category, cause_summary FROM move_causes WHERE symbol=? "
        "ORDER BY date DESC LIMIT 1", (sym,))
    out["news"] = rows(
        "SELECT headline FROM raw_news WHERE symbol=? ORDER BY published_epoch DESC LIMIT 4", (sym,))
    out["shareholding"] = one(
        "SELECT promoter_pct, public_pct, qe_date FROM raw_shareholding_pattern WHERE symbol=? "
        "ORDER BY record_id DESC LIMIT 1", (sym,))
    out["paper"] = rows(
        "SELECT strategy, direction, entry_date, entry_px FROM paper_book WHERE symbol=? "
        "AND status='open'", (sym,))
    return out


def format_block(d: dict) -> tuple[str, int]:
    """Grounded text block + count of populated signal groups (sparse-data guard)."""
    L, n = [f"STOCK: {d['symbol']}"], 0

    def add(label, val, render):
        nonlocal n
        if val:
            n += 1
            L.append(f"{label}: {render(val)}")

    p = d.get("profile")
    if p:
        n += 1
        rsi = p.get("rsi_14")
        L.append(f"TECHNICALS: trend={p.get('trend_regime')} momentum={p.get('momentum_state')} "
                 f"RSI={round(rsi, 1) if rsi is not None else '?'} "
                 f"SMA50={p.get('sma_50')} SMA200={p.get('sma_200')} "
                 f"52w[{p.get('low_52w')}-{p.get('high_52w')}] deliv={p.get('delivery_ratio')} PE={p.get('pe_ratio')}")
    add("CONVICTION", d.get("conviction"),
        lambda c: f"{c.get('conf_label')} {c.get('direction')} adj={c.get('conviction_adj')} ({c.get('as_of_date')})")
    add("FPI SECTOR FLOW", d.get("fpi_sector"),
        lambda f: f"{f['sector']} {f['signal']} (sector net ₹{f['net_equity_cr']:.0f}cr)")
    add("INSTITUTIONAL DEALS", d.get("deals"),
        lambda ds: "; ".join(f"{x['deal_date']} {x['entity_type']} {x['txn_type']} "
                             f"₹{x['value_cr']:.0f}cr [{x['signal_type']}]" for x in ds if x.get('value_cr')))
    add("PROMOTER", d.get("promoter"),
        lambda ps: "; ".join(f"{x['filing_date']} {x['signal_type']}" for x in ps))
    add("UPCOMING EVENTS", d.get("events"),
        lambda es: ", ".join(f"{x['event_type']} {x['expected_date'][5:]}" for x in es))
    add("OPTIONS", d.get("options"),
        lambda o: f"PCR={o.get('pcr')} max_pain={o.get('max_pain')} GEX={o.get('gex_sign')}")
    add("CAUSE (last move)", d.get("cause"),
        lambda c: f"{c.get('date')} {c.get('category')}: {(c.get('cause_summary') or '')[:120]}")
    add("RECENT NEWS", d.get("news"),
        lambda ns: " | ".join(x['headline'][:80] for x in ns))
    add("SHAREHOLDING", d.get("shareholding"),
        lambda s: f"promoter {s.get('promoter_pct')}% public {s.get('public_pct')}% (QE {s.get('qe_date')})")
    add("OPEN PAPER POSITION", d.get("paper"),
        lambda ps: "; ".join(f"{x['strategy']} {x['direction']} from {x['entry_date']}" for x in ps))
    return "\n".join(L), n


def score_card(conn: sqlite3.Connection, symbol: str) -> str:
    """Terse quantitative scoreboard for a symbol — factor grades, conviction, financial strength.
    No LLM (instant, free); the numeric counterpart to the narrative pre-buy card."""
    sym = symbol.upper()

    def one(sql):
        try:
            cur = conn.execute(sql, (sym,))
            cols = [d[0] for d in cur.description]
            row = cur.fetchone()
            return dict(zip(cols, row)) if row else None
        except Exception:  # noqa: BLE001
            return None

    f = one("SELECT grade, composite, sector_rank, sector_n, sector, quality, valuation, momentum, "
            "liquidity, risk, regime FROM factor_snapshot WHERE symbol=? ORDER BY snapshot_date DESC LIMIT 1")
    c = one("SELECT conf_label, direction, conviction_adj, rr, setup FROM conviction_daily "
            "WHERE symbol=? ORDER BY as_of_date DESC LIMIT 1")
    s = one("SELECT f_score, bs_score, debt_equity, distress FROM stock_strength WHERE symbol=? "
            "ORDER BY updated_date DESC LIMIT 1")
    if not (f or c or s):
        return f"📊 {sym}: no scores on this name."
    L = [f"📊 {sym} scores"]

    def r0(v):
        return round(v) if isinstance(v, (int, float)) else "–"

    if f:
        L.append(f"Factor: {f.get('grade')} · composite {r0(f.get('composite'))} · "
                 f"rank {f.get('sector_rank')}/{f.get('sector_n')} ({f.get('sector')}, {f.get('regime')})")
        L.append(f"  Q{r0(f.get('quality'))} V{r0(f.get('valuation'))} M{r0(f.get('momentum'))} "
                 f"Liq{r0(f.get('liquidity'))} Risk{r0(f.get('risk'))}")
    if c:
        L.append(f"Conviction: {c.get('conf_label')} {c.get('direction')} "
                 f"adj={c.get('conviction_adj')} RR={c.get('rr')} [{c.get('setup')}]")
    if s:
        parts = []
        if s.get("f_score") is not None:
            parts.append(f"Piotroski {s['f_score']}/{s.get('f_signals')}")
        if s.get("bs_score") is not None:
            parts.append(f"BS {r0(s.get('bs_score'))}")
        if s.get("debt_equity") is not None:
            parts.append(f"D/E {s['debt_equity']}")
        if s.get("distress"):
            parts.append("⚠️DISTRESS")
        if parts:
            L.append("Strength: " + " · ".join(parts))
    return "\n".join(L)


def synthesize(conn: sqlite3.Connection, symbol: str) -> dict:
    """Build the grounded card + LLM read. Returns {symbol, signals, block, n_signals, synthesis, cost_usd}."""
    data = gather(conn, symbol)
    block, n = format_block(data)
    result = {"symbol": symbol.upper(), "signals": data, "block": block, "n_signals": n,
              "synthesis": None, "cost_usd": 0.0}
    if n == 0:
        result["synthesis"] = "No signals on this name."
        return result
    from ..parsers.extractors.llm_client import DailyCapExceeded, LLMClient
    try:
        res = LLMClient().chat_completion(
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": block}],
            max_tokens=600, temperature=0.3)
        result["cost_usd"] = res.cost_usd
        result["synthesis"] = res.content if res.success else f"(LLM unavailable: {res.error})"
    except DailyCapExceeded:
        result["synthesis"] = "(LLM daily cap reached — structured signals only)"
    except Exception as e:  # noqa: BLE001
        log.exception("prebuy_synthesize_failed")
        result["synthesis"] = f"(synthesis error: {type(e).__name__})"
    return result
