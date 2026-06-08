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

# Agency patterns → canonical name. Word-boundaried so 'CARE' doesn't match
# inside 'Counterparty'/'healthcare' etc. Indian agencies dominate the corpus;
# global agencies (Moody's/S&P/Fitch) appear on bank/large-cap filings.
_AGENCY_PATTERNS = [
    (re.compile(r"\bcrisil\b", re.I), "CRISIL"),
    (re.compile(r"\bicra\b", re.I), "ICRA"),
    (re.compile(r"\bcare\s+(?:ratings|edge)\b", re.I), "CARE"),   # not 'due care'
    (re.compile(r"india ratings|\bind[\s-]?ra\b", re.I), "India Ratings"),
    (re.compile(r"acuit", re.I), "Acuité"),
    (re.compile(r"brickwork|\bbwr\b", re.I), "Brickwork"),
    (re.compile(r"infomerics|\bivr\b", re.I), "INFOMERICS"),
    # Global agencies need their full names — bare 'S&P'/'Fitch'/'Moody' match
    # boilerplate ('S&P BSE Sensex', 'a Fitch Group company') on domestic filings.
    (re.compile(r"moody'?s\s+(?:investors|ratings)", re.I), "Moody's"),
    (re.compile(r"fitch\s+ratings", re.I), "Fitch"),
    (re.compile(r"s&p\s+global\s+ratings|standard\s*&\s*poor", re.I), "S&P"),
]

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
    for pat, name in _AGENCY_PATTERNS:
        if pat.search(text):
            return name
    return None


# A down/up-grade that's an ACTUAL rating action: the verb tied to a letter
# grade move ('downgraded … to/from … A-/BBB+', or '(Downgraded …'). Requiring a
# real A–D grade after to/from excludes:
#   - scenario boilerplate ('could be downgraded if margins decline')
#   - outlook changes ('upgraded the outlook from Negative to Stable')
#   - Moody's-scale moves ('downgraded to Ba2') — not our domestic-junk target
_GRADE_AFTER = r"(?:to|from)\b[^.]{0,15}?\b[A-D]{1,3}[+-]?(?![A-Za-z0-9])"
_DOWNGRADE = re.compile(r"down[\s-]?graded\b[^.]{0,80}?" + _GRADE_AFTER
                        + r"|\(\s*down[\s-]?graded", re.I)
_UPGRADE = re.compile(r"up[\s-]?graded\b[^.]{0,80}?" + _GRADE_AFTER
                      + r"|\(\s*up[\s-]?graded", re.I)
_REAFFIRM = re.compile(r"re[\s-]?affirm|affirmed|retained|maintained", re.I)


def extract_action(text: str) -> str | None:
    """Canonical action.

    Prefers the structured NSE form field ('Rating Action (...) <VALUE>'). Then
    looks for a *definitive* down/up-grade (verb tied to a grade move) so that
    boilerplate like 'the rating could be downgraded if…' in a reaffirmation
    PDF doesn't read as a downgrade. Reaffirm/assigned are last.
    """
    m = _FORM_ACTION.search(text)
    if m:
        value = m.group(1).strip().lower()
        for needle, action in _FORM_MAP:
            if value.startswith(needle):
                return action
        # 'Other'/'New' etc. → fall through

    low = _FORM_LABEL.sub(" ", text)
    if _DOWNGRADE.search(low):
        return "downgrade"
    if _UPGRADE.search(low):
        return "upgrade"
    ll = low.lower()
    if "watch" in ll and ("negative" in ll or "developing" in ll):
        return "watch_negative"
    if _REAFFIRM.search(low):
        return "reaffirm"
    if "assign" in ll:
        return "assigned"
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


# ---- multi-instrument headline + scoring -----------------------------------

