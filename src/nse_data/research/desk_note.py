"""LLM desk-analyst note — a grounded daily synthesis over the structured signal tables.

This is the "be informed" product: it turns the pile of signals (basket rotation, institutional
deals, promoter buys, events, macro, options, conviction, universe gaps) into one readable
assessment. It is ANALYSIS, not a trade signal — the LLM reasons over REAL rows only (no
fabrication), it never auto-trades or auto-scores conviction, and it's bounded by the LLMClient's
in-code $25/day cap. If a note repeatedly flags a tradeable pattern, that graduates to a paper
track (like deal_flow/promoter_flow) — it does not become a live alert on the LLM's say-so.

Runs nightly 22:45 IST (after all EOD signal jobs land). Stored in desk_notes + pushed to ntfy.
"""
from __future__ import annotations

import sqlite3

import structlog

log = structlog.get_logger(__name__)

SYSTEM_PROMPT = (
    "You are a senior markets desk analyst for an NSE swing/positional trading desk (1-10 day "
    "horizon; intraday scalping is OFF — validated net-negative). Using ONLY the structured "
    "signals provided (do NOT invent any fact, name, or number not present), write a concise "
    "daily desk note (<= 230 words) with three short sections:\n"
    "1) SETUP — the macro/regime backdrop in 1-2 sentences.\n"
    "2) CONVERGENCE — names where MULTIPLE signals align (e.g. a stock with a promoter buy AND "
    "an institutional deal AND an upcoming event, or members of a rotating basket). Name them.\n"
    "3) WATCH — 2-4 specific things to watch next session.\n"
    "Cite specific symbols/numbers from the data. These are SIGNAL INPUTS, not validated trades — "
    "frame as analysis ('X is setting up', 'worth watching'), never as 'buy/sell X'. If the data "
    "is thin, say so plainly rather than padding."
)


def _rows(conn, sql, args=()):
    try:
        cur = conn.execute(sql, args)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception:  # noqa: BLE001 — missing table / drift → empty
        return []


def gather_signals(conn: sqlite3.Connection) -> dict:
    """Pull the day's structured signals — the exact grounding the LLM may reason over."""
    pm = _rows(conn, "SELECT regime, macro_bias, gift_signal, gift_nifty_pct, brent, brent_pct, "
                     "india_vix, india_vix_signal, copper_pct, sp500_pct FROM premarket_snapshots "
                     "ORDER BY snapshot_date DESC LIMIT 1")
    try:
        from .fpi_sector import rotation
        fpi_sector = rotation(conn, n=4)
    except Exception:  # noqa: BLE001
        fpi_sector = {}
    return {
        "macro": pm[0] if pm else {},
        "fpi": _rows(conn, "SELECT net_5d_cr, regime FROM fpi_flow "
                           "ORDER BY as_of_date DESC LIMIT 1"),
        "fpi_sector": fpi_sector,
        "fpi_headwind": _rows(conn, "SELECT DISTINCT symbol FROM fpi_sector_stock WHERE "
                                    "as_of_date=(SELECT MAX(as_of_date) FROM fpi_sector_stock) "
                                    "AND signal='FPI_SECTOR_HEADWIND' LIMIT 14"),
        "fpi_tailwind": _rows(conn, "SELECT DISTINCT symbol FROM fpi_sector_stock WHERE "
                                    "as_of_date=(SELECT MAX(as_of_date) FROM fpi_sector_stock) "
                                    "AND signal='FPI_SECTOR_TAILWIND' LIMIT 14"),
        "baskets": _rows(conn, "SELECT basket_name, signal_type, confidence, advancing, declining "
                               "FROM basket_signals WHERE signal_date=(SELECT MAX(signal_date) "
                               "FROM basket_signals) ORDER BY confidence DESC"),
        "inst_deals": _rows(conn, "SELECT symbol, GROUP_CONCAT(DISTINCT entity_type) entities, "
                                  "ROUND(SUM(value_cr)) value_cr FROM large_deal_signals WHERE "
                                  "signal_type IN ('INSTITUTIONAL_BUY','INSTITUTIONAL_BUY_LARGE') "
                                  "AND created_at>=datetime('now','-2 day') GROUP BY symbol "
                                  "ORDER BY value_cr DESC LIMIT 10"),
        "promoter_buys": _rows(conn, "SELECT symbol, MIN(signal_type) signal FROM promoter_signals "
                                     "WHERE signal_type IN ('PROMOTER_BUY','PROMOTER_BUY_STRONG',"
                                     "'PROMOTER_SUSTAINED') AND created_at>=datetime('now','-2 day') "
                                     "GROUP BY symbol LIMIT 12"),
        "events_3d": _rows(conn, "SELECT symbol, event_type, expected_date FROM pending_events "
                                 "WHERE status='upcoming' AND event_type!='result' AND "
                                 "expected_date>date('now') AND expected_date<=date('now','+3 day') "
                                 "ORDER BY expected_date LIMIT 15"),
        "smallcap": _rows(conn, "SELECT symbol, move_pct, vol_ratio, signal FROM smallcap_signals "
                                "WHERE signal_date=(SELECT MAX(signal_date) FROM smallcap_signals) "
                                "AND signal IS NOT NULL ORDER BY move_pct DESC LIMIT 8"),
        "conviction": _rows(conn, "SELECT symbol, conviction_adj, direction FROM conviction_daily "
                                  "WHERE as_of_date=(SELECT MAX(as_of_date) FROM conviction_daily) "
                                  "AND conf_label='ALIGNED' ORDER BY ABS(conviction_adj) DESC LIMIT 12"),
        "universe_gaps": _rows(conn, "SELECT symbol, move_pct, reason_out FROM universe_gaps WHERE "
                                     "gap_date=(SELECT MAX(gap_date) FROM universe_gaps) "
                                     "ORDER BY ABS(move_pct) DESC LIMIT 8"),
    }


