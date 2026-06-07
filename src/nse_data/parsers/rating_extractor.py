"""
Credit-rating extractor (FEATURE_CHECKLIST Phase 5, Week 16, task 16.5).

Parses credit-rating announcement text into structured `raw_rating_actions`:
agency, action, old/new rating, instrument, and a junk-downgrade flag. The pure
parsers (top) are unit-tested; `run_rating_extraction` glues them to
`raw_announcements` (which already carries extracted `pdf_text`).
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta

import structlog

from ..scheduler import market_hours
from ..storage.db import open_db

log = structlog.get_logger()

# action → credit signal_type (task 16.8). reaffirm/assigned don't signal.
_CREDIT_SIGNAL = {
    "downgrade": "credit_downgrade",
    "upgrade": "credit_upgrade",
    "watch_negative": "credit_watch_negative",
}
# Only alert on actions broadcast within this window — so a historical backfill
# (task 16.7) populates raw_rating_actions WITHOUT spamming old downgrades.
_ALERT_RECENCY_DAYS = 2

# keyword (lowercased substring) -> canonical agency
_AGENCIES = {
    "crisil": "CRISIL", "icra": "ICRA", "care": "CARE",
    "india ratings": "India Ratings", "ind-ra": "India Ratings",
    "acuit": "Acuité", "brickwork": "Brickwork", "infomerics": "INFOMERICS",
}

# Long-term rating scale, best → worst. Junk = BB+ and below.
_SCALE = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-",
          "BBB+", "BBB", "BBB-", "BB+", "BB", "BB-", "B+", "B", "B-", "C", "D"]
_JUNK_FROM = _SCALE.index("BB+")

# A bare long-term grade as a whole token. Leading \b + trailing "not a letter/
# digit" lookahead make it skip agency names (CRISIL/CARE/ICRA…) while still
# capturing the +/- suffix (e.g. 'A-/Stable' → 'A-', 'BBB+/Neg' → 'BBB+').
_GRADE_TOKEN = re.compile(r"\b([A-D]{1,3}[+-]?)(?![A-Za-z0-9])")

# NSE's structured filing carries a "Rating Action (New/ Upgrade/ Downgrade/
# Re- Affirm/ Other) <VALUE>" field. The list inside the parens is a LABEL, not
# the action — scanning it for "downgrade" gives a false positive. We read the
# VALUE after the parens, and strip the label before any keyword fallback.
_FORM_ACTION = re.compile(r"Rating Action\s*\([^)]*\)\s*[:\-]?\s*([A-Za-z][A-Za-z \-]*)", re.I)
_FORM_LABEL = re.compile(r"\(\s*new\s*/\s*upgrade\s*/\s*downgrade[^)]*\)", re.I)
_FORM_MAP = (
    ("downgrad", "downgrade"), ("upgrad", "upgrade"),
    ("re- affirm", "reaffirm"), ("re-affirm", "reaffirm"), ("reaffirm", "reaffirm"),
    ("assign", "assigned"),
)


def _first_grade(segment: str) -> str | None:
    m = _GRADE_TOKEN.search(segment.upper())
    return m.group(1) if m else None


def _last_grade(segment: str) -> str | None:
    matches = _GRADE_TOKEN.findall(segment.upper())
    return matches[-1] if matches else None


def extract_agency(text: str) -> str | None:
    low = text.lower()
    for key, name in _AGENCIES.items():
        if key in low:
            return name
    return None


def extract_action(text: str) -> str | None:
    """Canonical action.

    Prefers the structured NSE form field ('Rating Action (...) <VALUE>') so the
    parenthesised label list isn't mistaken for the action. Otherwise falls back
    to a worst-first keyword scan (downgrade beats reaffirm in a mixed PDF) —
    after stripping that label list to avoid the same false positive.
    """
    m = _FORM_ACTION.search(text)
    if m:
        value = m.group(1).strip().lower()
        for needle, action in _FORM_MAP:
            if value.startswith(needle):
                return action
        # 'Other'/'New' etc. → fall through to keyword scan

    low = _FORM_LABEL.sub(" ", text.lower())
    if "downgrad" in low:
        return "downgrade"
    if "upgrad" in low:
        return "upgrade"
    if "watch" in low and ("negative" in low or "developing" in low):
        return "watch_negative"
    if "assign" in low:
        return "assigned"
    if any(w in low for w in ("reaffirm", "retained", "maintained", "affirmed")):
        return "reaffirm"
    return None


def normalize_grade(rating: str | None) -> str | None:
    """Strip agency prefix/outlook → the bare grade ('CRISIL BBB+/Stable' → 'BBB+')."""
    return _first_grade(rating) if rating else None


def extract_ratings(text: str) -> tuple[str | None, str | None]:
    """(old_rating, new_rating) grades from 'from X to Y' style phrasing.

    Grades are pulled as whole tokens from the 'from …' and 'to …' segments, so
    an agency name sitting between the keyword and the grade ('from CRISIL A-/
    Stable to CRISIL BBB+') doesn't trip it up.
    """
    t = " ".join(text.split())

    # A) new-first: "<NEW> … (Downgraded/Upgraded from <OLD>)"
    m = re.search(r"\((?:down|up)graded\s+from\s+([^)]{1,40})\)", t, re.IGNORECASE)
    if m:
        old = _first_grade(m.group(1))
        new = _last_grade(t[max(0, m.start() - 40):m.start()])
        if old or new:
            return old, new

    # B) "<rating-word> … from <OLD> … to <NEW>" — anchored to a rating context
    #    so a stray "from … to …" in prose doesn't yield a phantom grade.
    m = re.search(
        r"(?:rating|revised|downgraded|upgraded|reaffirmed)[^.]{0,30}?"
        r"\bfrom\b(.{1,40}?)\bto\b(.{1,40})",
        t, re.IGNORECASE,
    )
    if m:
        old, new = _first_grade(m.group(1)), _first_grade(m.group(2))
        if old or new:
            return old, new

    # C) action context giving only the new rating: "… to/at '<NEW>'"
    m = re.search(
        r"(?:downgraded|upgraded|revised|reaffirmed|assigned)\b[^.]{0,80}?\b(?:to|at)\s+'?([^'\n]{1,30})",
        t, re.IGNORECASE,
    )
    if m:
        return None, _first_grade(m.group(1))
    return None, None


def extract_instrument(text: str) -> str | None:
    low = text.lower()
    for needle, label in (
        ("commercial paper", "Commercial Paper"),
        ("non-convertible", "NCD"), ("ncd", "NCD"),
        ("long term", "Long Term"), ("long-term", "Long Term"),
        ("short term", "Short Term"), ("short-term", "Short Term"),
        ("bank facilit", "Bank Facilities"),
    ):
        if needle in low:
            return label
    return None


def is_junk_downgrade(new_rating: str | None) -> bool:
    """True when the new grade is BB+ or below."""
    grade = normalize_grade(new_rating)
    if grade is None or grade not in _SCALE:
        return False
    return _SCALE.index(grade) >= _JUNK_FROM


def parse_rating(text: str) -> dict:
    """Full structured parse of one rating PDF's text."""
    agency = extract_agency(text)
    action = extract_action(text)
    old_rating, new_rating = extract_ratings(text)
    return {
        "agency": agency,
        "action": action,
        "old_rating": old_rating,
        "new_rating": new_rating,
        "instrument_type": extract_instrument(text),
        "is_junk_downgrade": 1 if (action == "downgrade" and is_junk_downgrade(new_rating)) else 0,
    }


# ---- DB pass (tasks 16.5 / 16.7) -------------------------------------------

def run_rating_extraction(
    conn: sqlite3.Connection, *, limit: int = 1000,
    emit: bool = False, sender=None, now: datetime | None = None,
) -> dict:
    """Parse credit-rating announcements with text that aren't yet in
    raw_rating_actions. Returns counts. Idempotent via the UNIQUE fingerprint.

    `emit=True` writes credit_* signals + sends a rating alert for each newly
    inserted action broadcast within the last few days (backfill stays silent)."""
    now = now or market_hours.now_ist()
    rows = conn.execute(
        "SELECT a.fingerprint, a.symbol, a.broadcast_dt, a.pdf_text "
        "FROM raw_announcements a "
        "WHERE LOWER(a.subject) LIKE '%credit rating%' "
        "  AND a.pdf_text IS NOT NULL AND LENGTH(a.pdf_text) > 100 "
        "  AND a.fingerprint NOT IN (SELECT announcement_fingerprint FROM raw_rating_actions) "
        "LIMIT ?",
        (limit,),
    ).fetchall()

    parsed = inserted = signaled = 0
    for fingerprint, symbol, broadcast_dt, pdf_text in rows:
        r = parse_rating(pdf_text)
        parsed += 1
        if r["action"] is None:          # couldn't classify — skip (re-tried next run)
            continue
        cur = conn.execute(
            "INSERT OR IGNORE INTO raw_rating_actions "
            "(symbol, agency, action, old_rating, new_rating, instrument_type, "
            " is_junk_downgrade, broadcast_dt, announcement_fingerprint) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol, r["agency"], r["action"], r["old_rating"], r["new_rating"],
             r["instrument_type"], r["is_junk_downgrade"], broadcast_dt, fingerprint),
        )
        if cur.rowcount == 0:            # already present — idempotent re-run
            continue
        inserted += 1
        if emit and _is_recent(broadcast_dt, now):
            if _emit_credit_signal(conn, symbol, broadcast_dt, r, sender):
                signaled += 1
    conn.commit()
    return {"candidates": len(rows), "parsed": parsed,
            "inserted": inserted, "signaled": signaled}


