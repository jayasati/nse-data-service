"""Generate blank ground-truth YAML stubs for hand-labeling.

Reads metadata.json, picks ~50 fixtures using a strategic order, writes one
pre-filled YAML stub per pick to tests/financial_extraction/ground_truth/.

Idempotent: never overwrites an existing stub. Re-run safely to add more
stubs as the corpus grows.

Strategic order:
  1. Outcome of Board Meeting   — cleanest result filings, highest priority
  2. Investor Presentation      — result-adjacent
  3. Press Release              — mixed; some result content, some not
  4. Dividend                   — usually attached to result filings
  5. Acquisition                — non-result but worth labeling as negative
  6. Two truly negative subjects (Updates, Newspaper Publication) — train
                                  the classifier on what isn't a result

Usage:
  PYTHONPATH=src python scripts/generate_label_stubs.py
  PYTHONPATH=src python scripts/generate_label_stubs.py --count 50
  PYTHONPATH=src python scripts/generate_label_stubs.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURE_ROOT = ROOT / "tests" / "financial_extraction" / "fixtures"
METADATA_PATH = FIXTURE_ROOT / "metadata.json"
GT_ROOT = ROOT / "tests" / "financial_extraction" / "ground_truth"

# Subject priority for labeling order. Higher = labeled earlier.
SUBJECT_PRIORITY = {
    "Outcome of Board Meeting": 100,
    "Investor Presentation": 80,
    "Press Release": 60,
    "Dividend": 40,
    "Acquisition": 20,
    "General Updates": 10,
    "Copy of Newspaper Publication": 10,
    "Updates": 10,
}

# Target mix — keys must match SUBJECT_PRIORITY. Values are target counts.
TARGET_MIX = {
    "Outcome of Board Meeting": 15,
    "Investor Presentation": 15,
    "Press Release": 10,
    "Dividend": 5,
    "Acquisition": 3,
    "General Updates": 1,            # negative example
    "Copy of Newspaper Publication": 1,  # negative example
}


STUB_TEMPLATE = """\
# Ground truth for: {symbol} - {subject}
# Filed: {broadcast_dt}
# PDF: {pdf_path}
# Source URL: {attachment_url}
#
# Open the PDF, fill in the fields below. Set fields to null where the PDF
# doesn't state them. See tests/financial_extraction/SCHEMA.md (or the
# original schema discussion) for field-by-field rules.

# ---- Identity (do not edit) ----
fingerprint: "{fingerprint}"
symbol: "{symbol}"
subject: "{subject}"
broadcast_dt: "{broadcast_dt}"
attachment_url: "{attachment_url}"

# ---- Primary triage (REQUIRED) ----
# Does this PDF report quarterly/annual results with structured numbers?
is_result_filing: null   # true | false

# Plain summary of what the PDF is about
pdf_actually_about: ""

# ---- Period (only if is_result_filing: true) ----
period:
  type: null              # quarterly | annual | half_yearly | nine_months
  from_date: null         # YYYY-MM-DD
  to_date: null           # YYYY-MM-DD
  fiscal_year: null
  audited: null

# ---- Financials (INR crore unless noted; EPS in INR per share) ----
financials:
  consolidated:
    revenue_from_operations: null
    other_income: null
    total_income: null
    total_expenses: null
    profit_before_tax: null
    tax_expense: null
    profit_after_tax: null
    eps_basic: null
    eps_diluted: null
  standalone:
    revenue_from_operations: null
    other_income: null
    total_income: null
    total_expenses: null
    profit_before_tax: null
    tax_expense: null
    profit_after_tax: null
    eps_basic: null
    eps_diluted: null

# ---- Comparison periods (transcribe from PDF; do not compute) ----
comparison:
  prior_quarter:
    period_label: null
    revenue_from_operations: null
    profit_after_tax: null
  prior_year_quarter:
    period_label: null
    revenue_from_operations: null
    profit_after_tax: null

# ---- Stated percentages (transcribe only what PDF prints) ----
stated_pct:
  yoy_revenue_pct: null
  yoy_profit_pct: null
  qoq_revenue_pct: null
  qoq_profit_pct: null

# ---- Flags (REQUIRED, set even for non-result filings) ----
flags:
  is_scanned_image: null               # true | false
  has_segment_breakdown: null
  has_auditor_qualification: null
  has_dividend_announcement: null
  language_non_english: null

# ---- Labeler notes (free-form) ----
notes: ""

