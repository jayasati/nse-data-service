"""Open PDFs from a list of fingerprints, show the LLM draft alongside.

Useful for sanity-checking new prompt versions against the actual PDFs.

Usage:
  PYTHONPATH=src python scripts/verify_drafts.py <fp1> <fp2> ...
  PYTHONPATH=src python scripts/verify_drafts.py --all-drafts
  PYTHONPATH=src python scripts/verify_drafts.py --recent 5
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import yaml

DB_PATH = Path("data/nse.db")
DRAFTS_DIR = Path("tests/financial_extraction/drafts")
GT_DIR = Path("tests/financial_extraction/ground_truth")

CANONICAL_FIELDS = [
    "revenue", "other_income", "total_income", "total_expenses",
    "pbt", "tax", "pat", "total_comprehensive_income",
    "eps_basic", "eps_diluted",
]


def open_pdf(pdf_path: str) -> None:
    """Open a PDF in the user's default Windows viewer (from WSL)."""
    if not pdf_path or not Path(pdf_path).exists():
        print(f"  WARN: PDF not on disk at {pdf_path}")
        return
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", pdf_path])
        elif sys.platform == "win32":
            subprocess.Popen(["cmd", "/c", "start", "", pdf_path], shell=False)
        else:
            # WSL: prefer wslview, then xdg-open, then explorer.exe
            for opener in ("wslview", "xdg-open"):
                if shutil.which(opener):
                    subprocess.Popen([opener, pdf_path])
                    return
            try:
                win_path = subprocess.check_output(
                    ["wslpath", "-w", pdf_path], text=True,
                ).strip()
                subprocess.Popen(["explorer.exe", win_path])
            except (FileNotFoundError, subprocess.CalledProcessError):
                print(f"  (couldn't auto-open; PDF is at {pdf_path})")
    except Exception as e:
        print(f"  (PDF open failed: {e})")


def show_draft_summary(fp: str) -> dict:
    """Load and display a draft. Return the parsed YAML."""
    draft_path = DRAFTS_DIR / f"{fp}.yaml"
    if not draft_path.exists():
        print(f"  No draft at {draft_path}")
        return {}

    with draft_path.open() as f:
        draft = yaml.safe_load(f) or {}

    standalone = draft.get("standalone") or {}
    meta = draft.get("_meta") or {}

    print(f"  Symbol:       {meta.get('symbol', '?')}")
    print(f"  Subject:      {meta.get('subject', '?')[:60]}")
    print(f"  Filed:        {meta.get('broadcast_dt', '?')}")
    print(f"  Period:       {draft.get('period_label')} ending {draft.get('period_ending')}")
    print(f"  Units:        {draft.get('units_in_source_pdf')}")
    print(f"  Table found:  {draft.get('table_found')}")
    print(f"  Notes:        {draft.get('notes', '')[:100]}")
    print()
    print(f"  Standalone values (raw, as-extracted):")
    for field in CANONICAL_FIELDS:
        val = standalone.get(field)
        print(f"    {field:<32} {val}")
    print()
    return draft


def show_ground_truth(fp: str) -> None:
    """If a ground truth exists for this fingerprint, show it."""
    gt_path = GT_DIR / f"{fp}.yaml"
    if not gt_path.exists():
        print("  (no ground truth saved yet)")
        return

    with gt_path.open() as f:
        gt = yaml.safe_load(f) or {}
    standalone = gt.get("standalone") or {}

    print(f"  Ground truth (your reviewed labels in Crore):")
    for field in CANONICAL_FIELDS:
        val = standalone.get(f"{field}_cr") if field not in ("eps_basic", "eps_diluted") \
              else standalone.get(field)
        # Fallback to the bare name if the _cr suffix isn't there
        if val is None:
            val = standalone.get(field)
        print(f"    {field:<32} {val}")
    print()


def fetch_pdf_path(db: sqlite3.Connection, fp: str) -> str | None:
    row = db.execute(
        "SELECT pdf_path FROM raw_announcements WHERE fingerprint=?",
        (fp,),
    ).fetchone()
    return row[0] if row and row[0] else None


def list_drafts(recent: int | None = None, all_drafts: bool = False) -> list[str]:
    """Get fingerprints from drafts dir, optionally limited."""
    if not DRAFTS_DIR.exists():
        return []
    drafts = sorted(DRAFTS_DIR.glob("*.yaml"),
                    key=lambda p: p.stat().st_mtime, reverse=True)
    if recent:
        drafts = drafts[:recent]
    elif not all_drafts:
        return []
    return [p.stem for p in drafts]


def main(args: argparse.Namespace) -> int:
    if args.fingerprints:
        fps = args.fingerprints
    elif args.recent:
        fps = list_drafts(recent=args.recent)
        print(f"Latest {len(fps)} drafts:")
    elif args.all_drafts:
        fps = list_drafts(all_drafts=True)
        print(f"All {len(fps)} drafts:")
    else:
        print("Specify fingerprints, --recent N, or --all-drafts", file=sys.stderr)
        return 1

    if not fps:
        print("No fingerprints to verify.")
        return 0

    db = sqlite3.connect(DB_PATH)
    try:
        for i, fp in enumerate(fps, 1):
            print()
            print("=" * 80)
            print(f"[{i}/{len(fps)}] {fp}")
            print("=" * 80)

            pdf_path = fetch_pdf_path(db, fp)
            if pdf_path:
                print(f"  PDF: {pdf_path}")
                if not args.no_open:
                    print("  Opening in viewer...")
                    open_pdf(pdf_path)
            else:
                print("  No PDF path in DB.")

            print()
            print("  --- LLM DRAFT ---")
            show_draft_summary(fp)
            print("  --- GROUND TRUTH (if any) ---")
            show_ground_truth(fp)

            if i < len(fps) and not args.no_pause:
                input("  Press Enter for next PDF (Ctrl+C to quit) ... ")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fingerprints", nargs="*",
                        help="Specific fingerprints to verify")
    parser.add_argument("--all-drafts", action="store_true",
                        help="Verify every draft on disk")
    parser.add_argument("--recent", type=int, default=None,
                        help="Verify the N most recent drafts")
    parser.add_argument("--no-open", action="store_true",
                        help="Don't auto-open PDFs (just show draft contents)")
    parser.add_argument("--no-pause", action="store_true",
                        help="Don't pause between PDFs")
    args = parser.parse_args()
    sys.exit(main(args))