# Long-term grade → credit-quality score (0–100). Junk boundary at BB+ (45).
_LT_SCALE = {
    "AAA": 100, "AA+": 92, "AA": 88, "AA-": 84, "A+": 78, "A": 74, "A-": 70,
    "BBB+": 64, "BBB": 60, "BBB-": 55, "BB+": 45, "BB": 40, "BB-": 35,
    "B+": 30, "B": 25, "B-": 20, "C": 10, "D": 0,
}
_ST_TOKEN = re.compile(r"\bA[1-4][+]?\b")
# A long-term grade only counts when it's in a rating CONTEXT — preceded by an
# agency name, or followed by an outlook — so stray 'D)' list markers and lone
# 'C'/'A' letters in prose aren't mistaken for grades.
_RATED_GRADE = re.compile(
    r"(?:crisil|\[?icra\]?|care|ind(?:-ra)?|bwr|ivr|brickwork|acuit|moody|fitch)"
    r"\s*'?\s*([A-D]{1,3}[+-]?)(?![A-Za-z0-9])"
    r"|\b([A-D]{1,3}[+-]?)\s*[/(]\s*(?:stable|negative|positive|developing)",
    re.I,
)
_INSTRUMENTS = [
    ("non-convertible", "NCD"), ("ncd", "NCD"),
    ("commercial paper", "Commercial Paper"),
    ("fixed deposit", "Fixed Deposit"),
    ("market linked", "MLD"), ("mld", "MLD"),
    ("sub debt", "Subordinated Debt"), ("subordinate", "Subordinated Debt"),
    ("term loan", "Term Loan"), ("cash credit", "Cash Credit"),
    ("bank loan", "Bank Facilities"), ("bank facilit", "Bank Facilities"),
    ("non-fund", "Bank Facilities"), ("fund based", "Bank Facilities"),
]


def credit_quality_score(grade: str | None) -> float | None:
    return _LT_SCALE.get(normalize_grade(grade) or "")


def extract_agencies(text: str) -> list[str]:
    """All rating agencies mentioned, in first-seen order (deduped)."""
    out: list[str] = []
    for pat, name in _AGENCY_PATTERNS:
        if name not in out and pat.search(text):
            out.append(name)
    return out


def extract_lt_grades(text: str) -> list[str]:
    """Long-term grades in a rating context (agency-prefixed or outlook-suffixed).
    Short-term (A1+/A2) and stray single letters are excluded."""
    out = []
    for m in _RATED_GRADE.finditer(text):
        g = (m.group(1) or m.group(2) or "").upper()
        if g in _LT_SCALE:
            out.append(g)
    return out


def extract_st_grades(text: str) -> list[str]:
    seen, out = set(), []
    for g in _ST_TOKEN.findall(text.upper()):
        if g not in seen:
            seen.add(g); out.append(g)
    return out


def min_lt_grade(grades: list[str]) -> str | None:
    """The worst (lowest-scoring) long-term grade in the list."""
    valid = [g for g in grades if g in _LT_SCALE]
    return min(valid, key=lambda g: _LT_SCALE[g]) if valid else None


def worst_action(text: str) -> str | None:
    """The filing's headline action. Downgrade/upgrade (grade-tied) and
    watch-negative are reliable and come first. Reaffirm/'Rating Outstanding' is
    checked BEFORE assigned: a reaffirmation PDF often contains the word
    'assigned' as boilerplate ('the assigned ratings'), and since 'assigned'
    alerts but reaffirm doesn't, biasing to reaffirm avoids false alerts."""
    low = _FORM_LABEL.sub(" ", text)
    ll = low.lower()
    if _DOWNGRADE.search(low):
        return "downgrade"
    if "watch" in ll and ("negative" in ll or "developing" in ll):
        return "watch_negative"
    if _UPGRADE.search(low):
        return "upgrade"
    if _REAFFIRM.search(low) or "outstanding" in ll:
        return "reaffirm"
    if "assign" in ll:
        return "assigned"
    return None


def parse_lines(text: str) -> list[dict]:
    """Best-effort per-instrument lines. The PDF text is column-jumbled, so each
    instrument keyword is paired with the nearest grade/agency/action in a window
    — imperfect, but captures the common structure for the audit/ML record."""
    t = " ".join(text.split())
    low = t.lower()
    lines, seen = [], set()
    for needle, label in _INSTRUMENTS:
        start = 0
        while True:
            i = low.find(needle, start)
            if i == -1:
                break
            start = i + len(needle)
            window = t[max(0, i - 15):i + 90]
            lt = _first_grade(window)
            st_m = _ST_TOKEN.search(window.upper())
            key = (label, lt, st_m.group(0) if st_m else None)
            if key in seen:
                continue
            seen.add(key)
            # nearest agency mentioned before this instrument
            agency = None
            for pat, name in _AGENCY_PATTERNS:
                if pat.search(t[:i]):
                    agency = name      # last match before i wins
            lines.append({
                "agency": agency, "instrument_type": label,
                "lt_rating": lt, "lt_outlook": _outlook(window),
                "st_rating": st_m.group(0) if st_m else None,
                "line_action": worst_action(window),
            })
    return lines