# ---- Labeling metadata ----
labeled_by: ""
labeled_at: ""
labeling_method: ""        # manual | llm_assisted | mixed
labeling_confidence: ""    # high | medium | low
"""


def gt_path_for(symbol: str, fingerprint: str) -> Path:
    fp_short = fingerprint[:8] if fingerprint else "nofp"
    return GT_ROOT / f"{symbol}_{fp_short}.yaml"


def select_for_labeling(fixtures: list[dict], total_target: int) -> list[dict]:
    """Pick fixtures to label using the target mix.

    Iterates the TARGET_MIX in priority order. For each subject, takes up to
    its target count from available fixtures (diversifying by symbol).
    """
    by_subject: dict[str, list[dict]] = defaultdict(list)
    for f in fixtures:
        if f["subject"] in SUBJECT_PRIORITY:
            by_subject[f["subject"]].append(f)

    # Sort each subject's bucket: prefer May 2026, then April, then by symbol
    # (so the labeler sees the freshest filings first).
    for subj, items in by_subject.items():
        items.sort(key=lambda x: (-(x.get("broadcast_month") or "").__hash__() if False else 0,
                                  x.get("broadcast_month") or "",
                                  x["symbol"]),
                   reverse=True)

    selected: list[dict] = []
    per_symbol_in_picks: dict[str, int] = defaultdict(int)
    SYMBOL_CAP_IN_PICKS = 2

    # Walk TARGET_MIX in declared order (priority is encoded by ordering)
    for subject, want in TARGET_MIX.items():
        bucket = by_subject.get(subject, [])
        if not bucket:
            continue
        taken = 0
        for f in bucket:
            if taken >= want:
                break
            if per_symbol_in_picks[f["symbol"]] >= SYMBOL_CAP_IN_PICKS:
                continue
            selected.append(f)
            per_symbol_in_picks[f["symbol"]] += 1
            taken += 1

    # If under target_total, relax symbol cap and fill from highest-priority subjects
    if len(selected) < total_target:
        already_fps = {f["fingerprint"] for f in selected}
        ordered_subjects = sorted(by_subject.keys(),
                                  key=lambda s: -SUBJECT_PRIORITY.get(s, 0))
        for subject in ordered_subjects:
            for f in by_subject[subject]:
                if len(selected) >= total_target:
                    break
                if f["fingerprint"] in already_fps:
                    continue
                selected.append(f)
                already_fps.add(f["fingerprint"])
            if len(selected) >= total_target:
                break

    return selected


def write_stub(fixture: dict) -> Path:
    path = gt_path_for(fixture["symbol"], fixture["fingerprint"])
    content = STUB_TEMPLATE.format(
        fingerprint=fixture["fingerprint"],
        symbol=fixture["symbol"],
        subject=fixture["subject"],
        broadcast_dt=fixture["broadcast_dt"],
        attachment_url=fixture["attachment_url"],
        pdf_path=fixture["pdf_path"],
    )
    path.write_text(content)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=50,
                    help="Total stubs to generate (default 50)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would be written without creating files")
    args = ap.parse_args()

    if not METADATA_PATH.exists():
        print(f"ERROR: {METADATA_PATH} not found. Run the miner first.")
        sys.exit(1)

    with METADATA_PATH.open() as f:
        meta = json.load(f)
    fixtures = meta.get("fixtures", [])
    print(f"Loaded {len(fixtures)} fixtures from {METADATA_PATH.name}")

    GT_ROOT.mkdir(parents=True, exist_ok=True)
    existing_stubs = {p.name for p in GT_ROOT.glob("*.yaml")}
    print(f"Existing stubs: {len(existing_stubs)}")

    picks = select_for_labeling(fixtures, args.count)
    print(f"Selected {len(picks)} fixtures for labeling")
    print()

    # Group by subject for display
    by_subject_picked: dict[str, int] = defaultdict(int)
    for f in picks:
        by_subject_picked[f["subject"]] += 1
    print("Pick distribution by subject:")
    for subj, n in sorted(by_subject_picked.items(), key=lambda kv: -kv[1]):
        print(f"  {n:3d}  {subj}")
    print()

    if args.dry_run:
        print("Dry run — would write stubs for:")
        for f in picks:
            path = gt_path_for(f["symbol"], f["fingerprint"])
            marker = "[exists]" if path.name in existing_stubs else "[new]"
            print(f"  {marker} {path.name}  ({f['subject'][:35]})")
        return

    written = skipped = 0
    for f in picks:
        path = gt_path_for(f["symbol"], f["fingerprint"])
        if path.name in existing_stubs:
            skipped += 1
            continue
        write_stub(f)
        written += 1

    print(f"Done.")
    print(f"  written:  {written}")
    print(f"  skipped:  {skipped} (already existed)")
    print(f"  location: {GT_ROOT.relative_to(ROOT)}/")
    print()
    print("Next: open each YAML, set is_result_filing, fill the financials")
    print("if applicable, set the flags, save.")


if __name__ == "__main__":
    main()