# ---- credit signal + alert (tasks 16.8 / 16.9) -----------------------------

def _is_recent(broadcast_dt: str | None, now: datetime) -> bool:
    """True if broadcast within the alert window (guards backfill from spamming)."""
    if not broadcast_dt:
        return False
    try:
        dt = datetime.strptime(broadcast_dt.strip(), "%d-%b-%Y %H:%M:%S").replace(
            tzinfo=market_hours.IST)
    except ValueError:
        return False
    return (now - dt) <= timedelta(days=_ALERT_RECENCY_DAYS)


def credit_signal_type(action: str, is_junk: int | bool) -> str | None:
    """Map a rating action to a credit signal_type (task 16.8)."""
    if action == "downgrade" and is_junk:
        return "credit_downgrade_junk"
    return _CREDIT_SIGNAL.get(action)


def build_rating_message(symbol: str, r: dict, broadcast_dt: str | None) -> str:
    """Rating alert text — distinct from the intraday signal format (task 16.9)."""
    junk = r.get("is_junk_downgrade")
    action = (r.get("action") or "").replace("_", " ").upper()
    emoji = "🔴" if r.get("action") in ("downgrade", "watch_negative") else "🟢"
    head = {
        "downgrade": "Credit Downgrade", "upgrade": "Credit Upgrade",
        "watch_negative": "Credit Watch (Negative)",
    }.get(r.get("action") or "", "Credit Action")

    lines = [
        f"{emoji} {symbol} — {head}",
        f"Agency: {r.get('agency') or 'n/a'} | Action: {action}",
        f"{r.get('old_rating') or '?'} → {r.get('new_rating') or '?'} "
        f"| {r.get('instrument_type') or 'n/a'}",
        f"Filed: {broadcast_dt or 'n/a'}",
    ]
    if junk:
        lines += ["", "⚠ New rating in junk territory (BB+ or below)"]
    if r.get("action") == "downgrade":
        lines += [
            "", "Watch tomorrow open:",
            "Check pre-open IEP ~09:08 for gap direction",
            "Gap down >3% and holds → short setup at 09:30; gap fills → avoid",
        ]
    return "\n".join(lines)


