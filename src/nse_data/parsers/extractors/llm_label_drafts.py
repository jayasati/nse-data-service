"""Generate LLM-drafted ground truth labels for financial extractor training.

Reads high-priority result PDFs from raw_announcements, sends the extracted
text to gpt-4o, asks for the 10 canonical fields in structured JSON. Saves
each draft as YAML for human review via scripts/review_labels.py.

Resume-safe: skips PDFs already drafted.

Usage:
  PYTHONPATH=src python scripts/llm_label_drafts.py
  PYTHONPATH=src python scripts/llm_label_drafts.py --count 100
  PYTHONPATH=src python scripts/llm_label_drafts.py --count 5 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Load .env BEFORE importing LLMClient (it reads env vars at construction)
load_dotenv()

from nse_data.parsers.extractors.llm_client import (  # noqa: E402
    DailyCapExceeded, LLMClient,
)

DB_PATH = Path("data/nse.db")
DRAFTS_DIR = Path("tests/financial_extraction/drafts")
RESULT_SUBJECTS = (
    "Outcome of Board Meeting",
    "Reply to Clarification- Financial results",
    "Clarification - Financial Results",
    "Integrated Filing- Financial",
)

# The structured extraction prompt. Pure function on pdf_text.
SYSTEM_PROMPT = """You are a financial data extractor for Indian quarterly result \
PDFs filed with NSE. Extract the 10 canonical financial fields below. All \
amounts must be returned in Crore (1 Cr = 10 million = 100 lakh). Convert \
from the source PDF's unit if it differs. EPS is in rupees, NOT crore.

Return ONLY a JSON object matching this exact schema:
{
  "standalone": {
    "revenue_cr": <number or null>,
    "other_income_cr": <number or null>,
    "total_income_cr": <number or null>,
    "total_expenses_cr": <number or null>,
    "pbt_cr": <number or null>,
    "tax_cr": <number or null>,
    "pat_cr": <number or null>,
    "total_comprehensive_income_cr": <number or null>,
    "eps_basic": <number or null>,
    "eps_diluted": <number or null>
  },
  "period_label": "<e.g. 'Q4-FY26' or 'Q3-FY26'>",
  "period_ending": "<YYYY-MM-DD>",
  "units_in_source_pdf": "<'INR million' | 'INR lakh' | 'INR crore'>",
  "notes": "<brief observations: company name, BFSI/normal, anomalies>"
}

