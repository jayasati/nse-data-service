"""Promote LLM drafts to ground truth via human review.

Loads each draft in tests/financial_extraction/drafts/, opens the
corresponding PDF for reference, and prompts the user to accept/edit/reject
each field. Validated drafts move to tests/financial_extraction/ground_truth/.

Resume-safe: skips drafts already promoted.

Usage:
  PYTHONPATH=src python scripts/review_labels.py
  PYTHONPATH=src python scripts/review_labels.py --count 10
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
GROUND_TRUTH_DIR = Path("tests/financial_extraction/ground_truth")

CANONICAL_FIELDS = [
    "revenue", "other_income", "total_income", "total_expenses",
    "pbt", "tax", "pat", "total_comprehensive_income",
    "eps_basic", "eps_diluted",
]


def open_pdf_in_viewer(pdf_path: str) -> None:
    """Best-effort PDF open in the user's default viewer."""
    if not pdf_path or not Path(pdf_path).exists():
        return
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", pdf_path])
        elif sys.platform == "win32":
            subprocess.Popen(["cmd", "/c", "start", "", pdf_path])
        else:
            # WSL: try wslview, xdg-open, then explorer.exe
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
                print(f"  (couldn't open PDF automatically; path: {pdf_path})")
    except Exception as e:
        print(f"  (PDF open failed: {e})")


def get_pdf_path(db: sqlite3.Connection, fingerprint: str) -> str | None:
    row = db.execute(
        "SELECT pdf_path FROM raw_announcements WHERE fingerprint=?",
        (fingerprint,),
    ).fetchone()
    return row[0] if row else None


def existing_truths() -> set[str]:
    if not GROUND_TRUTH_DIR.exists():
        return set()
    return {p.stem for p in GROUND_TRUTH_DIR.glob("*.yaml")}


UNIT_FACTORS = {
    "INR million": 0.1,    # 1 million = 0.1 Cr
    "INR lakh": 0.01,      # 1 lakh = 0.01 Cr
    "INR crore": 1.0,
    "INR thousand": 0.0001,
    "INR": 0.0000001,      # rupees to crore
}


def _convert_to_cr(raw_value, units: str | None) -> float | None:
    """Convert a raw extracted value to Crore based on units_in_source_pdf."""
    if raw_value is None:
        return None
    # The LLM sometimes returns strings like "20,964.47"
    if isinstance(raw_value, str):
        try:
            raw_value = float(raw_value.replace(",", "").strip())
        except ValueError:
            return None
    if not units:
        return float(raw_value)  # assume already Cr if unit unknown
    factor = UNIT_FACTORS.get(units, 1.0)
    return float(raw_value) * factor


def prompt_field(name: str, drafted_value, units: str | None,
                 is_eps: bool) -> tuple[bool, float | None]:
    """Prompt for one field. EPS doesn't convert units."""
    if drafted_value is None:
        raw_disp = "<null>"
        converted_disp = "<null>"
    else:
        raw_disp = str(drafted_value)
        if is_eps:
            converted_disp = raw_disp + " (rupees, no conversion)"
        else:
            converted = _convert_to_cr(drafted_value, units)
            converted_disp = (
                f"{converted:.2f} Cr (from {raw_disp} {units or '?'})"
                if converted is not None else raw_disp
            )

    print(f"    {name:<32}")
    print(f"      raw:        {raw_disp}")
    print(f"      converted:  {converted_disp}")
    print(f"    [Enter=accept converted | <number>=replace | n=null | q=quit] ",
          end="", flush=True)
    inp = input().strip().lower()

    if inp == "q":
        return False, None
    if inp == "":
        # Accept the converted value
        if drafted_value is None:
            return True, None
        if is_eps:
            return True, float(drafted_value) if not isinstance(drafted_value, str) \
                else float(drafted_value.replace(",", ""))
        return True, _convert_to_cr(drafted_value, units)
    if inp == "n":
        return True, None
    try:
        return True, float(inp.replace(",", "").replace("₹", "").strip())
    except ValueError:
        print(f"    '{inp}' not a number — keeping converted value")
        if is_eps or drafted_value is None:
            return True, float(drafted_value) if drafted_value is not None else None
        return True, _convert_to_cr(drafted_value, units)
    
