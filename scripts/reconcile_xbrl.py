"""After-close XBRL reconciliation with live progress.

Intraday, results are extracted from the PDF by the LLM (fast, to catch the
move). Hours later NSE broadcasts the structured XBRL — this re-extracts those
results deterministically and overwrites the stored numbers with the
authoritative values, printing what changed.

    .venv/bin/python -u scripts/reconcile_xbrl.py --limit 50
    .venv/bin/python -u scripts/reconcile_xbrl.py --limit 50 --alert   # send correction notes
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--alert", action="store_true", help="send Telegram correction notes")
    args = ap.parse_args()

    from nse_data.storage.db import open_db
    from nse_data.session.manager import SessionManager
    from nse_data.fundamentals.from_results import reconcile_xbrl_pass

    sender = token = chat_id = None
    if args.alert:
        from nse_data.bot.dispatcher import load_telegram_config, send_telegram
        token, chat_id = load_telegram_config()
        sender = send_telegram

    conn = open_db(args.db)
    rep = reconcile_xbrl_pass(conn, session=SessionManager(), limit=args.limit,
                              sender=sender, token=token, chat_id=chat_id)
    print(f"checked {rep['checked']} | corrected {rep['corrected']}\n")
    for r in rep["rows"]:
        flag = "MATERIAL" if r["material"] else "minor"
        print(f"[{flag}] {r['symbol']} {r['period']}")
        for scope, diffs in r["diffs"]:
            for f, old, new, mat in diffs:
                mark = "*" if mat else " "
                print(f"   {mark} {scope}.{f}: {old} -> {new}")
    print(f"\nDONE: {rep['corrected']} results corrected by XBRL.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