def _emit_credit_signal(conn, symbol, broadcast_dt, r: dict, sender) -> bool:
    """Write a credit_* signal row + send the rating alert. Returns True if alerted."""
    sig_type = credit_signal_type(r["action"], r["is_junk_downgrade"])
    if sig_type is None:
        return False
    detected_at = market_hours.now_ist().isoformat()
    conn.execute(
        "INSERT INTO signals (symbol, signal_type, detected_at, price, "
        " oi_change_pct, price_change_pct, volume_ratio) "
        "VALUES (?, ?, ?, NULL, NULL, NULL, NULL)",
        (symbol, sig_type, detected_at),
    )
    if sender is None:
        return False
    from ..bot.dispatcher import load_telegram_config
    token, chat_id = load_telegram_config()
    return bool(sender(token, chat_id, build_rating_message(symbol, r, broadcast_dt)))


def run_rating_job(db_path: str) -> dict:
    from ..bot.dispatcher import send_telegram
    conn = open_db(db_path)
    try:
        return run_rating_extraction(conn, emit=True, sender=send_telegram)
    finally:
        conn.close()


def register_rating_job(scheduler, db_path: str) -> str:
    """Every 20 min: extract new rating PDFs → raw_rating_actions → credit alerts."""
    from apscheduler.triggers.interval import IntervalTrigger

    def _tick():
        try:
            log.info("rating_extractor", **run_rating_job(db_path))
        except Exception:
            log.exception("rating_extractor_failed")

    scheduler.add_job(
        _tick, trigger=IntervalTrigger(seconds=1200),
        id="rating_extractor", max_instances=1, coalesce=True, replace_existing=True,
    )
    return "rating_extractor"