def _outlook(window: str) -> str | None:
    low = window.lower()
    for o in ("negative", "positive", "stable", "developing"):
        if o in low:
            return o.capitalize()
    return None


def parse_filing(text: str) -> dict:
    """Headline (drives alerts/score) + best-effort instrument lines."""
    agencies = extract_agencies(text)
    lt_grades = extract_lt_grades(text)
    st_grades = extract_st_grades(text)
    worst_lt = min_lt_grade(lt_grades)
    action = worst_action(text)
    lines = parse_lines(text)
    return {
        "agencies": agencies,
        "n_instruments": len(lines),
        "worst_action": action,
        "min_lt_grade": worst_lt,
        "credit_quality_score": credit_quality_score(worst_lt),
        "is_junk_downgrade": 1 if (action == "downgrade" and is_junk_downgrade(worst_lt)) else 0,
        "outlook_negative": 1 if re.search(r"negative\s+outlook|outlook[:\s]+negative|/\s*negative", text, re.I) else 0,
        "has_short_term": 1 if st_grades else 0,
        "lt_grades": lt_grades,
        "st_grades": st_grades,
        "lines": lines,
    }


def parse_rating(text: str) -> dict:
    """Full structured parse of one rating PDF's text (single-action view)."""
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
        h = parse_filing(pdf_text)
        parsed += 1
        if h["worst_action"] is None:    # couldn't classify — skip (re-tried next run)
            continue
        cur = conn.execute(
            "INSERT OR IGNORE INTO raw_rating_actions "
            "(symbol, agency, action, old_rating, new_rating, instrument_type, "
            " is_junk_downgrade, broadcast_dt, announcement_fingerprint, "
            " agencies, n_instruments, worst_action, min_lt_grade, "
            " credit_quality_score, outlook_negative, has_short_term) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol, (h["agencies"][0] if h["agencies"] else None),
             h["worst_action"], None, h["min_lt_grade"], None,
             h["is_junk_downgrade"], broadcast_dt, fingerprint,
             ",".join(h["agencies"]), h["n_instruments"], h["worst_action"],
             h["min_lt_grade"], h["credit_quality_score"],
             h["outlook_negative"], h["has_short_term"]),
        )
        if cur.rowcount == 0:            # already present — idempotent re-run
            continue
        inserted += 1
        _write_lines(conn, fingerprint, symbol, broadcast_dt, h["lines"])
        if emit and _is_recent(broadcast_dt, now):
            if _emit_credit_signal(conn, symbol, broadcast_dt, h, sender):
                signaled += 1
    conn.commit()
    return {"candidates": len(rows), "parsed": parsed,
            "inserted": inserted, "signaled": signaled}


def _write_lines(conn, fingerprint, symbol, broadcast_dt, lines: list[dict]) -> None:
    for ln in lines:
        conn.execute(
            "INSERT INTO raw_rating_lines (announcement_fingerprint, symbol, agency, "
            " instrument_type, rated_amount, lt_rating, lt_outlook, st_rating, "
            " line_action, broadcast_dt) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (fingerprint, symbol, ln.get("agency"), ln.get("instrument_type"),
             ln.get("rated_amount"), ln.get("lt_rating"), ln.get("lt_outlook"),
             ln.get("st_rating"), ln.get("line_action"), broadcast_dt),
        )


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


def credit_signal_type(action: str | None, is_junk: int | bool) -> str | None:
    """Map a headline action to a credit signal_type. Alerts on everything except
    reaffirm/outstanding (task 16.8 + Week-16 rework)."""
    if action == "downgrade" and is_junk:
        return "credit_downgrade_junk"
    if action == "assigned":
        return "credit_rating_assigned"
    return _CREDIT_SIGNAL.get(action or "")


