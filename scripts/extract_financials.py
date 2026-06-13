"""One-off financial-results extraction with live per-result progress.

Reads result-subject PDFs that have text (pdf_status='text_extracted') and
aren't in extracted_financials yet, runs the LLM extractor on each, and prints
one line per result as it lands — newest filings first. Honors the LLMClient
$25/day spend cap.

    PYTHONPATH=src python scripts/extract_financials.py --limit 50
    # or with the venv:
    .venv/bin/python -u scripts/extract_financials.py --limit 50

`-u` (unbuffered) makes the progress stream live over SSH.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Load .env relative to the repo root, so it works regardless of CWD, and
# override any blank AZURE_* inherited from the service environment.
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import os  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50, help="max results to extract")
    ap.add_argument("--db", default="data/nse.db")
    args = ap.parse_args()

    missing = [k for k in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY",
                           "AZURE_OPENAI_DEPLOYMENT_NAME") if not os.environ.get(k)]
    if missing:
        print(f"ABORT: missing Azure creds {missing} — check .env", file=sys.stderr)
        return 2

    from nse_data.storage.db import open_db
    from nse_data.parsers.state import State
    from nse_data.fundamentals.from_results import extract_and_store, is_result_subject

    conn = open_db(args.db)
    candidates = conn.execute(
        "SELECT fingerprint, symbol, subject, broadcast_dt, pdf_path "
        "FROM raw_announcements "
        "WHERE pdf_status = ? AND pdf_path IS NOT NULL "
        "AND fingerprint NOT IN ("
        "  SELECT source_fingerprint FROM extracted_financials "
        "  WHERE source_fingerprint IS NOT NULL) "
        "ORDER BY broadcast_dt DESC",
        (State.TEXT_EXTRACTED,),
    ).fetchall()
    results = [c for c in candidates if is_result_subject(c[2])]
    todo = results[: args.limit]
    print(f"candidates with text: {len(candidates)} | result-subject: {len(results)} "
          f"| extracting: {len(todo)}\n", flush=True)

    done = stored = 0
    cost = 0.0
    started = time.time()
    for i, (fp, sym, subj, bdt, path) in enumerate(todo, 1):
        t0 = time.time()
        try:
            r = extract_and_store(
                conn, fingerprint=fp, symbol=sym, subject=subj,
                broadcast_dt=bdt, pdf_path=path, use_llm=True,
            )
        except Exception as e:  # noqa: BLE001 — one bad PDF shouldn't stop the batch
            print(f"[{i:>3}/{len(todo)}] {sym:<14} ERROR {e!r}", flush=True)
            continue
        done += 1
        stored += r["stored"]
        cost += r["cost_usd"] or 0.0
        mark = "OK " if r["stored"] else "-- "
        print(f"[{i:>3}/{len(todo)}] {mark}{sym:<14} stored={r['stored']} "
              f"strategy={r['strategy']:<8} conf={r['confidence']:.2f} "
              f"period={r['period_ending']} cost=${r['cost_usd'] or 0:.4f} "
              f"(run ${cost:.2f}, {time.time()-t0:.1f}s)", flush=True)

    conn.close()
    print(f"\nDONE: {done} processed, {stored} rows stored, ${cost:.4f} spent "
          f"in {time.time()-started:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