def _fmt_signals(sig: dict) -> tuple[str, int]:
    """Render the signals as a compact grounded block; return (text, n_signals)."""
    L, n = [], 0
    m = sig.get("macro") or {}
    if m:
        L.append(f"MACRO: regime={m.get('regime')} bias={m.get('macro_bias')} "
                 f"GIFT={m.get('gift_signal')}({m.get('gift_nifty_pct')}%) "
                 f"crude=${m.get('brent')}({m.get('brent_pct')}%) "
                 f"VIX={m.get('india_vix')}({m.get('india_vix_signal')}) "
                 f"copper={m.get('copper_pct')}% S&P={m.get('sp500_pct')}%")
    fpi = sig.get("fpi")
    if fpi:
        L.append(f"FPI FLOW (5d equity net): ₹{fpi[0]['net_5d_cr']:+.0f}cr → {fpi[0]['regime']}")
    fs = sig.get("fpi_sector") or {}
    if fs.get("into") or fs.get("out_of"):
        into = ", ".join(f"{s}(+{v:.0f})" for s, v in fs.get("into", []))
        out = ", ".join(f"{s}({v:.0f})" for s, v in fs.get("out_of", []))
        L.append(f"FPI SECTOR ROTATION ({fs['as_of_date']}): into [{into}] · out of [{out}]")
    hw = [r["symbol"] for r in sig.get("fpi_headwind", [])]
    tw = [r["symbol"] for r in sig.get("fpi_tailwind", [])]
    if hw or tw:
        L.append(f"FPI SECTOR-FLOW NAMES — headwind (outflow sector): {', '.join(hw) or '—'}"
                 f"  |  tailwind: {', '.join(tw) or '—'}")
    def add(title, rows, render):
        nonlocal n
        if rows:
            n += len(rows)
            L.append(f"{title}: " + "; ".join(render(r) for r in rows))
    add("BASKETS", sig["baskets"], lambda r: f"{r['basket_name']} {r['signal_type']}(conf {r['confidence']}, {r['advancing']}up/{r['declining']}dn)")
    add("INSTITUTIONAL DEALS (disclosed buys)", sig["inst_deals"], lambda r: f"{r['symbol']}({r['entities']} ₹{r['value_cr']}cr)")
    add("PROMOTER BUYS", sig["promoter_buys"], lambda r: f"{r['symbol']}({r['signal'].replace('PROMOTER_','').lower()})")
    add("UPCOMING EVENTS (<=3d)", sig["events_3d"], lambda r: f"{r['symbol']}({r['event_type']} {r['expected_date'][5:]})")
    add("SMALL-CAP MOMENTUM", sig["smallcap"], lambda r: f"{r['symbol']}(+{r['move_pct']}%, {r['vol_ratio']}x vol)")
    add("CONVICTION (aligned)", sig["conviction"], lambda r: f"{r['symbol']}({r['direction']} {r['conviction_adj']})")
    add("UNTRACKED >7% MOVERS", sig["universe_gaps"], lambda r: f"{r['symbol']}({r['move_pct']}%, {r['reason_out']})")
    return "\n".join(L) if L else "(no structured signals today)", n


