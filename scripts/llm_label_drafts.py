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
PDFs filed with NSE. You will receive text extracted from a PDF result filing.

CRITICAL CONTEXT — How the text is structured:
The PDF's table has been flattened into reading order. Each row label appears \
on its own line, followed by the row's numeric values on separate lines, one \
per column. Most NSE result PDFs have 5 numeric columns in this order:
  Column 1: Most recent quarter (e.g. Q4 ending March 2026)
  Column 2: Previous quarter (e.g. Q3)
  Column 3: Year-ago quarter (e.g. Q4 of prior year)
  Column 4: Current full year
  Column 5: Previous full year

Example pattern in the text:
  Revenue from operations
  81,010    <-- this is the Q4-26 value (Column 1, what we want)
  81,463    <-- Q3-26
  77,271    <-- Q4-25
  324,931   <-- FY26
  301,228   <-- FY25

OCR ERRORS ARE COMMON:
Some PDFs have OCR'd text with character substitutions:
  - "adittd" or "audtd" might mean "Audited"
  - "lncomr" or "lncome" might mean "Income"
  - "l.552" might mean "1,552"
  - "0th r" might mean "Other"
Be tolerant. If a label looks close to a known label, treat it as that label.

YOUR TASK:
Extract the 10 canonical financial fields below from the MOST RECENT QUARTER \
(the FIRST numeric value after the row label).

Return raw values as they appear in the PDF, in the unit the PDF uses. \
DO NOT convert units. Identify the unit and return it separately.

Return ONLY a JSON object matching this exact schema:
{
  "standalone": {
    "revenue": <number or null>,
    "other_income": <number or null>,
    "total_income": <number or null>,
    "total_expenses": <number or null>,
    "pbt": <number or null>,
    "tax": <number or null>,
    "pat": <number or null>,
    "total_comprehensive_income": <number or null>,
    "eps_basic": <number or null>,
    "eps_diluted": <number or null>
  },
  "period_label": "<e.g. 'Q4-FY26' or 'Q3-FY26'>",
  "period_ending": "<YYYY-MM-DD>",
  "units_in_source_pdf": "<exact unit phrase: 'INR million' | 'INR lakh' | 'INR crore' | 'INR thousand' | 'INR'>",
  "table_found": <true if you found the P&L table, false if the text is cover-letter-only>,
  "notes": "<observations: BFSI? consolidated only? table location? OCR quality?>"
}

Field-to-label mapping (look for ANY of these labels, fuzzy-matched):
  revenue:
    - "Revenue from operations" / "Total revenue from operations"
    - "Net Sales / Income from Operations"
    - "Total Income from Operations"
    - For banks/NBFCs: "Interest Earned" or "Net Interest Income"
  other_income:
    - "Other income" / "Total other income"
  total_income:
    - "Total income" / "Total Income (I+II)" / "Total income (1+2)"
  total_expenses:
    - "Total expenses" / "Total Expenses (IV)"
  pbt:
    - "Profit before tax" / "Profit/(loss) before tax" / "PBT"
  tax:
    - "Total tax expense" / "Tax expense"
  pat:
    - "Profit after tax" / "Net profit" / "Net profit / (loss) for the period"
  total_comprehensive_income:
    - "Total comprehensive income"
  eps_basic:
    - "(a) Basic" or "Basic (in Rs)" under "Earnings per equity share"
    - Look for a small decimal number, usually < 100
  eps_diluted:
    - "(b) Diluted" or "Diluted (in Rs)" — usually identical or very close to eps_basic

RULES:
1. Use STANDALONE numbers, NOT consolidated. PDFs may show both sections. Standalone usually appears first.
2. For each label found, the FIRST number on a subsequent line is the value to extract (it's Column 1 = most recent quarter).
3. If a number is clearly out of magnitude (e.g. you see "EPS = 81,010") you've grabbed the wrong line — re-scan.
4. If the text is cover-letter-only (no numbers found near financial labels), set table_found=false and ALL standalone fields to null.
5. Validate before returning: PAT magnitude should be smaller than revenue magnitude. If you've extracted PAT > Revenue, you've likely misaligned columns — re-scan.
6. NEVER guess. If unsure, return null for that specific field.
"""


def fetch_candidates(
    db: sqlite3.Connection,
    target_count: int,
    drafted_already: set,
) -> list:
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
    candidates = [c for c in candidates if c["fingerprint"] not in drafted_already]
    return candidates[:target_count]


def existing_drafts() -> set:
    """Return fingerprints that already have drafts on disk."""
    if not DRAFTS_DIR.exists():
        return set()
    return {p.stem for p in DRAFTS_DIR.glob("*.yaml")}


def draft_one(client: LLMClient, row: dict):
    """Send one PDF's text to the LLM and parse the response."""
    text = row["pdf_text"]
    if len(text) > 40000:
        text = text[:40000] + "\n\n[...text truncated for token budget...]"

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


def main(args):
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
        print("\nDRY RUN -- would draft these:")
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
