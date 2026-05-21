"""Print coverage of the fixture set + labeling progress."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from tests.financial_extraction.loader import coverage_summary  # noqa: E402


def main():
    s = coverage_summary()
    print(f"Total fixtures:    {s['total_fixtures']}")
    print(f"Labeled fixtures:  {s['labeled_fixtures']} (target ~50 for extractor eval)")
    print(f"Unique symbols:    {s['unique_symbols']}")
    print()
    print("By broadcast month:")
    for m, n in s["by_month"].items():
        print(f"  {m:8s} {n:4d}")
    print()
    print("Top subjects (← labeler picks the result-bearing ones to label):")
    for subj, n in s["by_subject"].items():
        print(f"  {n:4d}  {subj[:60]}")
    print()
    print("Top 10 symbols by fixture count:")
    for sym, n in s["top_symbols_by_count"].items():
        print(f"  {sym:14s} {n:4d}")


if __name__ == "__main__":
    main()