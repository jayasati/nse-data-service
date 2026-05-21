"""Mine announcement PDF fixtures for Layer 3 (classifier + extractor + sentiment).

Source: raw_announcements (NOT raw_financial_results — that table has metadata
only, no PDF URLs). Filters to F&O universe by default. Subject-agnostic — the
hand-labeling step decides what each PDF actually contains.

Outputs:
  tests/financial_extraction/fixtures/pdfs/<symbol>_<fp8>.pdf
  tests/financial_extraction/fixtures/metadata.json

Re-run safe: idempotent on existing fixtures.

Usage:
  PYTHONPATH=src python scripts/mine_announcement_fixtures.py --dry-run
  PYTHONPATH=src python scripts/mine_announcement_fixtures.py --target 200
  PYTHONPATH=src python scripts/mine_announcement_fixtures.py --include-non-fno
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nse_data.parsers._utils import safe_filename  # noqa: E402
from nse_data.session.manager import SessionManager  # noqa: E402

DB_PATH = ROOT / "data" / "nse.db"
FIXTURE_ROOT = ROOT / "tests" / "financial_extraction" / "fixtures"
PDF_DIR = FIXTURE_ROOT / "pdfs"
METADATA_PATH = FIXTURE_ROOT / "metadata.json"
REFERER = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"


# ---------- metadata I/O ----------

def load_existing_metadata() -> dict[str, dict]:
    if not METADATA_PATH.exists():
        return {}
    with METADATA_PATH.open() as f:
        data = json.load(f)
    return {entry["fingerprint"]: entry for entry in data.get("fixtures", [])}


def save_metadata(fixtures: list[dict]) -> None:
    METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    out = {
        "schema_version": 2,
        "source": "raw_announcements",
        "generated_count": len(fixtures),
        "fixtures": sorted(fixtures, key=lambda x: (x["symbol"], x["broadcast_dt"])),
    }
    with METADATA_PATH.open("w") as f:
        json.dump(out, f, indent=2)


# ---------- date / file helpers ----------

def month_bucket(broadcast_dt: str | None) -> str | None:
    if not broadcast_dt or broadcast_dt.strip() in ("", "-"):
        return None
    s = broadcast_dt.strip()
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m")
        except ValueError:
            continue
    return None


def fixture_path(symbol: str, fingerprint: str) -> Path:
    fp_short = fingerprint[:8] if fingerprint else "nofp"
    return PDF_DIR / f"{safe_filename(symbol)}_{fp_short}.pdf"


# ---------- candidate selection ----------

def select_candidates(
    db: sqlite3.Connection,
    target: int,
    per_symbol_cap: int,
    months_back: int,
    include_non_fno: bool,
    subjects: list[str] | None = None,
) -> list[dict]:
    """Pick announcement rows to mine.

    Strategy: filter to URL-bearing rows in (optionally) F&O universe, bucket
    by broadcast month, take an even slice across the most recent N months,
    cap per symbol. If under target, relax the cap and re-fill.
    """
    fno_clause = "" if include_non_fno else "JOIN raw_fno_list f ON f.symbol = a.symbol"
    subject_clause = ""
    subject_params: tuple = ()
    if subjects:
        placeholders = ",".join("?" * len(subjects))
        subject_clause = f"AND a.subject IN ({placeholders})"
        subject_params = tuple(subjects)

    cur = db.execute(f"""
        SELECT a.fingerprint    AS fingerprint,
               a.segment        AS segment,
               a.symbol         AS symbol,
               a.company_name   AS company_name,
               a.subject        AS subject,
               a.details        AS details,
               a.attachment_url AS attachment_url,
               a.broadcast_dt   AS broadcast_dt,
               a.created_at     AS created_at
          FROM raw_announcements a
          {fno_clause}
         WHERE a.attachment_url IS NOT NULL
           AND a.attachment_url != ''
           AND a.attachment_url != '-'
           {subject_clause}
         ORDER BY a.broadcast_dt DESC
    """,subject_params)
    rows = [dict(zip([c[0] for c in cur.description], row)) for row in cur.fetchall()]
    scope = "all-universe" if include_non_fno else "F&O"
    subj_desc = f", subjects={len(subjects)}" if subjects else ""
    print(f"  found {len(rows)} {scope} announcement rows with attachment_url{subj_desc}")
    if not rows:
        return []

    by_month: dict[str, list[dict]] = defaultdict(list)
    unparsed = 0
    for r in rows:
        m = month_bucket(r["broadcast_dt"])
        if m is None:
            unparsed += 1
            continue
        r["broadcast_month"] = m
        by_month[m].append(r)
    if unparsed:
        print(f"  warning: {unparsed} rows had unparseable broadcast_dt — skipped")

    top_months = sorted(by_month.keys(), reverse=True)[:months_back]
    print(f"  using months: {top_months}")

    per_m = max(1, target // len(top_months)) if top_months else 0
    selected: list[dict] = []
    per_symbol_counts: dict[str, int] = defaultdict(int)

    for m in top_months:
        bucket = by_month[m]
        taken = 0
        for r in bucket:
            if taken >= per_m:
                break
            if per_symbol_counts[r["symbol"]] >= per_symbol_cap:
                continue
            selected.append(r)
            per_symbol_counts[r["symbol"]] += 1
            taken += 1
        print(f"  month {m}: selected {taken} / {len(bucket)} available")

    # Relax cap if under target
    if len(selected) < target:
        already = {r["fingerprint"] for r in selected}
        for m in top_months:
            for r in by_month[m]:
                if len(selected) >= target:
                    break
                if r["fingerprint"] in already:
                    continue
                selected.append(r)
                already.add(r["fingerprint"])
            if len(selected) >= target:
                break
        print(f"  after cap relaxation: {len(selected)} total")

    return selected


# ---------- download ----------

def download_pdf(session: SessionManager, url: str) -> bytes | None:
    try:
        return session.get_bytes(
            endpoint_name="fixture_mining",
            url=url,
            referer=REFERER,
        )
    except Exception as exc:
        print(f"    ! download failed: {type(exc).__name__}: {exc}")
        return None


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, default=200,
                    help="Target number of fixtures (default 200)")
    ap.add_argument("--per-symbol", type=int, default=3,
                    help="Soft cap per company (default 3); relaxed if under target")
    ap.add_argument("--months-back", type=int, default=6,
                    help="Recent broadcast months to span (default 6)")
    ap.add_argument("--include-non-fno", action="store_true",
                    help="Include non-F&O symbols")
    ap.add_argument("--max-pdf-bytes", type=int, default=50 * 1024 * 1024,
                    help="Skip PDFs larger than this (default 50MB)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Pick candidates but do not download")
    ap.add_argument("--subjects", type=str, default=None,
                        help="Comma-separated subject list to filter on (exact match). "
                            "Example: \"Outcome of Board Meeting,Investor Presentation\"")
    args = ap.parse_args()
    subjects = None
    if args.subjects:
        subjects = [s.strip() for s in args.subjects.split(",") if s.strip()]
        print(f"  subject filter: {subjects}")

    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}")
        sys.exit(1)

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    existing = load_existing_metadata()
    print(f"Mining fixtures (target={args.target}, per_symbol_cap={args.per_symbol})")
    print(f"  existing fixtures: {len(existing)}")

    db = sqlite3.connect(DB_PATH)
    candidates = select_candidates(
        db, args.target, args.per_symbol, args.months_back, args.include_non_fno,subjects,
    )
    db.close()

    print(f"\nSelected {len(candidates)} candidates")
    if args.dry_run:
        print("\nDry run preview (first 20):")
        for r in candidates[:20]:
            subj = (r["subject"] or "")[:45]
            print(f"  {r['symbol']:14s} {r['broadcast_month']}  {subj}")
        if len(candidates) > 20:
            print(f"  ... and {len(candidates) - 20} more")
        return

    session = SessionManager()
    fixtures = list(existing.values())
    new_count = skip_existing = skip_failed = skip_oversize = 0

    try:
        for i, r in enumerate(candidates, 1):
            fp = r["fingerprint"]
            path = fixture_path(r["symbol"], fp)

            if fp in existing:
                skip_existing += 1
                continue

            if path.exists():
                # Disk-present but not in metadata — record it
                fixtures.append(_build_metadata_entry(r, path, path.stat().st_size))
                skip_existing += 1
                continue

            print(f"[{i}/{len(candidates)}] {r['symbol']} {r['broadcast_month']} ...",
                  end=" ", flush=True)
            blob = download_pdf(session, r["attachment_url"])
            if blob is None:
                skip_failed += 1
                print("FAILED")
                continue
            if len(blob) > args.max_pdf_bytes:
                skip_oversize += 1
                print(f"SKIP ({len(blob)//1024} KB > cap)")
                continue
            if not blob.startswith(b"%PDF"):
                skip_failed += 1
                print(f"SKIP (not a PDF, magic={blob[:8]!r})")
                continue

            path.write_bytes(blob)
            fixtures.append(_build_metadata_entry(r, path, len(blob)))
            new_count += 1
            print(f"OK ({len(blob)//1024} KB)")
    finally:
        session.close()
        save_metadata(fixtures)

    print()
    print(f"Done. Total fixtures: {len(fixtures)}")
    print(f"  new this run:    {new_count}")
    print(f"  already on disk: {skip_existing}")
    print(f"  download failed: {skip_failed}")
    print(f"  oversize/bad:    {skip_oversize}")
    print(f"  metadata:        {METADATA_PATH.relative_to(ROOT)}")


def _build_metadata_entry(r: dict, path: Path, size_bytes: int) -> dict:
    return {
        "fingerprint": r["fingerprint"],
        "symbol": r["symbol"],
        "company_name": r.get("company_name"),
        "subject": r["subject"],
        "details": (r.get("details") or "")[:500],
        "segment": r.get("segment"),
        "broadcast_dt": r["broadcast_dt"],
        "broadcast_month": r.get("broadcast_month"),
        "attachment_url": r["attachment_url"],
        "pdf_path": str(path.relative_to(ROOT)),
        "size_bytes": size_bytes,
    }


if __name__ == "__main__":
    main()