def review_one(draft_path: Path, db: sqlite3.Connection) -> bool:
    """Review one draft. Returns True if review completed (truth promoted)."""
    fingerprint = draft_path.stem
    with draft_path.open() as f:
        draft = yaml.safe_load(f)

    meta = draft.get("_meta", {})
    symbol = meta.get("symbol", "?")
    subject = meta.get("subject", "?")
    bdt = meta.get("broadcast_dt", "?")
    standalone = draft.get("standalone", {}) or {}

    print("=" * 72)
    print(f"  {fingerprint[:16]}  {symbol}  ({bdt})")
    print(f"  Subject:    {subject}")
    print(f"  Period:     {draft.get('period_label')} ending {draft.get('period_ending')}")
    print(f"  Units:      {draft.get('units_in_source_pdf')}")
    print(f"  LLM notes:  {draft.get('notes', '')}")
    print("-" * 72)

    pdf_path = get_pdf_path(db, fingerprint)
    if pdf_path:
        print(f"  Opening PDF: {pdf_path}")
        open_pdf_in_viewer(pdf_path)
    else:
        print("  (no PDF on disk — review based on draft only)")

    print()
    print("  Review each field. Press Enter to accept drafted value.")
    print()

    reviewed: dict[str, float | None] = {}
    units = draft.get("units_in_source_pdf")
    for field in CANONICAL_FIELDS:
        is_eps = field.startswith("eps_")
        cont, value = prompt_field(field, standalone.get(field), units, is_eps)
        if not cont:
            return False
        # Store with _cr suffix to match ground truth schema
        key = field if is_eps else f"{field}_cr"
        reviewed[key] = value

    print()
    notes_in = input("  notes for ground truth (Enter for none): ").strip()

    truth = {
        "standalone": reviewed,
        "consolidated": None,
        "period_label": draft.get("period_label"),
        "period_ending": draft.get("period_ending"),
        "units_in_source_pdf": draft.get("units_in_source_pdf"),
        "notes": notes_in or draft.get("notes", ""),
        "_meta": {
            "fingerprint": fingerprint,
            "symbol": symbol,
            "subject": subject,
            "broadcast_dt": bdt,
            "reviewed": True,
            "draft_cost_usd": meta.get("cost_usd", 0),
        },
    }

    out_path = GROUND_TRUTH_DIR / f"{fingerprint}.yaml"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        yaml.safe_dump(truth, f, default_flow_style=False, sort_keys=False)

    print(f"  Saved ground truth: {out_path}")
    return True


def main(args: argparse.Namespace) -> int:
    if not DRAFTS_DIR.exists():
        print(f"ERROR: no drafts at {DRAFTS_DIR}. Run llm_label_drafts.py first.",
              file=sys.stderr)
        return 1

    truths = existing_truths()
    print(f"Already promoted: {len(truths)}")

    drafts = [p for p in sorted(DRAFTS_DIR.glob("*.yaml"))
              if p.stem not in truths]
    print(f"Drafts to review: {len(drafts)}")

    if args.count:
        drafts = drafts[:args.count]
        print(f"Limiting session to {len(drafts)}.")

    db = sqlite3.connect(DB_PATH)
    reviewed_count = 0
    for draft_path in drafts:
        try:
            if review_one(draft_path, db):
                reviewed_count += 1
            else:
                print("Quitting.")
                break
        except KeyboardInterrupt:
            print("\nInterrupted.")
            break

    db.close()
    print(f"\nReviewed this session: {reviewed_count}")
    print(f"Total ground truths:   {len(existing_truths())}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=None,
                        help="Max drafts to review this session")
    args = parser.parse_args()
    sys.exit(main(args))