"""Multi-source move-cause engine — Tier 1 (internal, free, exact-dated).

See plans/MULTI_SOURCE_CAUSE_ENGINE_PLAN.md. For a move (symbol, date) we scan
our own catalyst tables in a window ENDING at the move date (events on/just
before the move — never later) and emit dated candidate causes. No web/LLM:
these are structured facts already keyed to the symbol and day.

Each connector returns Candidates; ``gather_candidates`` runs them all (fail-soft)
and stores into ``move_cause_candidates`` (the audit trail of everything that
happened around the move). Tier-2 (news/SEBI/NSE) and fusion-to-primary build on
top of this.

Snapshot-only tables (raw_price_bands, surveillance ASM/GSM) carry no per-date
history, so they're live-context, not historical connectors here.
"""
from __future__ import annotations

import datetime as dt

import structlog

log = structlog.get_logger()

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# source -> default weight (higher = more likely the PRIMARY cause). Fusion uses
# this plus recency; a result/rating/regulatory filing outranks an OI/delivery
# reading, which are corroborating flow rather than a standalone reason.
_WEIGHT = {
    "systematic": 10,      # move explained by the market/sector → no stock-specific cause
    "ex_date_artifact": 10,  # split/bonus ex-date → the "move" may be a price adjustment
    "result": 9, "rating_action": 9, "brokerage": 8, "corporate_action": 8,
    "block_deal": 8, "announcement": 7, "board_meeting": 6, "news": 5, "fno_expiry": 5,
    "52w_break": 4, "oi_spurt": 3, "delivery_spike": 3, "fii_dii": 3,
    "market_context": 2,   # market diverged → idiosyncratic; context only, low weight
}

# sector → liquid sector-ETF proxy we hold daily candles for (else market-only).
_SECTOR_ETF = {"bfsi": "BANKBEES", "it": "ITBEES"}

_DATE_FORMATS = ("%Y-%m-%d", "%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y",
                 "%d %b %Y", "%d-%m-%Y", "%Y-%m-%dT%H:%M:%S")


def _to_iso(val) -> str | None:
    """Any of our date encodings → 'YYYY-MM-DD' (IST), or None."""
    if val is None:
        return None
    if isinstance(val, (int, float)):           # epoch seconds (as_of, created_at)
        try:
            return dt.datetime.fromtimestamp(int(val), IST).date().isoformat()
        except (ValueError, OSError):
            return None
    s = str(val).strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(s[:len(fmt) + 4].strip(), fmt).date().isoformat()
        except ValueError:
            continue
    # last resort: leading 10 chars look ISO?
    return s[:10] if len(s) >= 10 and s[4] == "-" and s[7] == "-" else None


def _cand(source, cause_type, event_date, summary, url=None):
    return {"source": source, "cause_type": cause_type, "event_date": event_date,
            "summary": (summary or "")[:300], "url": url, "weight": _WEIGHT.get(cause_type, 3)}


def _in_window(d: str | None, lo: str, hi: str) -> bool:
    return bool(d) and lo <= d <= hi


# --- connectors: (conn, symbol, lo_iso, hi_iso) -> [Candidate] ---------------

def _announcements(conn, sym, lo, hi):
    out = []
    for subj, bdt, url in conn.execute(
            "SELECT subject, broadcast_dt, attachment_url FROM raw_announcements "
            "WHERE symbol=?", (sym,)):
        d = _to_iso(bdt)
        if _in_window(d, lo, hi):
            out.append(_cand("nse_announcement", "announcement", d, subj, url))
    return out


