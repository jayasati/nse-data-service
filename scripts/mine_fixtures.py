"""Mine fixture PDFs from production raw_announcements.

Downloads ~100 high-priority PDFs for use as test fixtures and a labeling
corpus. Idempotent — re-runs skip already-downloaded files.

Strategy:
  - 80 F&O symbols (from raw_fno_list), 20 non-F&O — for diversity
  - All priority='high' as classified by config/priority.yaml
  - Spread across the full broadcast_dt range available
  - Cap per-PDF size at 50MB; skip larger with warning
  - Run classifier on each downloaded PDF, write to manifest

Outputs:
  tests/parsers/fixtures/pdfs/<symbol>_<YYYYMMDD>_<fingerprint8>.pdf
  tests/parsers/fixtures/manifest.csv

Usage:
  PYTHONPATH=src python scripts/mine_fixtures.py
  PYTHONPATH=src python scripts/mine_fixtures.py --count 50 --fno-only
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import yaml

# Allow running as a script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nse_data.parsers.pdf_classifier import classify_pdf  # noqa: E402
from nse_data.session.manager import SessionManager  # noqa: E402

DB_PATH = Path("data/nse.db")
FIXTURE_DIR = Path("tests/parsers/fixtures/pdfs")
MANIFEST_PATH = Path("tests/parsers/fixtures/manifest.csv")
PRIORITY_CONFIG = Path("config/priority.yaml")
MAX_PDF_SIZE_MB = 50
DEFAULT_TARGET_COUNT = 100
DEFAULT_FNO_SHARE = 0.8


def load_priority_subjects() -> set[str]:
    """Return the set of subjects classified as 'high' priority."""
    with PRIORITY_CONFIG.open() as f:
        cfg = yaml.safe_load(f)
    return set(cfg["announcement_priority"]["high"])


def fetch_candidates(
    db: sqlite3.Connection,
    high_subjects: set[str],
    target_count: int,
    fno_share: float,
) -> list[dict]:
    """Pick candidate rows for fixture mining.

    Returns list of dicts with fingerprint, symbol, subject, attachment_url,
    broadcast_dt, is_fno.
    """
    # Subjects clause as parameterized IN
    placeholders = ",".join("?" * len(high_subjects))
    subjects = list(high_subjects)

    fno_count = int(target_count * fno_share)
    non_fno_count = target_count - fno_count

    # F&O candidates
    query_fno = f"""
        SELECT a.fingerprint, a.symbol, a.subject, a.attachment_url,
               a.broadcast_dt, 1 as is_fno
        FROM raw_announcements a
        INNER JOIN raw_fno_list f ON a.symbol = f.symbol
        WHERE a.subject IN ({placeholders})
          AND a.attachment_url IS NOT NULL
          AND a.attachment_url != ''
          AND a.attachment_url LIKE '%.pdf'
        ORDER BY a.broadcast_dt DESC
        LIMIT ?
    """
    fno_rows = db.execute(query_fno, [*subjects, fno_count * 3]).fetchall()
    fno_rows = _spread_by_date(fno_rows, fno_count)

    # Non-F&O candidates
    query_non_fno = f"""
        SELECT a.fingerprint, a.symbol, a.subject, a.attachment_url,
               a.broadcast_dt, 0 as is_fno
        FROM raw_announcements a
        LEFT JOIN raw_fno_list f ON a.symbol = f.symbol
        WHERE a.subject IN ({placeholders})
          AND f.symbol IS NULL
          AND a.attachment_url IS NOT NULL
          AND a.attachment_url != ''
          AND a.attachment_url LIKE '%.pdf'
        ORDER BY a.broadcast_dt DESC
        LIMIT ?
    """
    non_fno_rows = db.execute(
        query_non_fno, [*subjects, non_fno_count * 3]
    ).fetchall()
    non_fno_rows = _spread_by_date(non_fno_rows, non_fno_count)

    cols = ("fingerprint", "symbol", "subject", "attachment_url",
            "broadcast_dt", "is_fno")
    return [
        dict(zip(cols, row))
        for row in (fno_rows + non_fno_rows)
    ]


def _spread_by_date(rows: list[tuple], target: int) -> list[tuple]:
    """Subsample to spread evenly across the date range.

    Avoids clustering all fixtures on one or two days. Buckets rows by
    date prefix and takes round-robin until target is reached.
    """
    if len(rows) <= target:
        return rows

    by_date: dict[str, list] = {}
    for row in rows:
        broadcast_dt = row[4]
        date_key = broadcast_dt.split()[0] if broadcast_dt else "unknown"
        by_date.setdefault(date_key, []).append(row)

    selected = []
    dates = sorted(by_date.keys())
    idx_per_date = {d: 0 for d in dates}

    while len(selected) < target:
        progress = False
        for d in dates:
            if idx_per_date[d] < len(by_date[d]):
                selected.append(by_date[d][idx_per_date[d]])
                idx_per_date[d] += 1
                progress = True
                if len(selected) >= target:
                    break
        if not progress:
            break

    return selected


def fingerprint_short(fp: str) -> str:
    """First 8 chars of fingerprint, for filenames."""
    return fp[:8]


def broadcast_to_date_str(broadcast_dt: str) -> str:
    """Normalize NSE's 'DD-MMM-YYYY HH:MM:SS' to 'YYYYMMDD' for filenames."""
    try:
        dt = datetime.strptime(broadcast_dt.split()[0], "%d-%b-%Y")
        return dt.strftime("%Y%m%d")
    except (ValueError, IndexError):
        return "unknown"


