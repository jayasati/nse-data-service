"""Persist extracted quarterly financials + compute growth (Phase 5, E1).

Wires the vision-first ``parsers.financial_extractor`` into production: finds
result-PDF announcements whose text is already extracted, runs the financial
extractor on the PDF, and stores the headline P&L numbers in
``extracted_financials`` (one row per scope). That table is the per-quarter
history the earnings engine reads to compute YoY/QoQ growth — the
fundamental-surprise input.

    from nse_data.storage.db import open_db
    conn = open_db("data/nse.db")
    run_extract_pass(conn, limit=20, use_llm=True)        # backfill
    quarter_growth(conn, "TCS", "2026-03-31")             # {'yoy_revenue_pct': 16.8, ...}
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import sqlite3
import time

import structlog

from nse_data.parsers.state import State

log = structlog.get_logger()

# Headline fields we persist, in column order. EPS is per-share rupees; the rest
# are crore. Mirrors financial_extractor's canonical names.
AMOUNT_FIELDS = (
    "revenue_cr", "other_income_cr", "total_income_cr", "total_expenses_cr",
    "pbt_cr", "tax_cr", "pat_cr", "total_comprehensive_income_cr",
    "eps_basic", "eps_diluted", "cfo_cr",
    "depreciation_cr", "finance_cost_cr",   # non-bank operating-EBITDA inputs (P3)
    # cost structure (gross margin & bottom-up EBITDA) + earnings-quality lines
    # + below-the-line (migration 072). NULL when the filing/XBRL omits them.
    "cost_of_materials_cr", "purchases_of_stock_cr", "change_in_inventory_cr",
    "employee_cost_cr", "other_expenses_cr",
    "exceptional_items_cr", "pbt_before_exceptional_cr",
    "current_tax_cr", "deferred_tax_cr",
    "share_of_associates_cr", "other_comprehensive_income_cr",
    # balance-sheet working capital (migration 073) — capgoods balloon guard
    "trade_receivables_cr", "inventories_cr", "trade_payables_cr",
)

# BFSI operating lines (Week 17.5, S2) — persisted alongside the headline fields
# when the extractor read them (bank/NBFC filings). NULL for non-BFSI symbols.
# gross/net_npa_pct are ratios, the rest crore; all flow through fields.get().
BFSI_FIELDS = (
    "interest_earned_cr", "interest_expended_cr", "net_interest_income_cr",
    "operating_profit_cr", "provisions_cr", "profit_on_sale_of_investments_cr",
    "gross_npa_pct", "net_npa_pct", "slippages_cr",
    # bank health (migration 072): opex, NPA amounts, capital adequacy, ROA.
    "operating_expenses_cr", "gross_npa_cr", "net_npa_cr",
    "cet1_ratio", "return_on_assets",
)

# Balance-sheet base (migration 081) — period-end instant from the result XBRL,
# present ~half-yearly. Feeds the ratio factors (ROE/ROCE/current-ratio/
# asset-turnover/P-B). borrowings_cr/cash_cr are best-effort. All NULL otherwise.
BALANCE_SHEET_FIELDS = (
    "equity_cr", "total_assets_cr", "current_assets_cr", "current_liabilities_cr",
    "total_liabilities_cr", "borrowings_cr", "cash_cr",
)

# A result filing's announcement subject. Board-meeting *outcomes* often carry
# the P&L too; if the extractor finds no table we simply store nothing for them.
_RESULT_SUBJECT_RE = re.compile(
    r"financial result|audited financial|unaudited financial|quarterly result|"
    r"outcome of board meeting",
    re.I,
)


def is_result_subject(subject: str | None) -> bool:
    return bool(_RESULT_SUBJECT_RE.search(subject or ""))


# --------------------------------------------------------------------------- #
# persistence
# --------------------------------------------------------------------------- #

def persist_extraction(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    period_ending: str,
    scope: str,
    fields: dict[str, float],
    units_phrase: str | None,
    confidence: float,
    strategy: str,
    source_fingerprint: str | None,
    broadcast_dt: str | None,
    relating_to: str | None = None,
    financial_year: str | None = None,
    growth: dict | None = None,
    narrative: dict | None = None,
    now: int | None = None,
) -> None:
    """Upsert one (symbol, period_ending, scope) row into extracted_financials.

    ``growth`` is the extractor's PDF-derived YoY/QoQ dict for this scope; it is
    stored as JSON in ``growth_json`` so the quality signal (S3) can read the
    divergence at detection time without needing prior-quarter history.
    ``narrative`` is the press-release signal dict (``NarrativeFields.as_dict()``,
    P7) stored as ``narrative_json`` for the same reason."""
    now = now if now is not None else int(time.time())
    growth_json = json.dumps(growth, sort_keys=True) if growth else None
    narrative_json = json.dumps(narrative, sort_keys=True) if narrative else None
    cols = (
        "symbol", "period_ending", "scope", "relating_to", "financial_year",
        *AMOUNT_FIELDS, *BFSI_FIELDS, *BALANCE_SHEET_FIELDS,
        "growth_json", "narrative_json",
        "units_phrase", "extract_confidence", "strategy",
        "source_fingerprint", "broadcast_dt", "extracted_at",
    )
    vals = [
        symbol, period_ending, scope, relating_to, financial_year,
        *[fields.get(f) for f in AMOUNT_FIELDS],
        *[fields.get(f) for f in BFSI_FIELDS],
        *[fields.get(f) for f in BALANCE_SHEET_FIELDS],
        growth_json, narrative_json,
        units_phrase, confidence, strategy,
        source_fingerprint, broadcast_dt, now,
    ]
    placeholders = ", ".join(["?"] * len(cols))
    conn.execute(
        f"INSERT OR REPLACE INTO extracted_financials ({', '.join(cols)}) "
        f"VALUES ({placeholders})",
        vals,
    )
    conn.commit()


def narrative_for_fingerprint(
    conn: sqlite3.Connection, fingerprint: str | None, *, use_llm: bool = True,
) -> dict | None:
    """P7: lift the press-release signals from one filing's already-extracted
    text (``raw_announcements.pdf_text``). Single-document helper —
    ``narrative_for_filing`` (which also reads sibling press releases / decks)
    supersedes it in the live flow. Never raises: a missing or unreadable
    narrative must not block the P&L extraction it supplements."""
    if not fingerprint:
        return None
    try:
        row = conn.execute(
            "SELECT pdf_text FROM raw_announcements WHERE fingerprint = ?",
            (fingerprint,),
        ).fetchone()
        if not row or not row[0]:
            return None
        from nse_data.parsers.narrative import narrative_fields

        d, _cost = narrative_fields(row[0], use_llm=use_llm)
        return d
    except Exception:  # noqa: BLE001 — narrative is best-effort by design
        log.warning("narrative_extract_failed", fingerprint=fingerprint, exc_info=True)
        return None


# Sibling attachments that carry the narrative the result PDF's own text may
# not: the press release (the cleanest source — KPIs restated in prose) and the
# investor deck (noisiest; often image-only → vision). All are 'medium'
# priority in config/priority.yaml, so their text is already collected.
# Priority: press release (0) beats the result PDF (1) beats the deck (2).
_SIBLING_SUBJECTS: dict[str, int] = {
    "Press Release": 0,
    "Press Release (Revised)": 0,
    "Investor Presentation": 2,
}
_RESULT_PDF_PRIORITY = 1
# A press release lands with or shortly after the outcome filing; same-day
# window, asymmetric: −2h (occasionally filed just before) to +6h.
_SIBLING_WINDOW = (-2 * 3600, 6 * 3600)


def _parse_broadcast_dt(s: str | None) -> _dt.datetime | None:
    """NSE 'DD-MMM-YYYY HH:MM[:SS]' → datetime, None on failure."""
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M"):
        try:
            return _dt.datetime.strptime((s or "").strip()[:20], fmt)
        except ValueError:
            continue
    return None


def narrative_for_filing(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    fingerprint: str | None = None,
    broadcast_dt: str | None = None,
    use_llm: bool = True,
    prev: dict | None = None,
) -> dict | None:
    """P7: the merged narrative for one result filing — the result PDF's own
    text plus sibling Press Release / Investor Presentation attachments filed
    in the same window. Field-wise merge, first non-None by source priority
    (press release → result PDF → deck). Image-only decks go through the
    vision read when ``use_llm``.

    ``prev`` (the previously stored narrative dict) short-circuits the LLM
    spend: when the source set hasn't changed since it was computed (matched
    via the ``_source_fps`` meta key), ``prev`` is returned untouched — so the
    refresh pass only pays for filings that actually grew a new sibling.
    Best-effort: returns ``prev``/None on any internal failure, never raises."""
    try:
        sources: list[tuple[int, str, str, str | None, str | None]] = []
        if fingerprint:
            own = conn.execute(
                "SELECT pdf_text, broadcast_dt, pdf_path FROM raw_announcements "
                "WHERE fingerprint = ?",
                (fingerprint,),
            ).fetchone()
            if own:
                broadcast_dt = broadcast_dt or own[1]
                if own[0]:
                    sources.append((_RESULT_PDF_PRIORITY, "result PDF", fingerprint, own[0], None))

        anchor = _parse_broadcast_dt(broadcast_dt)
        if anchor is not None:
            marks = ", ".join("?" * len(_SIBLING_SUBJECTS))
            rows = conn.execute(
                f"SELECT fingerprint, subject, broadcast_dt, pdf_text, pdf_path "
                f"FROM raw_announcements WHERE symbol = ? AND subject IN ({marks})",
                (symbol, *_SIBLING_SUBJECTS),
            ).fetchall()
            for fp, subj, bdt, text, pdf_path in rows:
                dt = _parse_broadcast_dt(bdt)
                if dt is None or not text and not pdf_path:
                    continue
                delta = (dt - anchor).total_seconds()
                if _SIBLING_WINDOW[0] <= delta <= _SIBLING_WINDOW[1]:
                    sources.append((_SIBLING_SUBJECTS[subj], subj, fp, text, pdf_path))

        if not sources:
            return None
        source_fps = sorted(fp for _, _, fp, _, _ in sources)
        if prev and prev.get("_source_fps") == source_fps:
            return prev   # source set unchanged — keep the stored read, spend nothing

        from nse_data.parsers.narrative import extract_narrative_vision, narrative_fields

        sector = None
        try:
            from nse_data.fundamentals.sectors import sector_class_for
            sector = sector_class_for(symbol).value
        except Exception:  # noqa: BLE001 — a sector hint is optional
            pass

        merged: dict = {}
        used: list[str] = []
        cost = 0.0
        for _prio, label, _fp, text, pdf_path in sorted(sources, key=lambda s: s[0]):
            d = None
            if text:
                d, c = narrative_fields(text, use_llm=use_llm, symbol=symbol, sector=sector)
                cost += c
            elif pdf_path and use_llm:
                # image-only sibling (deck with no text layer) → vision read
                try:
                    from pathlib import Path
                    d, c = extract_narrative_vision(
                        Path(pdf_path).read_bytes(), symbol=symbol, sector=sector,
                    )
                    cost += c
                except OSError:
                    d = None
            if not d:
                continue
            used.append(label)
            for k, v in d.items():
                if v is not None and merged.get(k) is None:
                    merged[k] = v

        if not merged:
            return None
        merged["_sources"] = used
        merged["_source_fps"] = source_fps
        if cost:
            log.info("narrative_llm_read", symbol=symbol, sources=used, cost_usd=round(cost, 4))
        return merged
    except Exception:  # noqa: BLE001 — narrative is best-effort by design
        log.warning("narrative_merge_failed", symbol=symbol, exc_info=True)
        return prev


def _update_announcement(conn: sqlite3.Connection, fingerprint: str, fields: dict) -> None:
    """Update raw_announcements (same pattern as parsers/job.py:_update_row)."""
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE raw_announcements SET {set_clause} WHERE fingerprint = ?",
        list(fields.values()) + [fingerprint],
    )
    conn.commit()


def _state_for(stored: int, strategy: str, confidence: float) -> str:
    """Terminal pdf_status after a financial-extraction attempt."""
    if stored == 0:
        return State.EXTRACTION_FAILED
    if strategy == "vision":
        return State.EXTRACTED_VIA_VISION
    if confidence >= 0.75:
        return State.EXTRACTED
    return State.EXTRACTED_LOW_CONF


def extract_and_store(
    conn: sqlite3.Connection,
    *,
    fingerprint: str,
    symbol: str,
    subject: str | None,
    broadcast_dt: str | None,
    pdf_path: str,
    use_llm: bool = True,
    now: int | None = None,
    session=None,
) -> dict:
    """Run the financial extractor on one PDF and persist its numbers.

    Stores up to two rows (standalone + consolidated) when ``period_ending`` is
    known, and advances the announcement's pdf_status. Returns a summary dict.
    """
    now = now if now is not None else int(time.time())
    from nse_data.parsers.financial_extractor import extract

    # XBRL-first: the company's own structured submission is authoritative and
    # free. Use it when a parseable XBRL exists; fall back to the LLM vision
    # read only when it doesn't (older PDF-only filings, missing XBRL).
    res = None
    try:
        from nse_data.parsers.xbrl_extract import extract_via_xbrl
        res = extract_via_xbrl(conn, symbol, broadcast_dt, session=session)
    except Exception:  # noqa: BLE001 — XBRL trouble must never block the LLM fallback
        res = None
    if res is None or not (res.fields or res.consolidated):
        res = extract(
            pdf_path, use_llm_fallback=use_llm,
            symbol=symbol, subject=subject, broadcast_dt=broadcast_dt,
        )

    stored = 0
    if res.period_ending:
        # P7: the narrative (guidance/volumes/order book/FDA/…) from the filing
        # text PLUS sibling press release / deck — extracted once, stored on
        # every scope row. Late-arriving siblings are folded in by
        # refresh_narratives on the intraday tick.
        narrative = narrative_for_filing(
            conn, symbol=symbol, fingerprint=fingerprint,
            broadcast_dt=broadcast_dt, use_llm=use_llm,
        )
        scopes = (
            ("standalone", res.fields, res.growth),
            ("consolidated", res.consolidated, res.growth_consolidated),
        )
        for scope, block, growth in scopes:
            if block:
                persist_extraction(
                    conn, symbol=symbol, period_ending=res.period_ending, scope=scope,
                    fields=block, units_phrase=res.units_phrase, confidence=res.confidence,
                    strategy=res.strategy, source_fingerprint=fingerprint,
                    broadcast_dt=broadcast_dt, growth=growth, narrative=narrative, now=now,
                )
                stored += 1

    _update_announcement(conn, fingerprint, {
        "extraction_confidence": res.confidence,
        "extraction_strategy": res.strategy,
        "extraction_attempted_at": now,
        "pdf_status": _state_for(stored, res.strategy, res.confidence),
        "pdf_status_updated_at": now,
    })
    return {
        "stored": stored, "strategy": res.strategy, "confidence": res.confidence,
        "cost_usd": res.llm_cost_usd, "period_ending": res.period_ending,
    }


_UNSET = object()   # "caller didn't pass universe" — resolve via focus_universe()


def run_extract_pass(
    conn: sqlite3.Connection,
    *,
    limit: int = 20,
    use_llm: bool = True,
    now: int | None = None,
    universe=_UNSET,
    session=None,
) -> dict:
    """Backfill: extract+store financials for result PDFs not yet processed.

    Selects announcements at ``text_extracted`` whose subject looks like a result
    and that have no extracted_financials rows yet. Bounded by ``limit`` (each
    PDF is one LLM call, so keep batches modest against the daily spend cap).

    Focus-universe gated: results for symbols outside the top-1000 are skipped so
    LLM budget isn't spent on off-universe/defunct names (the text-extracted
    backlog predates the parser gate). `universe` defaults to focus_universe()
    (the scheduled callers stay gated); pass None to disable, or a set to scope.
    """
    if universe is _UNSET:
        from nse_data.parsers.job import focus_universe
        universe = focus_universe()

    candidates = conn.execute(
        "SELECT fingerprint, symbol, subject, broadcast_dt, pdf_path "
        "FROM raw_announcements "
        "WHERE pdf_status = ? AND pdf_path IS NOT NULL "
        "AND fingerprint NOT IN ("
        "  SELECT source_fingerprint FROM extracted_financials "
        "  WHERE source_fingerprint IS NOT NULL) "
        "ORDER BY broadcast_epoch DESC, broadcast_dt DESC",
        (State.TEXT_EXTRACTED,),
    ).fetchall()

    processed = stored = 0
    total_cost = 0.0
    report: list[dict] = []
    for fp, sym, subj, bdt, path in candidates:
        if processed >= limit:
            break
        if not is_result_subject(subj):
            continue
        if universe is not None and (sym or "").upper() not in universe:
            continue   # off-universe — don't spend LLM budget
        r = extract_and_store(
            conn, fingerprint=fp, symbol=sym, subject=subj,
            broadcast_dt=bdt, pdf_path=path, use_llm=use_llm, now=now, session=session,
        )
        processed += 1
        stored += r["stored"]
        total_cost += r["cost_usd"]
        report.append({"symbol": sym, **r})
        log.info("extracted_financials", symbol=sym, **r)
    return {"processed": processed, "stored": stored, "cost_usd": total_cost, "rows": report}


# Fields whose change between the intraday LLM read and the after-close XBRL is
# "material" enough to flag/alert — the headline numbers a trade keys off.
_MATERIAL_FIELDS = ("revenue_cr", "total_income_cr", "pat_cr", "eps_basic")
_MATERIAL_PCT = 3.0   # >3% move, or a sign flip, is material


def _read_row(conn: sqlite3.Connection, symbol: str, period: str, scope: str) -> dict:
    """Stored fields + narrative for one (symbol, period, scope). Does NOT touch
    conn.row_factory (a global mutation would surprise callers)."""
    cur = conn.execute(
        "SELECT * FROM extracted_financials WHERE symbol=? AND period_ending=? AND scope=?",
        (symbol, period, scope),
    )
    r = cur.fetchone()
    return {d[0]: v for d, v in zip(cur.description, r)} if r else {}


def _field_diffs(old: dict, new: dict) -> list[tuple]:
    """(field, old, new, material) for numeric fields that changed."""
    diffs = []
    for f, nv in new.items():
        if not isinstance(nv, (int, float)):
            continue
        ov = old.get(f)
        if ov is None or abs(float(ov) - float(nv)) < 0.01:
            continue
        ref = max(abs(float(ov)), abs(float(nv)), 1e-9)
        material = f in _MATERIAL_FIELDS and (
            (float(ov) > 0) != (float(nv) > 0)                       # sign flip
            or abs(float(ov) - float(nv)) / ref * 100.0 > _MATERIAL_PCT
        )
        diffs.append((f, ov, nv, material))
    return diffs


def reconcile_xbrl_pass(
    conn: sqlite3.Connection, *, session, limit: int = 50, now: int | None = None,
    sender=None, token: str | None = None, chat_id: str | None = None,
) -> dict:
    """After-close: replace intraday LLM-extracted financials with the
    authoritative XBRL once NSE broadcasts it (hours after the PDF).

    For each (symbol, period_ending) stored via a non-XBRL strategy, re-resolve
    the XBRL; when available, overwrite the numbers (preserving the press-release
    narrative, since XBRL carries none) and, if a headline figure changed
    materially, send a correction note. Idempotent: once a row is strategy=xbrl
    it's no longer a candidate.
    """
    now = now if now is not None else int(time.time())
    from nse_data.parsers.xbrl_extract import extract_via_xbrl
    targets = conn.execute(
        "SELECT DISTINCT symbol, period_ending FROM extracted_financials "
        "WHERE strategy IS NOT NULL AND strategy != 'xbrl' "
        "ORDER BY period_ending DESC",
    ).fetchall()

    checked = corrected = 0
    report: list[dict] = []
    for symbol, period in targets:
        if checked >= limit:
            break
        meta = conn.execute(
            "SELECT source_fingerprint, broadcast_dt FROM extracted_financials "
            "WHERE symbol=? AND period_ending=? AND strategy!='xbrl' LIMIT 1",
            (symbol, period),
        ).fetchone()
        src_fp, bdt = (meta[0], meta[1]) if meta else (None, None)
        try:
            res = extract_via_xbrl(conn, symbol, bdt, session=session)
        except Exception:  # noqa: BLE001
            res = None
        checked += 1
        if res is None or res.period_ending != period:
            continue   # XBRL not broadcast yet (or wrong quarter) — try again later

        scope_diffs: list = []
        for scope, block in (("standalone", res.fields), ("consolidated", res.consolidated)):
            if not block:
                continue
            old = _read_row(conn, symbol, period, scope)
            diffs = _field_diffs(old, block)
            narrative = None
            if old.get("narrative_json"):
                try:
                    narrative = json.loads(old["narrative_json"])   # keep press-release read
                except (ValueError, TypeError):
                    narrative = None
            persist_extraction(
                conn, symbol=symbol, period_ending=period, scope=scope,
                fields=block, units_phrase="xbrl", confidence=1.0, strategy="xbrl",
                source_fingerprint=src_fp, broadcast_dt=bdt, narrative=narrative, now=now,
            )
            if diffs:
                scope_diffs.append((scope, diffs))
        if scope_diffs:
            corrected += 1
            material = any(m for _, ds in scope_diffs for *_, m in ds)
            report.append({"symbol": symbol, "period": period,
                           "material": material, "diffs": scope_diffs})
            log.info("xbrl_reconciled", symbol=symbol, period=period, material=material)
            if material and sender is not None:
                _send_correction(sender, token, chat_id, symbol, period, scope_diffs)
    out = {"checked": checked, "corrected": corrected, "rows": report}
    log.info("reconcile_xbrl_pass", checked=checked, corrected=corrected)
    return out


def _send_correction(sender, token, chat_id, symbol: str, period: str, scope_diffs: list) -> None:
    """Telegram note: XBRL corrected an intraday-LLM result (material change)."""
    import os
    lines = [f"📒 {symbol} — Result figures corrected by XBRL ({period})"]
    for scope, diffs in scope_diffs:
        mats = [(f, o, n) for f, o, n, m in diffs if m]
        if not mats:
            continue
        lines.append(f"  {scope}:")
        for f, o, n in mats:
            lines.append(f"    {f}: {o} -> {n}")
    lines.append("(intraday LLM read superseded by the company's XBRL)")
    thread = os.environ.get("TELEGRAM_TOPIC_EARNINGS")
    thread_id = int(thread) if thread and thread.lstrip("-").isdigit() else None
    try:
        sender(token, chat_id, "\n".join(lines), thread_id)
    except Exception:  # noqa: BLE001 — an alert failure must not abort reconciliation
        log.exception("correction_alert_failed", symbol=symbol)


# Per-night batch bound. Each PDF is one gpt-4o call; 40 keeps a heavy results
# night well under the LLMClient daily cap ($10). Unprocessed results carry over
# to the next night (the pass only picks text_extracted rows not yet stored).
EXTRACT_BATCH_LIMIT = 40

# Intraday cadence: results filed DURING market hours must have their financials
# extracted promptly so the post-result reaction's confidence can fold in the
# fundamental surprise within the dispatcher's ~15-min re-score window. Small
# per-tick batch (most ticks are no-ops — only new text_extracted results match).
INTRADAY_EXTRACT_INTERVAL_MIN = 5
INTRADAY_EXTRACT_BATCH = 10


# How far back the refresh re-checks stored narratives for late siblings. The
# detector only re-reads rows for 30 min (its extracted_at lookback), but the
# alert card re-reads at send time and the press release can trail the result
# by an hour or two — 2h covers both at negligible cost (the _source_fps cache
# means a filing whose source set hasn't grown costs zero LLM calls).
NARRATIVE_REFRESH_LOOKBACK_SECS = 2 * 3600


def refresh_narratives(
    conn: sqlite3.Connection,
    *,
    use_llm: bool = True,
    lookback_secs: int = NARRATIVE_REFRESH_LOOKBACK_SECS,
    now: int | None = None,
) -> dict:
    """P7: fold late-arriving sibling attachments into stored narratives.

    A press release / deck often lands minutes-to-hours AFTER the result PDF
    whose extraction froze ``narrative_json``. For recently extracted rows,
    recompute the merged narrative and UPDATE the row when it changed —
    without touching ``extracted_at`` (the detector's recency gate is not
    ours to re-arm). A verdict that flips inside the detector's 30-min
    lookback fires on its next tick; beyond that the card still benefits."""
    now = now if now is not None else int(time.time())
    rows = conn.execute(
        "SELECT DISTINCT symbol, period_ending, source_fingerprint, broadcast_dt, "
        "narrative_json FROM extracted_financials "
        "WHERE extracted_at >= ? AND source_fingerprint IS NOT NULL",
        (now - lookback_secs,),
    ).fetchall()
    checked = updated = 0
    for symbol, period_ending, fp, bdt, njson in rows:
        checked += 1
        prev = None
        if njson:
            try:
                prev = json.loads(njson)
            except (ValueError, TypeError):
                prev = None
        new = narrative_for_filing(
            conn, symbol=symbol, fingerprint=fp, broadcast_dt=bdt,
            use_llm=use_llm, prev=prev,
        )
        if new is not None and new != prev:
            conn.execute(
                "UPDATE extracted_financials SET narrative_json = ? "
                "WHERE symbol = ? AND period_ending = ?",
                (json.dumps(new, sort_keys=True), symbol, period_ending),
            )
            updated += 1
    conn.commit()
    return {"checked": checked, "updated": updated}


def register_extract_runner(scheduler, db_path: str) -> str:
    """Nightly 21:00 IST: extract financials from the day's result PDFs.

    Runs after the calendar (20:00) / pre-screen (20:15) and late enough that the
    day's result PDFs have been downloaded + text-extracted. Trading-day gated;
    catch-up safe (a missed night's results are picked up the next run)."""
    from apscheduler.triggers.cron import CronTrigger

    from nse_data.scheduler import market_hours
    from nse_data.storage.db import open_db

    job_id = "extract_financials"

    def _tick():
        if not market_hours.is_trading_day(market_hours.now_ist().date()):
            return
        from nse_data.session.manager import SessionManager
        conn = open_db(db_path)
        session = SessionManager()   # XBRL URL lookup + correction sends
        try:
            report = run_extract_pass(conn, limit=EXTRACT_BATCH_LIMIT, use_llm=True,
                                      session=session)
            log.info("extract_financials_job", **report)
            # After close the structured XBRL is broadcast: replace any intraday
            # LLM reads with the authoritative numbers and alert on material fixes.
            try:
                from nse_data.bot.dispatcher import load_telegram_config, send_telegram
                token, chat_id = load_telegram_config()
                rec = reconcile_xbrl_pass(conn, session=session, limit=EXTRACT_BATCH_LIMIT,
                                          sender=send_telegram, token=token, chat_id=chat_id)
                log.info("reconcile_xbrl_job", **{k: rec[k] for k in ("checked", "corrected")})
            except Exception:
                log.exception("reconcile_xbrl_job_failed")
        except Exception:
            log.exception("extract_financials_job_failed")
        finally:
            conn.close()

    scheduler.add_job(
        _tick, trigger=CronTrigger(hour=21, minute=0, timezone=market_hours.IST),
        id=job_id, max_instances=1, coalesce=True, replace_existing=True,
    )
    return job_id


# Fast lane: process freshly-filed RESULT PDFs end-to-end within ~1 min of
# arrival during market hours, so the surprise is ready when the 5-10 min
# reaction fires. Same download/extraction logic as the general parser — only
# the latency changes, never the accuracy.
FAST_LANE_INTERVAL_SEC = 60
FAST_LANE_BATCH = 5


def register_fast_result_lane(scheduler, db_path: str, session) -> str:
    """Every minute during market hours: run the WHOLE pipeline (download ->
    text-extract -> financial-extract) on just-arrived result announcements,
    newest first. Collapses the collector->parser->extract latency for results
    to ~1 min so a mid-session reaction can be scored with the real surprise.

    ``session`` is the shared SessionManager (for the PDF download). Idle ticks
    (no fresh results) are cheap no-ops; per-row errors are isolated."""
    from apscheduler.triggers.interval import IntervalTrigger

    from nse_data.parsers.job import process_one_row
    from nse_data.scheduler.jobs import ARCHIVE_ROOT
    from nse_data.scheduler.market_hours import is_market_open
    from nse_data.storage.db import open_db

    job_id = "fast_result_lane"

    def _tick():
        if not is_market_open():
            return
        conn = open_db(db_path)
        try:
            conn.row_factory = __import__("sqlite3").Row
            rows = conn.execute(
                "SELECT fingerprint, symbol, subject, attachment_url, broadcast_dt "
                "FROM raw_announcements WHERE pdf_status IN ('pending', 'classified') "
                "ORDER BY broadcast_epoch DESC, broadcast_dt DESC LIMIT 50"
            ).fetchall()
            results = [r for r in rows if is_result_subject(r["subject"])][:FAST_LANE_BATCH]
            processed = stored = 0
            for r in results:
                try:
                    state = process_one_row(conn, session, dict(r), ARCHIVE_ROOT)
                except Exception:
                    log.exception("fast_lane_download_failed", symbol=r["symbol"])
                    continue
                processed += 1
                if state != State.TEXT_EXTRACTED:
                    continue
                path_row = conn.execute(
                    "SELECT pdf_path FROM raw_announcements WHERE fingerprint = ?",
                    (r["fingerprint"],),
                ).fetchone()
                if not path_row or not path_row[0]:
                    continue
                res = extract_and_store(
                    conn, fingerprint=r["fingerprint"], symbol=r["symbol"],
                    subject=r["subject"], broadcast_dt=r["broadcast_dt"],
                    pdf_path=path_row[0], use_llm=True,
                )
                stored += res["stored"]
            if processed:
                log.info("fast_result_lane", processed=processed, stored=stored)
        except Exception:
            log.exception("fast_result_lane_failed")
        finally:
            conn.close()

    scheduler.add_job(
        _tick, trigger=IntervalTrigger(seconds=FAST_LANE_INTERVAL_SEC),
        id=job_id, max_instances=1, coalesce=True, replace_existing=True,
    )
    return job_id


def register_intraday_extract_runner(scheduler, db_path: str) -> str:
    """Every few minutes DURING market hours: extract financials from results
    that just filed, so a mid-session reaction can be scored with the actual
    fundamental surprise. Internally gated on market hours (off-hours = no-op);
    most ticks find nothing new and return immediately."""
    from apscheduler.triggers.interval import IntervalTrigger

    from nse_data.scheduler.market_hours import is_market_open
    from nse_data.storage.db import open_db

    job_id = "extract_financials_intraday"

    def _tick():
        if not is_market_open():
            return
        conn = open_db(db_path)
        try:
            report = run_extract_pass(conn, limit=INTRADAY_EXTRACT_BATCH, use_llm=True)
            if report["processed"]:
                log.info("extract_financials_intraday", **report)
            # P7: fold in press releases / decks that landed after extraction.
            refreshed = refresh_narratives(conn, use_llm=True)
            if refreshed["updated"]:
                log.info("narratives_refreshed_intraday", **refreshed)
        except Exception:
            log.exception("extract_financials_intraday_failed")
        finally:
            conn.close()

    scheduler.add_job(
        _tick, trigger=IntervalTrigger(minutes=INTRADAY_EXTRACT_INTERVAL_MIN),
        id=job_id, max_instances=1, coalesce=True, replace_existing=True,
    )
    return job_id


# --------------------------------------------------------------------------- #
# growth (YoY / QoQ) over the stored history
# --------------------------------------------------------------------------- #

def _pct_change(current: float | None, prior: float | None) -> float | None:
    """Percent change, sign-aware. None if not computable (incl. prior == 0)."""
    if current is None or prior is None or prior == 0:
        return None
    return (current - prior) / abs(prior) * 100.0


def _undo_pct(current: float | None, pct: float | None) -> float | None:
    """Recover the prior level from a current level and its percent change."""
    if not isinstance(current, (int, float)) or not isinstance(pct, (int, float)):
        return None
    factor = 1.0 + pct / 100.0
    if factor == 0:
        return None
    return current / factor


def derive_core_operating(growth: dict | None, fields: dict | None) -> dict:
    """Core operating profit ex-other-income (PBT − other income) growth.

    Non-bank filings rarely print an operating-profit subtotal and EBITDA needs
    depreciation/finance lines the extractor doesn't read — but PBT and other
    income are extracted, so ``PBT − other income`` is a derivable operating line
    that isolates the other-income prop (the ONGC signature: revenue & PAT up,
    core down). Adds ``yoy``/``qoq_operating_ex_oi_pct`` when PBT & other-income
    growth and current levels are present. Pure arithmetic; only emitted when the
    prior-period core base is positive (a percent off a non-positive base is
    meaningless). Banks ignore this key — their operating line is PPOP."""
    growth = growth or {}
    fields = fields or {}
    pbt, oi = fields.get("pbt_cr"), fields.get("other_income_cr")
    if not isinstance(pbt, (int, float)) or not isinstance(oi, (int, float)):
        return {}
    core_cur = pbt - oi
    out: dict[str, float] = {}
    for period in ("yoy", "qoq"):
        pbt_prior = _undo_pct(pbt, growth.get(f"{period}_pbt_pct"))
        oi_prior = _undo_pct(oi, growth.get(f"{period}_other_income_pct"))
        if pbt_prior is None or oi_prior is None:
            continue
        core_prior = pbt_prior - oi_prior
        if core_prior <= 0:
            continue
        v = _pct_change(core_cur, core_prior)
        if v is not None:
            out[f"{period}_operating_ex_oi_pct"] = round(v, 2)
    return out


def derive_ebitda(growth: dict | None, fields: dict | None) -> dict:
    """True operating EBITDA growth, when the inputs are extracted.

    ``EBITDA = PBT(before exceptional) + finance_cost + depreciation − other_income``
    — operating profit before interest, tax, D&A, **exceptional items** and
    non-operating other income. Using PBT-before-exceptional is what keeps a
    one-off (e.g. JSW Steel's Q4 FY26 ₹17,888 cr exceptional gain) from inflating
    the operating line; it falls back to plain PBT when the filing doesn't split
    out exceptionals (i.e. when there are none). This is the textbook operating
    line for non-banks; it supersedes the core-ex-OI proxy when depreciation &
    finance costs are present, and falls back to it (and finally revenue) when
    they are not. Adds ``yoy``/``qoq_ebitda_pct``. Pure arithmetic; only emitted
    when the prior-period EBITDA base is positive. Banks ignore this key — their
    operating line is PPOP."""
    growth = growth or {}
    fields = fields or {}
    # PBT before exceptional items when the filing splits it out, else plain PBT.
    pbt = fields.get("pbt_before_exceptional_cr")
    pbt_key = "pbt_before_exceptional"
    if not isinstance(pbt, (int, float)):
        pbt, pbt_key = fields.get("pbt_cr"), "pbt"
    oi = fields.get("other_income_cr")
    dep, fin = fields.get("depreciation_cr"), fields.get("finance_cost_cr")
    if (not isinstance(pbt, (int, float)) or not isinstance(oi, (int, float))
            or not isinstance(dep, (int, float)) or not isinstance(fin, (int, float))):
        return {}
    ebitda_cur = pbt + fin + dep - oi
    out: dict[str, float] = {}
    for period in ("yoy", "qoq"):
        # prior PBT-before-exceptional growth, falling back to plain PBT growth
        pbt_pct = growth.get(f"{period}_{pbt_key}_pct")
        if pbt_pct is None:
            pbt_pct = growth.get(f"{period}_pbt_pct")
        pbt_p = _undo_pct(pbt, pbt_pct)
        fin_p = _undo_pct(fin, growth.get(f"{period}_finance_cost_pct"))
        dep_p = _undo_pct(dep, growth.get(f"{period}_depreciation_pct"))
        oi_p = _undo_pct(oi, growth.get(f"{period}_other_income_pct"))
        if pbt_p is None or fin_p is None or dep_p is None or oi_p is None:
            continue
        ebitda_prior = pbt_p + fin_p + dep_p - oi_p
        if ebitda_prior <= 0:
            continue
        v = _pct_change(ebitda_cur, ebitda_prior)
        if v is not None:
            out[f"{period}_ebitda_pct"] = round(v, 2)
    return out


def _parse_date(s: str) -> _dt.date | None:
    try:
        return _dt.date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None


def _nearest_prior_row(
    conn: sqlite3.Connection, symbol: str, scope: str,
    current_period: str, months_back: int, tol_days: int = 25,
) -> sqlite3.Row | None:
    """The stored quarter closest to ``current_period`` shifted back N months.

    YoY uses months_back=12 (same quarter last year), QoQ uses 3 (prior quarter).
    Quarter-end days differ across quarters (Mar 31 vs Dec 31), so we match within
    a ±tol_days window rather than requiring an exact date.
    """
    cur = _parse_date(current_period)
    if cur is None:
        return None
    # shift back ~months_back months (30.44 days/month is plenty given tol window)
    target = cur - _dt.timedelta(days=round(months_back * 30.44))
    rows = conn.execute(
        "SELECT period_ending, revenue_cr, pat_cr, eps_basic, total_income_cr, "
        "operating_profit_cr, net_interest_income_cr, provisions_cr, other_income_cr, "
        "pbt_cr, tax_cr, depreciation_cr, finance_cost_cr, pbt_before_exceptional_cr, "
        "cost_of_materials_cr, employee_cost_cr, other_expenses_cr, "
        "trade_receivables_cr, inventories_cr, trade_payables_cr "
        "FROM extracted_financials "
        "WHERE symbol = ? AND scope = ? AND period_ending < ?",
        (symbol, scope, current_period),
    ).fetchall()
    best = None
    best_gap = tol_days + 1
    for row in rows:
        d = _parse_date(row[0])
        if d is None:
            continue
        gap = abs((d - target).days)
        if gap <= tol_days and gap < best_gap:
            best, best_gap = row, gap
    return best


# Column index in the _nearest_prior_row / cur SELECT for each growth input.
# (period_ending=0, revenue=1, pat=2, eps=3, total_income=4, ppop=5, nii=6,
#  provisions=7, other_income=8, pbt=9, tax=10, depreciation=11, finance_cost=12,
#  pbt_before_exceptional=13, cost_of_materials=14, employee_cost=15, other_expenses=16,
#  trade_receivables=17, inventories=18, trade_payables=19)
_GROW_IDX = {"revenue": 1, "pat": 2, "total_income": 4, "ppop": 5, "nii": 6,
             "provisions": 7, "other_income": 8, "pbt": 9, "tax": 10,
             "depreciation": 11, "finance_cost": 12, "pbt_before_exceptional": 13,
             "cost_of_materials": 14, "employee_cost": 15, "other_expenses": 16,
             "trade_receivables": 17, "inventories": 18, "trade_payables": 19}


def quarter_growth(
    conn: sqlite3.Connection, symbol: str, period_ending: str,
    scope: str = "standalone",
) -> dict:
    """YoY and QoQ growth at one quarter, computed from STORED history.

    Compares this quarter's extracted level to the prior-year / prior-quarter
    extracted level (both current-quarter reads) — far more reliable than a
    model's in-filing comparative columns on a dense bank P&L. Returns ``*_pct``
    keys for whichever lines are computable (empty when no prior quarter on file).
    Keys match what earnings_quality.classify_quality consumes (incl. BFSI:
    ppop / nii / provisions / other_income).
    """
    cur = conn.execute(
        "SELECT period_ending, revenue_cr, pat_cr, eps_basic, total_income_cr, "
        "operating_profit_cr, net_interest_income_cr, provisions_cr, other_income_cr, "
        "pbt_cr, tax_cr, depreciation_cr, finance_cost_cr, pbt_before_exceptional_cr, "
        "cost_of_materials_cr, employee_cost_cr, other_expenses_cr, "
        "trade_receivables_cr, inventories_cr, trade_payables_cr "
        "FROM extracted_financials "
        "WHERE symbol = ? AND scope = ? AND period_ending = ?",
        (symbol, scope, period_ending),
    ).fetchone()
    if cur is None:
        return {}

    def g(idx):
        return cur[idx]

    out: dict[str, float] = {}
    yoy = _nearest_prior_row(conn, symbol, scope, period_ending, 12)
    if yoy is not None:
        for key, name in (
            ("yoy_revenue_pct", "revenue"), ("yoy_pat_pct", "pat"),
            ("yoy_total_income_pct", "total_income"), ("yoy_ppop_pct", "ppop"),
            ("yoy_nii_pct", "nii"), ("yoy_provisions_pct", "provisions"),
            ("yoy_other_income_pct", "other_income"),
            ("yoy_pbt_pct", "pbt"), ("yoy_tax_pct", "tax"),
            ("yoy_depreciation_pct", "depreciation"),
            ("yoy_finance_cost_pct", "finance_cost"),
            ("yoy_pbt_before_exceptional_pct", "pbt_before_exceptional"),
            ("yoy_cost_of_materials_pct", "cost_of_materials"),
            ("yoy_employee_cost_pct", "employee_cost"),
            ("yoy_other_expenses_pct", "other_expenses"),
        ):
            i = _GROW_IDX[name]
            v = _pct_change(g(i), yoy[i])
            if v is not None:
                out[key] = round(v, 2)
    qoq = _nearest_prior_row(conn, symbol, scope, period_ending, 3)
    if qoq is not None:
        for key, name in (
            ("qoq_revenue_pct", "revenue"), ("qoq_pat_pct", "pat"),
            ("qoq_ppop_pct", "ppop"), ("qoq_nii_pct", "nii"),
            ("qoq_other_income_pct", "other_income"),
            ("qoq_pbt_pct", "pbt"), ("qoq_tax_pct", "tax"),
            ("qoq_depreciation_pct", "depreciation"),
            ("qoq_finance_cost_pct", "finance_cost"),
            ("qoq_pbt_before_exceptional_pct", "pbt_before_exceptional"),
            ("qoq_cost_of_materials_pct", "cost_of_materials"),
            ("qoq_employee_cost_pct", "employee_cost"),
            ("qoq_other_expenses_pct", "other_expenses"),
        ):
            i = _GROW_IDX[name]
            v = _pct_change(g(i), qoq[i])
            if v is not None:
                out[key] = round(v, 2)

    # Cost-line INTENSITY change (percentage points of revenue), computed from the
    # current vs prior rows directly. Negative material-ratio change = inputs got
    # cheaper as a share of revenue — the commodity-tailwind signal the auto/FMCG
    # rules use to tell a durable margin gain (volume/mix/pricing) from an
    # input-cost windfall. ``mi`` = cost-line column index in the SELECT. The
    # EBITDA-margin change (pp) lets a rule check whether the input move actually
    # explains the margin gain.
    def _ebitda_lvl(r):
        pbt = r[13] if r[13] is not None else r[9]      # before-exceptional, else PBT
        if pbt is None or r[12] is None or r[11] is None or r[8] is None:
            return None
        return pbt + r[12] + r[11] - r[8]               # +finance +deprec −other income

    for period, prior in (("yoy", yoy), ("qoq", qoq)):
        if prior is None:
            continue
        rev_c, rev_p = g(1), prior[1]
        if not rev_c or not rev_p:
            continue
        for key, mi in (("material_ratio_chg_pp", 14),
                        ("employee_ratio_chg_pp", 15),
                        ("other_exp_ratio_chg_pp", 16)):
            cc, cp = g(mi), prior[mi]
            if cc is not None and cp is not None:
                out[f"{period}_{key}"] = round((cc / rev_c - cp / rev_p) * 100, 2)
        eb_c, eb_p = _ebitda_lvl(cur), _ebitda_lvl(prior)
        if eb_c is not None and eb_p is not None:
            out[f"{period}_ebitda_margin_chg_pp"] = round((eb_c / rev_c - eb_p / rev_p) * 100, 2)
        # Working-capital intensity change (pp of revenue): WC = receivables +
        # inventory − payables. A rising ratio = cash stuck in the balance sheet
        # (the capgoods working_capital_balloon tell). Needs all three balance-
        # sheet lines on both rows.
        def _wc(r):
            recv, inv, pay = r[17], r[18], r[19]
            return None if recv is None or inv is None or pay is None else recv + inv - pay
        wc_c, wc_p = _wc(cur), _wc(prior)
        if wc_c is not None and wc_p is not None:
            out[f"{period}_wc_ratio_chg_pp"] = round((wc_c / rev_c - wc_p / rev_p) * 100, 2)

    # Derive the non-bank operating lines from the growth just computed — the
    # lines the energy/generic sector rules read (base.generic_operating_growth):
    # true EBITDA when depreciation & finance costs are present, else core profit
    # ex-other-income (PBT − other income).
    levels = {
        "pbt_cr": g(9), "other_income_cr": g(8),
        "depreciation_cr": g(11), "finance_cost_cr": g(12),
        "pbt_before_exceptional_cr": g(13),
    }
    out.update(derive_core_operating(out, levels))
    out.update(derive_ebitda(out, levels))
    return out
