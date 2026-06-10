"""Policy/benchmark rate state for the BFSI earnings-risk overlay (Week 17.5, S6).

Stores and reads the two rates the pre-print risk flag (S7) keys off — the RBI
repo rate and the 10-year G-sec yield — and derives the macro *direction* that
predicted SBI's Q4 risk:

  * ``rising_yields``    — 10Y G-sec up over the trailing quarter → AFS mark-to-
                           market loss risk for treasury-heavy banks/insurers.
  * ``repo_cut_recent``  — repo lower than ~6 months ago → EBLR/T-bill books
                           reprice down → NIM-compression risk for banks.

Ingestion: ``record_rates()`` is the working manual path (repo changes a few
times a year; the 10Y can be hand-updated or wired to a feed later). There is no
reliable free API for either series, so an automated collector is a pending
external-source decision (S6) — this module is source-agnostic and works today
off whatever rows exist.

    record_rates(conn, "2026-03-31", repo_rate=6.0, gsec_10y_yield=6.98)
    macro_state(conn)   # {'gsec_10y_yield': 6.98, 'gsec_10y_qoq_bps': +35, ...}
"""
from __future__ import annotations

import csv as _csv
import datetime as _dt
import sqlite3
import time
from pathlib import Path

# A move smaller than this (basis points, over the trailing quarter) is treated
# as flat rather than "rising/falling" — keeps the flag off noise.
_RISING_BPS = 15.0
# How far back to compare for the QoQ yield move and the repo-cut window.
_QOQ_DAYS = 90
_REPO_WINDOW_DAYS = 190


def record_rates(
    conn: sqlite3.Connection,
    as_of_date: str,
    *,
    repo_rate: float | None = None,
    gsec_10y_yield: float | None = None,
    source: str = "manual",
    now: int | None = None,
) -> None:
    """Upsert one day's rates. Either rate may be omitted (kept if already set)."""
    now = now if now is not None else int(time.time())
    existing = conn.execute(
        "SELECT repo_rate, gsec_10y_yield FROM raw_macro_rates WHERE as_of_date = ?",
        (as_of_date,),
    ).fetchone()
    if existing is not None:
        repo_rate = repo_rate if repo_rate is not None else existing[0]
        gsec_10y_yield = gsec_10y_yield if gsec_10y_yield is not None else existing[1]
    conn.execute(
        "INSERT OR REPLACE INTO raw_macro_rates "
        "(as_of_date, repo_rate, gsec_10y_yield, source, captured_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (as_of_date, repo_rate, gsec_10y_yield, source, now),
    )
    conn.commit()


def _parse(d: str) -> _dt.date | None:
    try:
        return _dt.date.fromisoformat(d[:10])
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- #
# CSV import — the free, authoritative route for the 10Y G-sec yield (S6)
# --------------------------------------------------------------------------- #
#
# Repo rate stays MANUAL via record_rates(): it changes only at RBI MPC meetings
# (~6/year), so a scraper is negative ROI — calendar the MPC dates and enter the
# value. For the 10Y yield, the free authoritative sources publish CSV, not a
# JSON API: FBIL (the official benchmark administrator) and RBI's DBIE portal.
# Both are clunky to scrape but trivial to *download*, so the working path is:
# download the CSV → import_rates_csv(). Yahoo does NOT carry the India 10Y;
# investpy/Investing.com is unreliable (Cloudflare) — neither is built on.

_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y", "%m/%d/%Y")


def _parse_any_date(s: str) -> str | None:
    """Parse a CSV date cell to an ISO 'YYYY-MM-DD' string, or None."""
    s = (s or "").strip()
    if not s:
        return None
    iso = _parse(s)
    if iso is not None:
        return iso.isoformat()
    for fmt in _DATE_FORMATS:
        try:
            return _dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_float(s: str) -> float | None:
    s = (s or "").strip().replace("%", "").replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _pick_column(headers: list[str], *needles: str) -> str | None:
    """First header (case-insensitive) containing ALL of any needle group.

    Each needle is a '+'-joined set of required substrings, e.g. '10+yield'."""
    low = {h: h.lower() for h in headers}
    for needle in needles:
        parts = needle.split("+")
        for h in headers:
            if all(p in low[h] for p in parts):
                return h
    return None