def _news_store(conn, sym, lo, hi):
    """Stored news corpus (raw_news) in the window — the PERSISTENT news memory,
    so attribution reads the growing corpus, not only an ephemeral live search.
    Capped + newest-first so a busy window doesn't flood the candidate set."""
    try:
        lo_ep = int(dt.datetime.fromisoformat(lo).replace(tzinfo=IST).timestamp())
        hi_ep = int((dt.datetime.fromisoformat(hi) + dt.timedelta(days=1))
                    .replace(tzinfo=IST).timestamp())
    except ValueError:
        return []
    out = []
    for headline, url, source, ep in conn.execute(
            "SELECT headline, url, source, published_epoch FROM raw_news "
            "WHERE symbol=? AND published_epoch >= ? AND published_epoch < ? "
            "ORDER BY published_epoch DESC LIMIT 15", (sym, lo_ep, hi_ep)):
        d = dt.datetime.fromtimestamp(ep, IST).date().isoformat() if ep else None
        out.append(_cand(f"news:{source}", "news", d, headline, url))
    return out


def _results(conn, sym, lo, hi):
    out = []
    for relating, audited, fdt in conn.execute(
            "SELECT relating_to, audited, filing_date FROM raw_financial_results "
            "WHERE symbol=?", (sym,)):
        d = _to_iso(fdt)
        if _in_window(d, lo, hi):
            out.append(_cand("nse_results", "result", d,
                             f"{relating or 'Quarterly'} results filed ({audited or ''})".strip()))
    return out


def _rating_actions(conn, sym, lo, hi):
    out = []
    for agency, action, old, new, bdt in conn.execute(
            "SELECT agency, action, old_rating, new_rating, broadcast_dt "
            "FROM raw_rating_actions WHERE symbol=?", (sym,)):
        d = _to_iso(bdt)
        if _in_window(d, lo, hi):
            chg = f"{old}→{new}" if old and new else (new or "")
            out.append(_cand("rating", "rating_action", d,
                             f"{agency or 'Rating'} {action or 'action'} {chg}".strip()))
    return out


def _corporate_actions(conn, sym, lo, hi):
    out = []
    for subj, ex in conn.execute(
            "SELECT subject, ex_date FROM raw_corporate_actions WHERE symbol=?", (sym,)):
        d = _to_iso(ex)
        if not _in_window(d, lo, hi):
            continue
        s = (subj or "").lower()
        # split / bonus / face-value change mechanically ADJUST the price on the
        # ex-date — a big "move" that day may be an artifact, not a real move.
        if any(k in s for k in ("split", "bonus", "face value", "sub-division", "sub division")):
            out.append(_cand("nse_corp_action", "ex_date_artifact", d,
                             f"Ex-date: {subj} — price adjusts mechanically today; "
                             f"the 'move' may be a corporate-action artifact, not a real move"))
        else:
            out.append(_cand("nse_corp_action", "corporate_action", d, f"Ex-date: {subj}"))
    return out


def _board_meetings(conn, sym, lo, hi):
    out = []
    for purpose, mdt in conn.execute(
            "SELECT purpose, meeting_date FROM raw_board_meetings WHERE symbol=?", (sym,)):
        d = _to_iso(mdt)
        if _in_window(d, lo, hi):
            out.append(_cand("nse_board_meeting", "board_meeting", d, f"Board meeting: {purpose}"))
    return out


def _oi_spurt(conn, sym, lo, hi):
    # raw_oi_spurts has many intraday snapshots/day → collapse to ONE candidate
    # per day (the largest |avg_oi_pct|), and only when it's a real spurt.
    best: dict[str, tuple[float, str]] = {}
    for as_of, _chg, avgpct in conn.execute(
            "SELECT as_of, change_in_oi, avg_oi_pct FROM raw_oi_spurts WHERE symbol=?", (sym,)):
        d = _to_iso(as_of)
        if not d or not _in_window(d, lo, hi) or avgpct is None or abs(avgpct) < 20:
            continue
        if d not in best or abs(avgpct) > abs(best[d][0]):
            best[d] = (avgpct, f"F&O OI spurt ({avgpct:+.0f}% vs avg)")
    return [_cand("fno_oi", "oi_spurt", d, msg) for d, (_, msg) in best.items()]


