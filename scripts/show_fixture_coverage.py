"""Report on fixture corpus composition and classifier output.

Reads the manifest and prints:
  - PDF type distribution
  - Top subjects represented
  - F&O vs non-F&O split
  - OCR decision suggestion based on scanned share
  - Labeling progress

Usage:
  PYTHONPATH=src python scripts/show_fixture_coverage.py
"""

from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

import yaml

MANIFEST_PATH = Path("tests/parsers/fixtures/manifest.csv")
LABELS_PATH = Path("tests/parsers/fixtures/labels.yaml")

OCR_THRESHOLD = 0.15  # scanned share above this = OCR worth building upfront


def load_manifest() -> list[dict]:
    if not MANIFEST_PATH.exists():
        print(f"ERROR: manifest not found at {MANIFEST_PATH}", file=sys.stderr)
        sys.exit(1)
    with MANIFEST_PATH.open() as f:
        return list(csv.DictReader(f))


def load_labels() -> dict:
    if not LABELS_PATH.exists():
        return {}
    with LABELS_PATH.open() as f:
        return yaml.safe_load(f) or {}


def print_section(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def main() -> int:
    rows = load_manifest()
    labels = load_labels()
    downloaded = [r for r in rows if r.get("pdf_path")]

    print_section(f"Fixture Corpus Summary (n={len(downloaded)} downloaded, "
                  f"{len(rows)} attempted)")

    failed = [r for r in rows if not r.get("pdf_path")]
    if failed:
        print(f"  Failed downloads: {len(failed)}")
        err_counts = Counter(
            (r.get("classifier_error") or "unknown").split(":")[0]
            for r in failed
        )
        for err, n in err_counts.most_common(5):
            print(f"    {err}: {n}")

    # ---- PDF type distribution ----
    print_section("PDF Type Distribution (classifier output)")
    type_counts = Counter(
        r["classifier_pdf_type"] for r in downloaded
        if r.get("classifier_pdf_type")
    )
    total = sum(type_counts.values())
    for pdf_type, n in type_counts.most_common():
        pct = 100 * n / total if total else 0
        print(f"  {pdf_type:15s} : {n:3d} ({pct:5.1f}%)")

    # ---- Top subjects ----
    print_section("Top Subjects in Corpus")
    subject_counts = Counter(r["subject"] for r in downloaded)
    for subject, n in subject_counts.most_common(10):
        truncated = subject[:60] + ("..." if len(subject) > 60 else "")
        print(f"  {n:3d}  {truncated}")

    # ---- F&O split ----
    print_section("Universe Split")
    fno = sum(1 for r in downloaded if r.get("is_fno") in ("1", 1, True))
    non_fno = len(downloaded) - fno
    print(f"  F&O universe:    {fno}")
    print(f"  Non-F&O:         {non_fno}")

    # ---- OCR decision ----
    print_section("OCR Decision (informs Phase 4 vs Phase 8)")
    scanned_n = type_counts.get("scanned", 0)
    scanned_pct = (scanned_n / total) if total else 0
    print(f"  Scanned share: {scanned_n}/{total} ({scanned_pct*100:.1f}%)")
    print(f"  Threshold for upfront OCR: {OCR_THRESHOLD*100:.0f}%")
    if scanned_pct >= OCR_THRESHOLD:
        print("  -> Recommendation: include OCR in Phase 4.")
    else:
        print("  -> Recommendation: defer OCR to Phase 8.")
        print("     Mark scanned PDFs as 'ocr_required' for now;")
        print("     re-evaluate after 2-3 weeks of production data.")

    # ---- Labeling progress ----
    print_section("Hand-labeling Progress")
    label_count = len(labels)
    pct = (label_count / len(downloaded) * 100) if downloaded else 0
    print(f"  Labeled: {label_count}/{len(downloaded)} ({pct:.1f}%)")
    if label_count < 30:
        gap = 30 - label_count
        print(f"  Need {gap} more for the Phase 1 acceptance gate (≥30).")

    # Per-type labeling coverage
    if labels:
        type_labels = Counter(v["pdf_type"] for v in labels.values()
                              if "pdf_type" in v)
        print("  By type:")
        for pdf_type, n in type_labels.most_common():
            print(f"    {pdf_type:15s} : {n}")

    return 0


if __name__ == "__main__":
    sys.exit(main())