Rules:
1. Use STANDALONE numbers, NOT consolidated. If only consolidated exists, use it and note "consolidated_only" in notes.
2. Use the MOST RECENT QUARTER column (usually leftmost numeric column).
3. If a field is genuinely not present (e.g. eps_diluted not shown, or the PDF \
is a banking sector format that doesn't use 'Revenue from operations'), \
return null.
4. For banking PDFs (banks/NBFCs), 'Net Interest Income' or 'Interest Earned' \
is closest to revenue. Return that as revenue_cr and note "bfsi_format" in notes.
5. NEVER guess. If unsure, return null.
"""


def fetch_candidates(
    db: sqlite3.Connection,
    target_count: int,
    drafted_already: set[str],
) -> list[dict]:
    """Pick high-priority result PDFs not yet drafted."""
    placeholders = ",".join("?" * len(RESULT_SUBJECTS))
    rows = db.execute(
        f"""
        SELECT fingerprint, symbol, subject, broadcast_dt, pdf_text,
               pdf_text_length
          FROM raw_announcements
         WHERE pdf_status = 'text_extracted'
           AND priority = 'high'
           AND subject IN ({placeholders})
           AND pdf_text IS NOT NULL
           AND pdf_text_length > 1000
         ORDER BY broadcast_dt DESC
         LIMIT ?
        """,
        (*RESULT_SUBJECTS, target_count * 3),  # 3x oversample
    ).fetchall()

    cols = ("fingerprint", "symbol", "subject", "broadcast_dt",
            "pdf_text", "pdf_text_length")
    candidates = [dict(zip(cols, r)) for r in rows]
    # Filter out already-drafted
    candidates = [c for c in candidates if c["fingerprint"] not in drafted_already]
    return candidates[:target_count]


def existing_drafts() -> set[str]:
    """Return fingerprints that already have drafts on disk."""
    if not DRAFTS_DIR.exists():
        return set()
    return {p.stem for p in DRAFTS_DIR.glob("*.yaml")}


def draft_one(client: LLMClient, row: dict) -> dict | None:
    """Send one PDF's text to the LLM and parse the response."""
    text = row["pdf_text"]
    # Truncate very long text — most result PDFs have the P&L in first 3 pages.
    # Token budget: 1 token ≈ 4 chars; 12000 chars ≈ 3000 input tokens ≈ $0.0075.
    if len(text) > 12000:
        text = text[:12000] + "\n\n[...text truncated for token budget...]"

    user_msg = (
        f"Company: {row['symbol']}\n"
        f"Filing date: {row['broadcast_dt']}\n"
        f"Subject: {row['subject']}\n\n"
        f"PDF text:\n---\n{text}\n---"
    )

    result = client.chat_completion(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        max_tokens=2000,
        temperature=0.0,
    )

    if not result.success:
        return {
            "_error": result.error,
            "_cost_usd": result.cost_usd,
        }

    return {
        **(result.parsed_json or {}),
        "_meta": {
            "model": "gpt-4o",
            "cost_usd": round(result.cost_usd, 6),
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "symbol": row["symbol"],
            "subject": row["subject"],
            "broadcast_dt": row["broadcast_dt"],
            "fingerprint": row["fingerprint"],
        },
    }


def main(args: argparse.Namespace) -> int:
    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}", file=sys.stderr)
        return 1

    drafted = existing_drafts()
    print(f"Already drafted: {len(drafted)}")

    db = sqlite3.connect(DB_PATH)
    candidates = fetch_candidates(db, args.count, drafted)
    db.close()

    print(f"Candidates selected: {len(candidates)}")
    if not candidates:
        print("Nothing to do.")
        return 0

    if args.dry_run:
        print("\nDRY RUN — would draft these:")
        for c in candidates[:10]:
            print(f"  {c['fingerprint'][:12]}  {c['symbol']:<14}  "
                  f"{c['subject'][:50]}  ({c['pdf_text_length']} chars)")
        if len(candidates) > 10:
            print(f"  ... and {len(candidates) - 10} more")
        return 0

    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)

    client = LLMClient()
    print(f"Today's spend so far: ${client.todays_spend():.4f}")
    print(f"Remaining budget:     ${client.remaining_budget():.4f}")
    print()

    total_cost = 0.0
    drafted_n = 0
    failed_n = 0

    for i, row in enumerate(candidates, 1):
        symbol = row["symbol"]
        subject = row["subject"][:40]
        print(f"[{i:>3}/{len(candidates)}] {symbol:<14} {subject:<42} ",
              end="", flush=True)

        try:
            draft = draft_one(client, row)
        except DailyCapExceeded as e:
            print(f"\nSTOPPED: {e}")
            break

        if draft is None or "_error" in draft:
            err = draft.get("_error", "unknown") if draft else "no_response"
            print(f"FAIL ({err})")
            failed_n += 1
            continue

        out_path = DRAFTS_DIR / f"{row['fingerprint']}.yaml"
        with out_path.open("w") as f:
            yaml.safe_dump(draft, f, default_flow_style=False, sort_keys=False)

        cost = draft.get("_meta", {}).get("cost_usd", 0)
        total_cost += cost
        drafted_n += 1
        print(f"OK  (${cost:.4f})")

    print()
    print(f"Drafted:    {drafted_n}")
    print(f"Failed:     {failed_n}")
    print(f"This run:   ${total_cost:.4f}")
    print(f"Today total: ${client.todays_spend():.4f}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=50,
                        help="Number of new drafts to create (default 50)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be drafted without LLM calls")
    args = parser.parse_args()
    sys.exit(main(args))