def fixture_filename(row: dict) -> str:
    """Build the canonical fixture filename."""
    symbol = row["symbol"].replace("/", "_").replace(" ", "_")
    date_str = broadcast_to_date_str(row["broadcast_dt"])
    fp_short = fingerprint_short(row["fingerprint"])
    return f"{symbol}_{date_str}_{fp_short}.pdf"


def download_pdf(
    session: SessionManager, url: str, out_path: Path
) -> tuple[bool, str | None]:
    """Download a PDF via Layer 1's session manager.

    Returns (success, error_message). On success, writes file and returns
    (True, None). On failure, returns (False, "<reason>") without writing.
    """
    try:
        data = session.get_bytes(
            endpoint_name="fixture_mine_pdf",
            url=url,
            referer="https://www.nseindia.com/companies-listing/corporate-filings-announcements",
        )
    except Exception as e:
        return False, f"fetch_failed: {e!r}"

    if not data:
        return False, "empty_response"

    size_mb = len(data) / (1024 * 1024)
    if size_mb > MAX_PDF_SIZE_MB:
        return False, f"too_large: {size_mb:.1f}MB"

    # Quick sanity check: PDF magic bytes
    if not data.startswith(b"%PDF"):
        return False, "not_a_pdf"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    return True, None


def write_manifest(rows: list[dict]) -> None:
    """Write the fixture manifest CSV."""
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "fingerprint", "symbol", "subject", "broadcast_dt", "is_fno",
        "pdf_path", "file_size", "classifier_pdf_type",
        "classifier_confidence", "classifier_features_json",
        "classifier_error",
    ]
    with MANIFEST_PATH.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def main(args: argparse.Namespace) -> int:
    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}", file=sys.stderr)
        return 1

    high_subjects = load_priority_subjects()
    print(f"Loaded {len(high_subjects)} high-priority subjects.")

    db = sqlite3.connect(DB_PATH)
    db.row_factory = None  # tuples
    candidates = fetch_candidates(
        db, high_subjects, args.count, args.fno_share
    )
    db.close()
    print(f"Selected {len(candidates)} candidate rows for download.")

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    session = SessionManager()
    results: list[dict] = []
    downloaded = 0
    skipped_existing = 0
    failed = 0

    for i, row in enumerate(candidates, 1):
        filename = fixture_filename(row)
        out_path = FIXTURE_DIR / filename

        if out_path.exists():
            skipped_existing += 1
            # Still re-classify in case classifier code changed
            cls = classify_pdf(out_path)
            results.append({
                **row,
                "pdf_path": str(out_path),
                "file_size": out_path.stat().st_size,
                "classifier_pdf_type": cls.pdf_type,
                "classifier_confidence": cls.confidence,
                "classifier_features_json": json.dumps(cls.features),
                "classifier_error": cls.error or "",
            })
            continue

        print(f"[{i}/{len(candidates)}] {row['symbol']} {row['subject'][:40]}...",
              end=" ", flush=True)

        ok, err = download_pdf(session, row["attachment_url"], out_path)
        if not ok:
            print(f"SKIP ({err})")
            failed += 1
            results.append({
                **row,
                "pdf_path": "",
                "file_size": 0,
                "classifier_pdf_type": "",
                "classifier_confidence": "",
                "classifier_features_json": "",
                "classifier_error": err or "",
            })
            continue

        downloaded += 1
        # Classify immediately
        try:
            cls = classify_pdf(out_path)
            print(f"OK ({cls.pdf_type})")
            results.append({
                **row,
                "pdf_path": str(out_path),
                "file_size": out_path.stat().st_size,
                "classifier_pdf_type": cls.pdf_type,
                "classifier_confidence": cls.confidence,
                "classifier_features_json": json.dumps(cls.features),
                "classifier_error": cls.error or "",
            })
        except Exception as e:
            print(f"DOWNLOADED but classify failed: {e!r}")
            results.append({
                **row,
                "pdf_path": str(out_path),
                "file_size": out_path.stat().st_size,
                "classifier_pdf_type": "",
                "classifier_confidence": "",
                "classifier_features_json": "",
                "classifier_error": f"classify_failed: {e!r}",
            })

    session.close()

    write_manifest(results)

    print()
    print(f"Downloaded:        {downloaded}")
    print(f"Skipped (existed): {skipped_existing}")
    print(f"Failed:            {failed}")
    print(f"Manifest:          {MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--count", type=int, default=DEFAULT_TARGET_COUNT,
        help="Total fixture target count (default 100)",
    )
    parser.add_argument(
        "--fno-share", type=float, default=DEFAULT_FNO_SHARE,
        help="Fraction of fixtures from F&O universe (default 0.8)",
    )
    parser.add_argument(
        "--fno-only", action="store_true",
        help="Only mine F&O fixtures (override fno-share to 1.0)",
    )
    args = parser.parse_args()
    if args.fno_only:
        args.fno_share = 1.0
    sys.exit(main(args))