def build_rating_message(symbol: str, h: dict, broadcast_dt: str | None) -> str:
    """Rating alert from the per-filing headline — distinct from intraday (16.9)."""
    action = h.get("worst_action")
    head = {
        "downgrade": "Credit DOWNGRADE", "upgrade": "Credit Upgrade",
        "watch_negative": "Credit Watch (Negative)", "assigned": "Rating Assigned",
    }.get(action or "", "Credit Action")
    emoji = "🔴" if action in ("downgrade", "watch_negative") else "🟢"
    agencies = ", ".join(h.get("agencies") or []) or "n/a"
    score = h.get("credit_quality_score")

    lines = [
        f"{emoji} {symbol} — {head}",
        f"Agencies: {agencies} | Instruments: {h.get('n_instruments', 0)}",
        f"Worst grade: {h.get('min_lt_grade') or 'n/a'}"
        + (f"  (quality {score:.0f}/100)" if score is not None else "")
        + ("  ⚠ JUNK" if h.get("is_junk_downgrade") else "")
        + ("  ⚠ outlook negative" if h.get("outlook_negative") else ""),
        f"Filed: {broadcast_dt or 'n/a'}",
    ]
    # show up to a few parsed instrument lines for context
    for ln in (h.get("lines") or [])[:4]:
        grade = ln.get("lt_rating") or ln.get("st_rating") or "?"
        lines.append(f"  • {ln.get('instrument_type') or 'instrument'}: {grade}")
    if action == "downgrade":
        lines += [
            "", "Watch tomorrow open:",
            "Check pre-open IEP ~09:08 for gap direction",
            "Gap down >3% and holds → short setup at 09:30; gap fills → avoid",
        ]
    return "\n".join(lines)


def _emit_credit_signal(conn, symbol, broadcast_dt, h: dict, sender) -> bool:
    """Write a credit_* signal row + send the rating alert. Returns True if alerted."""
    sig_type = credit_signal_type(h["worst_action"], h["is_junk_downgrade"])
    if sig_type is None:                  # reaffirm / outstanding → no alert
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
    return bool(sender(token, chat_id, build_rating_message(symbol, h, broadcast_dt)))


# ---- credit context for the signal scorer (Week-16 credit→signal) ----------

_STRESSED_ST = {"A3", "A3+", "A4", "D"}


def _days_since(broadcast_dt: str | None, now: datetime) -> int | None:
    if not broadcast_dt:
        return None
    try:
        dt = datetime.strptime(broadcast_dt.strip(), "%d-%b-%Y %H:%M:%S").replace(
            tzinfo=market_hours.IST)
    except ValueError:
        return None
    return (now - dt).days


def latest_credit_by_symbol(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    """{symbol: credit-context} for the confidence scorer. Latest filing per
    symbol → action, quality score, junk flag, days-since, ST-stress. Used to
    nudge swing scores, bias intraday on event days, and hard-kill junk longs."""
    now = now or market_hours.now_ist()
    out: dict = {}
    # MAX(id) → most recently inserted (≈ latest) filing per symbol; SQLite takes
    # the other bare columns from that same row. Tolerant of the tables/columns
    # not existing yet (migrations 051/052 not applied) → empty context.
    try:
        rows = conn.execute(
            "SELECT symbol, worst_action, min_lt_grade, credit_quality_score, "
            "       is_junk_downgrade, broadcast_dt, MAX(id) "
            "FROM raw_rating_actions GROUP BY symbol"
        ).fetchall()
    except sqlite3.OperationalError:
        return out
    for symbol, action, grade, score, junk, bdt, _id in rows:
        out[symbol] = {
            "action": action, "min_lt_grade": grade, "quality_score": score,
            "is_junk": bool(junk), "days_since": _days_since(bdt, now),
            "st_stressed": False,
        }
    try:
        st_rows = conn.execute(
            "SELECT DISTINCT symbol, st_rating FROM raw_rating_lines "
            "WHERE st_rating IS NOT NULL"
        ).fetchall()
    except sqlite3.OperationalError:
        st_rows = []
    for symbol, st in st_rows:
        if symbol in out and (st or "").upper() in _STRESSED_ST:
            out[symbol]["st_stressed"] = True
    return out


def is_junk_downgrade_kill(credit: dict | None, *, window: int = 5) -> bool:
    """True when a recent junk downgrade should suppress LONG signals (hard-kill)."""
    if not credit:
        return False
    days = credit.get("days_since")
    return bool(credit.get("is_junk") and credit.get("action") == "downgrade"
                and days is not None and days <= window)


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