def generate(conn: sqlite3.Connection, llm) -> dict:
    """Build the grounded prompt, call the LLM (bounded), return {note, cost_usd, n_signals}."""
    from ..scheduler import market_hours
    sig = gather_signals(conn)
    block, n = _fmt_signals(sig)
    if n == 0:
        return {"note": None, "cost_usd": 0.0, "n_signals": 0, "skipped": "no signals"}
    today = market_hours.now_ist().strftime("%A %d %B %Y")  # ground the date — don't let it invent one
    res = llm.chat_completion(
        messages=[{"role": "system", "content": SYSTEM_PROMPT},
                  {"role": "user", "content": f"Date: {today} (IST). Use THIS date in the note "
                   f"header — do not invent a date.\n\nToday's structured signals:\n\n{block}"}],
        max_tokens=900, temperature=0.3)
    if not res.success:
        log.info("desk_note_llm_failed", error=res.error)
        return {"note": None, "cost_usd": res.cost_usd, "n_signals": n, "error": res.error}
    return {"note": res.content, "cost_usd": res.cost_usd, "n_signals": n}


def run_pass(conn: sqlite3.Connection, *, push: bool = True) -> dict:
    from ..parsers.extractors.llm_client import DailyCapExceeded, LLMClient
    from ..scheduler import market_hours
    today = market_hours.now_ist().date().isoformat()
    try:
        out = generate(conn, LLMClient())
    except DailyCapExceeded as e:
        log.info("desk_note_cap_exceeded", msg=str(e))
        return {"date": today, "skipped": "daily_cap"}
    if out.get("note"):
        conn.execute(
            "INSERT OR REPLACE INTO desk_notes (note_date, note, model, cost_usd, n_signals, "
            "created_at) VALUES (?,?,?,?,?,datetime('now'))",
            (today, out["note"], "azure", round(out["cost_usd"], 4), out["n_signals"]))
        conn.commit()
        if push:
            try:
                from ..bot.notify import ntfy_send
                ntfy_send(out["note"], channel="digest", title=f"🧠 Desk Note — {today}")
            except Exception:  # noqa: BLE001
                log.exception("desk_note_push_failed")
    report = {"date": today, "n_signals": out.get("n_signals"), "cost_usd": out.get("cost_usd"),
              "pushed": bool(out.get("note") and push), **({k: out[k] for k in ("skipped", "error") if k in out})}
    log.info("desk_note", **report)
    return report


def register_desk_note_job(scheduler, db_path: str) -> str:
    """Nightly 22:45 IST — after all EOD signal jobs (deal 16:30, smallcap 19:35, events 20:00,
    promoter 22:15, signal_paper 22:30). Trading-day + toggle gated; cap-protected."""
    from apscheduler.triggers.cron import CronTrigger

    from ..events.calendar import _feature_enabled
    from ..scheduler import market_hours
    from ..storage.db import open_db
    job_id = "desk_note"

    def _tick():
        if not market_hours.is_trading_day(market_hours.now_ist().date()):
            return
        if not _feature_enabled("desk_note", True):
            return
        conn = open_db(db_path)
        try:
            run_pass(conn)
        except Exception:
            log.exception("desk_note_failed")
        finally:
            conn.close()

    scheduler.add_job(
        _tick, trigger=CronTrigger(hour=22, minute=45, timezone=market_hours.IST),
        id=job_id, max_instances=1, coalesce=True, replace_existing=True)
    return job_id