def _high_low_52w(conn, sym, lo, hi):
    out = []
    for as_of, event, lvl in conn.execute(
            "SELECT as_of, event, new_52w_level FROM raw_high_low_52w WHERE symbol=?", (sym,)):
        d = _to_iso(as_of)
        if _in_window(d, lo, hi):
            out.append(_cand("nse_52w", "52w_break", d,
                             f"New 52-week {event}" + (f" (₹{lvl:.0f})" if lvl else "")))
    return out


def _intraday_pct(conn, sym: str, date: str) -> float | None:
    """A symbol's intraday move from open on `date` (daily candle: (close−open)/open)."""
    row = conn.execute(
        "SELECT open, close FROM raw_intraday_candles WHERE symbol=? AND interval='day' "
        "AND date(ts,'unixepoch','+05:30')=?", (sym, date)).fetchone()
    if row and row[0] and row[1]:
        return round((row[1] - row[0]) / row[0] * 100, 2)
    return None


def market_context(conn, symbol: str, move_date: str, stock_move_pct: float):
    """THE idiosyncratic-vs-systematic check. Compare the stock's move to the
    market (NIFTYBEES) and its sector ETF on the same day. Returns
    (candidate | None, regime) where regime is 'systematic' (the move largely
    tracked the index/sector — no stock-specific cause needed) or 'idiosyncratic'."""
    from ..fundamentals.sectors import sector_class_for   # lazy: avoid import cycle
    mkt = _intraday_pct(conn, "NIFTYBEES", move_date)
    etf = _SECTOR_ETF.get(sector_class_for(symbol).value)
    sec = _intraday_pct(conn, etf, move_date) if etf else None
    bench = sec if sec is not None else mkt          # prefer sector, fall back to market
    bench_name = (etf or "NIFTY") if bench is sec else "NIFTY"
    if bench is None:
        return None, "unknown"
    same_dir = (bench > 0) == (stock_move_pct > 0)
    excess = round(stock_move_pct - bench, 2)
    # systematic only if the move TRACKED the benchmark: same direction, benchmark
    # itself moved, and the residual (idiosyncratic excess) is small relative to the
    # move. A stock that fell ~2× the market has a real idiosyncratic component →
    # idiosyncratic, even though the market also fell.
    systematic = (same_dir and abs(bench) >= 0.5
                  and abs(excess) <= max(0.75, 0.4 * abs(stock_move_pct)))
    if systematic:
        return (_cand("market", "systematic", move_date,
                      f"Moved with the market/sector: {bench_name} {bench:+.1f}% "
                      f"(stock {stock_move_pct:+.1f}%, excess {excess:+.1f}%) — not stock-specific"),
                "systematic")
    return (_cand("market", "market_context", move_date,
                  f"{bench_name} {bench:+.1f}% vs stock {stock_move_pct:+.1f}% "
                  f"(excess {excess:+.1f}%) — move is idiosyncratic"),
            "idiosyncratic")


def _block_deals(conn, sym, lo, hi):
    out = []
    for dtype, ddate, client, side, qty in conn.execute(
            "SELECT deal_type, deal_date, client_name, buy_sell, quantity "
            "FROM raw_large_deals WHERE symbol=?", (sym,)):
        d = _to_iso(ddate)
        if _in_window(d, lo, hi):
            q = f"{qty:,}" if isinstance(qty, int) else qty
            out.append(_cand("block_deal", "block_deal", d,
                             f"{(dtype or '').title()} deal: {client} {side} {q}"))
    return out


def _fii_dii(conn, sym, lo, hi):
    # market-wide flow context (not stock-specific): a heavy FII net sell/buy day
    out = []
    for ddate, cat, net in conn.execute(
            "SELECT date, category, net_value FROM raw_fii_dii"):
        d = _to_iso(ddate)
        if _in_window(d, lo, hi) and net is not None and abs(net) >= 2000:   # ₹2000cr+ day
            out.append(_cand("fii_dii", "fii_dii", d,
                             f"{cat} net {'sold' if net < 0 else 'bought'} ₹{abs(net):,.0f} cr (market-wide)"))
    return out


