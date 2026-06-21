"""Eval harness for the financial extractor (FEATURE_CHECKLIST Week 17, 17.5).

Runs every labeled fixture through ``financial_extractor.extract`` and compares
the result against the hand-labeled ground truth (the ``standalone`` block, in
crore). Reports:

  * accuracy per field — % of fixtures where the extracted value is within
    tolerance of the label
  * accuracy per strategy — which strategy won, and how often it was right
  * failure cases — per fixture, which fields were wrong/missing and why

Gate: ≥90% (target 95%) field accuracy on the result-PDF fixtures.

Usage:
  PYTHONPATH=src python tests/financial_extraction/eval.py
  PYTHONPATH=src python tests/financial_extraction/eval.py --field revenue_cr
  PYTHONPATH=src python tests/financial_extraction/eval.py --verbose
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Load .env so the GPT-4o fallback (Azure OpenAI creds) is available under --llm.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from nse_data.parsers.financial_extractor import extract  # noqa: E402
import loader  # noqa: E402

# Match rules. The headline tolerance is 2% relative (the spec). On top of that,
# an absolute floor absorbs source-rounding noise on small line items: NSE PDFs
# report in lakh/thousand, and the lakh→crore conversion + OCR leave ~0.1-0.2cr
# of slop on a small number (e.g. a ₹3cr tax line) that a flat 2% can't tolerate.
REL_TOL = 0.02
AMOUNT_ABS_FLOOR = 0.25   # crore; ~25 lakh of rounding headroom on small lines
EPS_ABS_FLOOR = 0.05      # rupees; EPS is reported to 2 decimals
EPS_FIELDS = {"eps_basic", "eps_diluted"}


def _close(got: float, want: float, field: str) -> bool:
    if field in EPS_FIELDS:
        return abs(got - want) <= max(EPS_ABS_FLOOR, abs(want) * REL_TOL)
    return abs(got - want) <= max(abs(want) * REL_TOL, AMOUNT_ABS_FLOOR)


def evaluate(only_field: str | None = None, verbose: bool = False,
             use_llm: bool = False, limit: int | None = None) -> int:
    fixtures = [f for f in loader.load_fixtures() if f.ground_truth]
    if not fixtures:
        print("No labeled fixtures found. Run scripts/rehydrate_corpus.py first.")
        return 1
    if limit:
        fixtures = fixtures[:limit]

    per_field = defaultdict(lambda: {"correct": 0, "total": 0, "missing": 0})
    per_strategy = defaultdict(lambda: {"correct": 0, "total": 0})
    failures: list[str] = []
    total_llm_cost = 0.0

    mode = "HYBRID (ensemble + LLM fallback)" if use_llm else "ensemble-only"
    print(f"Evaluating {len(fixtures)} labeled fixtures [{mode}]...\n")

    for i, fx in enumerate(fixtures, 1):
        gt = (fx.ground_truth or {}).get("standalone") or {}
        if not gt:
            continue
        # Print BEFORE extract so the slow camelot pass (≈100s on big PDFs) shows
        # as live progress instead of a frozen screen.
        print(f"[{i}/{len(fixtures)}] {fx.symbol:14s} extracting…", flush=True)
        res = extract(
            fx.pdf_path,
            use_llm_fallback=use_llm,
            symbol=fx.symbol,
            subject=fx.subject,
            broadcast_dt=fx.broadcast_dt,
        )
        total_llm_cost += res.llm_cost_usd
        fixture_fails: list[str] = []

        def score(gt_block: dict, got_block: dict, scope: str) -> None:
            for fname, want in gt_block.items():
                if only_field and fname != only_field:
                    continue
                if want is None:
                    continue
                per_field[fname]["total"] += 1
                got = got_block.get(fname)
                tag = fname if scope == "standalone" else f"{fname}(cons)"
                if got is None:
                    per_field[fname]["missing"] += 1
                    fixture_fails.append(f"{tag}: MISSING (want {want})")
                    continue
                if _close(got, want, fname):
                    per_field[fname]["correct"] += 1
                    per_strategy[res.strategy]["correct"] += 1
                else:
                    fixture_fails.append(f"{tag}: got {got:.4g} want {want:.4g}")
                per_strategy[res.strategy]["total"] += 1

        score(gt, res.fields, "standalone")
        # Score consolidated only when the label provides it (most are null).
        gt_cons = (fx.ground_truth or {}).get("consolidated")
        if isinstance(gt_cons, dict) and gt_cons:
            score(gt_cons, res.consolidated, "consolidated")

        if fixture_fails:
            tag = f"{fx.symbol} [{res.strategy} conf={res.confidence:.2f} units={res.units_phrase}]"
            failures.append(f"  {tag}\n      " + "\n      ".join(fixture_fails))
            if verbose:
                print(f"✗ {tag}")
                for fail in fixture_fails:
                    print(f"      {fail}")
        elif verbose:
            print(f"✓ {fx.symbol} [{res.strategy}]")

    # ---- report ----
    print("\n" + "=" * 60)
    print("ACCURACY PER FIELD")
    print("=" * 60)
    grand_correct = grand_total = 0
    for fname in sorted(per_field):
        s = per_field[fname]
        grand_correct += s["correct"]
        grand_total += s["total"]
        pct = 100 * s["correct"] / s["total"] if s["total"] else 0
        print(f"  {fname:32s} {s['correct']:3d}/{s['total']:<3d} = {pct:5.1f}%  "
              f"(missing {s['missing']})")

    overall = 100 * grand_correct / grand_total if grand_total else 0
    print(f"\n  OVERALL: {grand_correct}/{grand_total} = {overall:.1f}%  "
          f"(gate: 90%, target: 95%)")
    if total_llm_cost:
        print(f"  LLM fallback cost: ${total_llm_cost:.4f}")

    print("\n" + "=" * 60)
    print("ACCURACY PER WINNING STRATEGY")
    print("=" * 60)
    for strat in sorted(per_strategy):
        s = per_strategy[strat]
        pct = 100 * s["correct"] / s["total"] if s["total"] else 0
        print(f"  {strat:20s} {s['correct']:3d}/{s['total']:<3d} = {pct:5.1f}%")

    if failures:
        print("\n" + "=" * 60)
        print(f"FAILURE CASES ({len(failures)} fixtures)")
        print("=" * 60)
        for f in failures:
            print(f)

    return 0 if overall >= 90 else 2


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--field", default=None, help="Evaluate a single field only")
    ap.add_argument("--verbose", action="store_true", help="Per-fixture pass/fail")
    ap.add_argument("--llm", action="store_true",
                    help="Enable GPT-4o fallback for low-confidence PDFs (costs API $)")
    ap.add_argument("--limit", type=int, default=None, help="Evaluate only the first N fixtures")
    args = ap.parse_args()
    sys.exit(evaluate(args.field, args.verbose, args.llm, args.limit))
