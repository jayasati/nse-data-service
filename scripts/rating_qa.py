"""
Rating-extraction QA (FEATURE_CHECKLIST Phase 5, Week 16, task 16.7).

Prints each parsed credit-rating action next to a snippet of its source PDF text,
plus a coverage summary (% where agency/action/new_rating parsed) — so you can
eyeball real-world extraction accuracy in one command instead of opening PDFs.

    # review the most recent 15 parsed actions
    PYTHONPATH=src python scripts/rating_qa.py

    # run the extractor first (backfill), then review
    PYTHONPATH=src python scripts/rating_qa.py --extract --limit 25
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nse_data.parsers.rating_extractor import run_rating_extraction   # noqa: E402
from nse_data.storage.db import open_db                               # noqa: E402

_KW = re.compile(r"(downgrad|upgrad|reaffirm|assigned|revised|rating action)", re.I)


def _snippet(text: str | None, width: int = 130) -> str:
    if not text:
        return "(no text)"
    t = " ".join(text.split())
    m = _KW.search(t)
    start = max(0, m.start() - 25) if m else 0
    return t[start:start + width]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Rating extraction QA review")
    p.add_argument("--db", default="data/nse.db")
    p.add_argument("--limit", type=int, default=15)
    p.add_argument("--extract", action="store_true", help="run the extractor first")
    args = p.parse_args(argv)

    conn = open_db(args.db)
    if args.extract:
        print("extract:", run_rating_extraction(conn), "\n")

    rows = conn.execute(
        "SELECT r.symbol, r.agency, r.action, r.old_rating, r.new_rating, "
        "       r.is_junk_downgrade, r.broadcast_dt, a.pdf_text "
        "FROM raw_rating_actions r "
        "LEFT JOIN raw_announcements a ON a.fingerprint = r.announcement_fingerprint "
        "ORDER BY r.id DESC LIMIT ?",
        (args.limit,),
    ).fetchall()

    if not rows:
        print("no rows in raw_rating_actions — run with --extract (needs pdf_text).")
        return 0

    for sym, agency, action, old, new, junk, bdt, text in rows:
        junk_tag = "  ⚠JUNK" if junk else ""
        print(f"{sym:12s} {(agency or '?'):13s} {(action or '?'):14s} "
              f"{(old or '?'):>5s} → {(new or '?'):<6s}{junk_tag}")
        print(f"    {bdt or ''}")
        print(f"    “{_snippet(text)}”\n")

    # ---- coverage summary -------------------------------------------------
    tot = conn.execute("SELECT COUNT(*) FROM raw_rating_actions").fetchone()[0]

    def pct(col):
        n = conn.execute(
            f"SELECT COUNT(*) FROM raw_rating_actions WHERE {col} IS NOT NULL"
        ).fetchone()[0]
        return f"{n}/{tot} ({100 * n / tot:.0f}%)" if tot else "0"

    by_action = conn.execute(
        "SELECT action, COUNT(*) FROM raw_rating_actions GROUP BY action ORDER BY 2 DESC"
    ).fetchall()

    print("=" * 60)
    print(f"total actions:   {tot}")
    print(f"agency parsed:   {pct('agency')}")
    print(f"new_rating parsed: {pct('new_rating')}")
    print(f"old_rating parsed: {pct('old_rating')}")
    print("by action:       " + ", ".join(f"{a}={n}" for a, n in by_action))
    print("\nEyeball ~10 of the rows above: does action match the snippet, and is "
          "new_rating right where present? Low new_rating % = lean on action/junk, "
          "not the grades.")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