def _fno_expiry(conn, sym, lo, hi):
    # deterministic: NSE monthly expiry = last Thursday of the month (weekly = any
    # Thursday). Expiry days carry index/F&O-driven technical moves.
    out = []
    d0 = dt.date.fromisoformat(hi)
    for off in range((dt.date.fromisoformat(hi) - dt.date.fromisoformat(lo)).days + 1):
        day = d0 - dt.timedelta(days=off)
        if day.weekday() == 3:   # Thursday
            nxt_thu = day + dt.timedelta(days=7)
            kind = "monthly" if nxt_thu.month != day.month else "weekly"
            out.append(_cand("fno", "fno_expiry", day.isoformat(),
                             f"F&O {kind} expiry day (index/derivatives-driven)"))
    return out


def _brokerage(conn, sym, lo, hi):
    """Equity broker/analyst calls (Jefferies, JPMorgan, Motilal, …) — buy/sell/
    target/initiate/upgrade. Distinct from credit ratings. From raw_analyst_ratings
    (Moneycontrol broker-reco feed); foreign houses also surface via the news source."""
    out = []
    for broker, new_call, old_call, new_tgt, pub, url in conn.execute(
            "SELECT brokerage, new_call, old_call, new_target, published_at, source_url "
            "FROM raw_analyst_ratings WHERE symbol=?", (sym,)):
        d = _to_iso(pub)
        if _in_window(d, lo, hi):
            chg = f" (was {old_call})" if old_call and old_call != new_call else ""
            tgt = f", target ₹{new_tgt:.0f}" if new_tgt else ""
            out.append(_cand("brokerage", "brokerage", d,
                             f"{broker or 'Broker'}: {new_call or 'call'}{tgt}{chg}".strip(), url))
    return out


def _delivery_spike(conn, sym, lo, hi):
    out = []
    for sd, z, trend in conn.execute(
            "SELECT session_date, delivery_ratio_z_score, delivery_trend "
            "FROM delivery_conviction WHERE symbol=?", (sym,)):
        d = _to_iso(sd)
        if _in_window(d, lo, hi) and (z or 0) >= 1.5:   # only a real spike
            out.append(_cand("delivery", "delivery_spike", d,
                             f"Delivery spike (z={z:.1f}, {trend})"))
    return out


CONNECTORS = (_announcements, _results, _rating_actions, _brokerage,
              _corporate_actions, _board_meetings, _block_deals, _fii_dii,
              _fno_expiry, _oi_spurt, _high_low_52w, _delivery_spike, _news_store)


def gather_candidates(conn, symbol: str, move_date: str, *, window_days: int = 4) -> list[dict]:
    """All internal candidate causes in [move_date − window, move_date]."""
    md = dt.date.fromisoformat(move_date)
    lo = (md - dt.timedelta(days=window_days)).isoformat()
    hi = move_date
    cands: list[dict] = []
    for fn in CONNECTORS:
        try:
            cands.extend(fn(conn, symbol, lo, hi))
        except Exception as e:  # noqa: BLE001 — one source down must not kill the rest
            log.info("connector_failed", connector=fn.__name__, symbol=symbol, error=str(e))
    # newest first, then heaviest source
    cands.sort(key=lambda c: (c["event_date"], c["weight"]), reverse=True)
    return cands


# Context flags, NOT catalysts: F&O expiry / a market-wide move co-occur with a
# big move by calendar/beta coincidence. They must never win "primary cause" — a
# move whose only tags are context has no idiosyncratic catalyst ('none').
CONTEXT_ONLY = frozenset({"fno_expiry", "market_context"})


