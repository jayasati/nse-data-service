"""Interactive CLI for hand-labeling fixture PDFs.

Reads tests/parsers/fixtures/manifest.csv to find fixtures, opens each
in your default PDF viewer, and prompts for ground-truth pdf_type and
financial numbers. Writes results to tests/parsers/fixtures/labels.yaml.

Usage:
  PYTHONPATH=src python scripts/label_fixtures.py
  PYTHONPATH=src python scripts/label_fixtures.py --count 30
  PYTHONPATH=src python scripts/label_fixtures.py --skip-financial

Resume-safe: skips already-labeled fixtures. Run repeatedly to chip away
at the corpus.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

MANIFEST_PATH = Path("tests/parsers/fixtures/manifest.csv")
LABELS_PATH = Path("tests/parsers/fixtures/labels.yaml")

TYPE_CHOICES = {
    "1": "native_text",
    "2": "presentation",
    "3": "scanned",
    "4": "hybrid",
    "s": None,   # skip this fixture entirely
    "q": "__quit__",
}


def load_existing_labels() -> dict:
    if not LABELS_PATH.exists():
        return {}
    with LABELS_PATH.open() as f:
        return yaml.safe_load(f) or {}


def save_labels(labels: dict) -> None:
    LABELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LABELS_PATH.open("w") as f:
        yaml.safe_dump(labels, f, default_flow_style=False, sort_keys=True)


def open_pdf(path: str) -> None:
    """Open PDF in OS default viewer. Best-effort, non-blocking."""
    if not path or not Path(path).exists():
        return
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif sys.platform == "win32":
            subprocess.Popen(["cmd", "/c", "start", "", path], shell=False)
        else:
            # WSL: try wslview, then xdg-open, then explorer.exe via wslpath
            for opener in ("wslview", "xdg-open"):
                if shutil.which(opener):
                    subprocess.Popen([opener, path])
                    return
            # WSL last-resort: use Windows explorer
            try:
                win_path = subprocess.check_output(
                    ["wslpath", "-w", path], text=True
                ).strip()
                subprocess.Popen(["explorer.exe", win_path])
            except (FileNotFoundError, subprocess.CalledProcessError):
                print(f"  (couldn't auto-open; PDF is at {path})")
    except Exception as e:
        print(f"  (couldn't open PDF: {e})")


def prompt_pdf_type() -> str | None:
    """Prompt for pdf_type. Returns chosen type, None to skip, '__quit__'."""
    print("  pdf_type? [1=native_text, 2=presentation, 3=scanned, 4=hybrid, s=skip, q=quit]")
    while True:
        choice = input("  > ").strip().lower()
        if choice in TYPE_CHOICES:
            return TYPE_CHOICES[choice]
        print(f"  invalid choice '{choice}'; try again")


def prompt_optional_float(label: str) -> float | None:
    """Prompt for an optional float (revenue, PAT, EPS). Enter to skip."""
    raw = input(f"  {label} (Enter to skip): ").strip()
    if not raw:
        return None
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        print(f"  '{raw}' isn't a number; skipping")
        return None


def label_key(fixture_path: str) -> str:
    """Derive the YAML key from the fixture filename (drops .pdf)."""
    return Path(fixture_path).stem


def main(args: argparse.Namespace) -> int:
    if not MANIFEST_PATH.exists():
        print(f"ERROR: manifest not found. Run mine_fixtures.py first.",
              file=sys.stderr)
        return 1

    labels = load_existing_labels()
    print(f"Loaded {len(labels)} existing labels.")

    with MANIFEST_PATH.open() as f:
        rows = list(csv.DictReader(f))

    # Filter to fixtures that actually downloaded
    rows = [r for r in rows if r.get("pdf_path")]
    print(f"Manifest has {len(rows)} downloaded fixtures.")

    # Skip already-labeled
    unlabeled = [r for r in rows if label_key(r["pdf_path"]) not in labels]
    print(f"Unlabeled: {len(unlabeled)}")

    if args.count:
        unlabeled = unlabeled[:args.count]
        print(f"Limiting this session to {len(unlabeled)} fixtures.")

    labeled_this_session = 0
    for i, row in enumerate(unlabeled, 1):
        key = label_key(row["pdf_path"])
        print()
        print(f"--- [{i}/{len(unlabeled)}] {key} ---")
        print(f"  symbol:           {row['symbol']}")
        print(f"  subject:          {row['subject']}")
        print(f"  broadcast_dt:     {row['broadcast_dt']}")
        print(f"  classifier said:  {row['classifier_pdf_type']} "
              f"(confidence {row['classifier_confidence']})")

        open_pdf(row["pdf_path"])

        pdf_type = prompt_pdf_type()
        if pdf_type == "__quit__":
            print("Quitting.")
            break
        if pdf_type is None:
            print("  skipped.")
            continue

        entry: dict = {"pdf_type": pdf_type, "subject": row["subject"]}

        if not args.skip_financial:
            revenue = prompt_optional_float("revenue (Cr)")
            pat = prompt_optional_float("PAT (Cr)")
            eps = prompt_optional_float("EPS")
            fin = {}
            if revenue is not None:
                fin["revenue_cr"] = revenue
            if pat is not None:
                fin["pat_cr"] = pat
            if eps is not None:
                fin["eps"] = eps
            if fin:
                entry["financial"] = fin

        notes = input("  notes (Enter for none): ").strip()
        if notes:
            entry["notes"] = notes

        labels[key] = entry
        save_labels(labels)   # save after every entry — resume-safe
        labeled_this_session += 1

    print()
    print(f"Labeled this session: {labeled_this_session}")
    print(f"Total labels on disk: {len(labels)}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--count", type=int, default=None,
        help="Max fixtures to label this session (default: unlimited)",
    )
    parser.add_argument(
        "--skip-financial", action="store_true",
        help="Only label pdf_type; skip revenue/PAT/EPS prompts",
    )
    args = parser.parse_args()
    sys.exit(main(args))