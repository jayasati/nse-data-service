#!/usr/bin/env python3
"""Promote human-verified extractions to ground-truth labels.

The old ground_truth/ labels are ~55% wrong (gpt-4o auto-drafts, never reviewed).
This turns every **'correct'** verdict from `verify_extraction.py` into a
trustworthy label: a human eyeballed those exact values against the PDF, so the
extracted numbers *are* the ground truth. Writes one canonical YAML per fixture,
keyed by fingerprint, with `reviewed: true`.

Wrong/partial verdicts are NOT promoted — their correct values aren't captured
(only a free-text note). Fix the extractor and re-verify (as we did for BEL), or
hand-correct those labels separately.

    python scripts/promote_verified_labels.py            # promote 'correct' verdicts
    python scripts/promote_verified_labels.py --dry-run  # show what would be written
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG = ROOT / "scripts/extraction_verify_log.jsonl"
GT_DIR = ROOT / "tests/financial_extraction/ground_truth"


def _period_label(period_ending: str | None) -> str | None:
    """Derive 'Q4-FY26' from a 'YYYY-MM-DD' quarter-end (Indian Apr–Mar FY)."""
    if not period_ending or len(period_ending) < 7:
        return None
    try:
        y, m = int(period_ending[:4]), int(period_ending[5:7])
    except ValueError:
        return None
    quarter = {6: "Q1", 9: "Q2", 12: "Q3", 3: "Q4"}.get(m)
    if quarter is None:
        return None
    fy = (y + 1) if m >= 4 else y      # Jun 2025 -> FY26 ; Mar 2026 -> FY26
    return f"{quarter}-FY{fy % 100:02d}"


def _label_doc(rec: dict) -> dict:
    """Build the canonical ground-truth YAML from one 'correct' verdict record."""
    return {
        "standalone": rec.get("fields") or {},
        "consolidated": rec.get("consolidated") or None,
        "period_label": _period_label(rec.get("period_ending")),
        "period_ending": rec.get("period_ending"),
        "units_in_source_pdf": "INR crore",   # values are crore-normalised
        "notes": "human-verified correct via verify_extraction.py",
        "_meta": {
            "fingerprint": rec["key"],
            "symbol": rec.get("symbol"),
            "reviewed": True,
            "source": "human_verified",
            "strategy": rec.get("strategy"),
            "confidence": rec.get("confidence"),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", default=str(DEFAULT_LOG))
    ap.add_argument("--out-dir", default=str(GT_DIR))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    log = Path(args.log)
    if not log.exists():
        print(f"No verify log at {log} — run verify_extraction.py first.")
        return 1
    out_dir = Path(args.out_dir)

    # Last verdict per fingerprint wins (re-verification supersedes).
    latest: dict[str, dict] = {}
    for line in log.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("key"):
            latest[r["key"]] = r

    promoted = skipped = 0
    for key, rec in latest.items():
        if rec.get("verdict") != "correct":
            skipped += 1
            continue
        if not rec.get("fields"):
            print(f"  skip {rec.get('symbol', key)}: no fields in record")
            skipped += 1
            continue
        doc = _label_doc(rec)
        dest = out_dir / f"{key}.yaml"
        if args.dry_run:
            print(f"  would write {dest.name}  ({rec.get('symbol')}, "
                  f"{len(doc['standalone'])} fields)")
        else:
            out_dir.mkdir(parents=True, exist_ok=True)
            with dest.open("w") as f:
                yaml.safe_dump(doc, f, sort_keys=False, allow_unicode=True)
            print(f"  wrote {dest.name}  ({rec.get('symbol')})")
        promoted += 1

    verb = "would promote" if args.dry_run else "promoted"
    print(f"\n{verb} {promoted} verified label(s); skipped {skipped} "
          f"(not 'correct' / no data).")
    if promoted and not args.dry_run:
        print(f"Labels in {out_dir} (gitignored — track with `git add -f` if sharing). "
              f"Run eval:  PYTHONPATH=src python tests/financial_extraction/eval.py --llm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