def primary_catalyst(conn, symbol: str, move_date: str, *, window_days: int = 4) -> str:
    """Highest-weight internal CATALYST bucket for a move ('none' if no real
    catalyst). Splits the catch-all 'announcement' into earnings-result vs other
    (orders/contracts/deals) — the non-earnings half is the multi-factor thesis.
    Single source of truth for the bucket used by the paper log + move digest."""
    from ..fundamentals.from_results import is_result_subject   # lazy: avoid import cycle
    cands = gather_candidates(conn, symbol, move_date, window_days=window_days)
    catalysts = [c for c in cands if c["cause_type"] not in CONTEXT_ONLY]
    if not catalysts:
        return "none"
    top = max(catalysts, key=lambda c: c["weight"])
    if top["cause_type"] == "announcement":
        return "ann:result" if is_result_subject(top["summary"]) else "ann:other"
    return top["cause_type"]


# --- external (best-effort) --------------------------------------------------

def _bse_scrip(conn, symbol: str) -> str | None:
    """Symbol → BSE numeric scrip code, via ISIN: our raw_quote_metadata holds the
    NSE symbol's ISIN; raw_bse_scrip_master (loaded by scripts/load_bse_scrip_master.py
    from BSE's List_of_companies.csv) maps ISIN → BSE code. Returns None if either
    side is missing (scrip master not loaded / no ISIN) — bse_announcements then
    fails soft."""
    try:
        row = conn.execute(
            "SELECT m.bse_code FROM raw_quote_metadata q "
            "JOIN raw_bse_scrip_master m ON q.isin = m.isin WHERE q.symbol=?",
            (symbol,)).fetchone()
        return str(row[0]) if row and row[0] else None
    except Exception:  # noqa: BLE001 — scrip master table not present yet
        return None


def bse_announcements(client, conn, symbol: str, move_date: str, *, window_days: int = 4) -> list[dict]:
    """BSE corporate announcements in the window. The BSE API IS reachable, but
    needs the numeric scrip code — so this fails soft (returns []) until a scrip
    master is wired. Kept here so the external framework is complete and BSE is
    one resolver away from live."""
    scrip = _bse_scrip(conn, symbol)
    if not scrip:
        return []
    md = dt.date.fromisoformat(move_date)
    lo, hi = (md - dt.timedelta(days=window_days)), md
    try:
        import json as _json
        r = client.get(
            "https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w",
            params={"strCat": "-1", "strPrevDate": lo.strftime("%Y%m%d"),
                    "strToDate": hi.strftime("%Y%m%d"), "strScrip": scrip,
                    "strSearch": "P", "strType": "C"},
            headers={"Referer": "https://www.bseindia.com/"})
        rows = _json.loads(r.text).get("Table", []) if r.text.strip().startswith("{") else []
    except Exception:  # noqa: BLE001 — best-effort
        return []
    out = []
    for row in rows[:10]:
        d = _to_iso(row.get("News_submission_dt") or row.get("DT_TM"))
        if _in_window(d, lo.isoformat(), hi.isoformat()):
            out.append(_cand("bse", "announcement", d,
                             row.get("NEWSSUB") or row.get("HEADLINE") or "BSE announcement",
                             row.get("ATTACHMENTNAME")))
    return out


def ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS move_cause_candidates (
            symbol TEXT, date TEXT, source TEXT, cause_type TEXT,
            event_date TEXT, summary TEXT, url TEXT, weight INTEGER,
            PRIMARY KEY (symbol, date, source, cause_type, event_date, summary)
        )""")


def store_candidates(conn, symbol: str, move_date: str, cands: list[dict]) -> None:
    ensure_table(conn)
    conn.executemany(
        "INSERT OR REPLACE INTO move_cause_candidates "
        "(symbol, date, source, cause_type, event_date, summary, url, weight) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [(symbol, move_date, c["source"], c["cause_type"], c["event_date"],
          c["summary"], c["url"], c["weight"]) for c in cands])
    conn.commit()