def import_rates_csv(
    conn: sqlite3.Connection,
    path: str | Path,
    *,
    date_col: str | None = None,
    gsec_col: str | None = None,
    repo_col: str | None = None,
    source: str = "csv",
    now: int | None = None,
) -> dict:
    """Ingest rate rows from a CSV (FBIL/DBIE 10Y export, or any date+rate file).

    Columns are auto-detected from common header variants when not given:
    a date column, a 10Y G-sec yield column, and optionally a repo column.
    Returns {'rows': n_seen, 'imported': n_written, 'gsec_col', 'date_col'}.
    Raises ValueError if no usable date/value columns are found."""
    rows = list(_csv.DictReader(Path(path).open(newline="")))
    if not rows:
        return {"rows": 0, "imported": 0}
    headers = list(rows[0].keys())

    date_col = date_col or _pick_column(headers, "date", "dt")
    gsec_col = gsec_col or _pick_column(
        headers, "10y", "10+yield", "10+yld", "gsec+yield", "gsec+yld",
        "g-sec", "10 year", "10-year", "benchmark",
    )
    repo_col = repo_col or _pick_column(headers, "repo")
    if date_col is None or (gsec_col is None and repo_col is None):
        raise ValueError(
            f"could not detect columns in {headers!r}; "
            "pass date_col= and gsec_col=/repo_col= explicitly"
        )

    imported = 0
    for r in rows:
        iso = _parse_any_date(r.get(date_col, ""))
        if iso is None:
            continue
        gsec = _parse_float(r.get(gsec_col, "")) if gsec_col else None
        repo = _parse_float(r.get(repo_col, "")) if repo_col else None
        if gsec is None and repo is None:
            continue
        record_rates(conn, iso, repo_rate=repo, gsec_10y_yield=gsec, source=source, now=now)
        imported += 1
    return {
        "rows": len(rows), "imported": imported,
        "date_col": date_col, "gsec_col": gsec_col, "repo_col": repo_col,
    }


def _latest_non_null(rows: list[tuple], col: int) -> tuple[_dt.date, float] | None:
    """Most recent (date, value) where column ``col`` is not NULL."""
    for d, repo, gsec in rows:
        val = (repo, gsec)[col]
        dt = _parse(d)
        if val is not None and dt is not None:
            return dt, float(val)
    return None


def _value_near(rows: list[tuple], col: int, target: _dt.date, tol_days: int) -> float | None:
    """The non-NULL value closest to ``target`` within ``tol_days``."""
    best, best_gap = None, tol_days + 1
    for d, repo, gsec in rows:
        val = (repo, gsec)[col]
        dt = _parse(d)
        if val is None or dt is None:
            continue
        gap = abs((dt - target).days)
        if gap <= tol_days and gap < best_gap:
            best, best_gap = float(val), gap
    return best


def macro_state(conn: sqlite3.Connection) -> dict:
    """Current rates + derived risk-direction flags. Empty-ish when no data.

    Keys: ``repo_rate``, ``gsec_10y_yield``, ``gsec_10y_qoq_bps`` (signed),
    ``rising_yields`` (bool), ``repo_cut_recent`` (bool)."""
    try:
        rows = conn.execute(
            "SELECT as_of_date, repo_rate, gsec_10y_yield FROM raw_macro_rates "
            "ORDER BY as_of_date DESC LIMIT 400"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    out = {
        "repo_rate": None, "gsec_10y_yield": None, "gsec_10y_qoq_bps": None,
        "rising_yields": False, "repo_cut_recent": False,
    }
    if not rows:
        return out

    gsec_latest = _latest_non_null(rows, 1)
    if gsec_latest is not None:
        gdate, gval = gsec_latest
        out["gsec_10y_yield"] = gval
        prior = _value_near(rows, 1, gdate - _dt.timedelta(days=_QOQ_DAYS), tol_days=30)
        if prior is not None:
            bps = round((gval - prior) * 100.0)   # 1% = 100 bps
            out["gsec_10y_qoq_bps"] = bps
            out["rising_yields"] = bps >= _RISING_BPS

    repo_latest = _latest_non_null(rows, 0)
    if repo_latest is not None:
        rdate, rval = repo_latest
        out["repo_rate"] = rval
        prior = _value_near(rows, 0, rdate - _dt.timedelta(days=_REPO_WINDOW_DAYS), tol_days=45)
        if prior is not None and rval < prior - 1e-9:
            out["repo_cut_recent"] = True
    return out


# Risk class stamped on a BFSI earnings setup (S7).
NIM_TREASURY_RISK = "NIM_TREASURY_RISK"


def bfsi_earnings_risk(state: dict) -> tuple[str | None, str]:
    """(risk_class, human note) from a macro state for a BFSI name into a result.

    Returns (None, "") when the backdrop is benign. The class is the same string
    regardless of which leg fires; the note explains which (NIM / treasury / both)."""
    reasons: list[str] = []
    if state.get("repo_cut_recent"):
        reasons.append("recent repo cut → EBLR/T-bill repricing → NIM-compression risk")
    if state.get("rising_yields"):
        bps = state.get("gsec_10y_qoq_bps")
        reasons.append(
            f"10Y G-sec {bps:+d}bps QoQ → AFS treasury MTM-loss risk"
            if isinstance(bps, int) else "rising 10Y G-sec → treasury MTM-loss risk"
        )
    if not reasons:
        return None, ""
    return NIM_TREASURY_RISK, "; ".join(reasons)
