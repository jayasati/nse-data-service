"""Attribute causes to detected intraday moves (external news + LLM).

Runs the move_causes pipeline over a symbol's most significant moves and stores
the result. Cost-gated: only the top moves (by absolute % and consistency), only
the tracked universe by default.

    PYTHONPATH=src .venv/bin/python -u scripts/find_move_causes.py --symbols ADANIGREEN --limit 5
    PYTHONPATH=src .venv/bin/python -u scripts/find_move_causes.py --min-move 4 --limit 3   # all tracked
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--symbols", help="comma list (default: tracked universe)")
    ap.add_argument("--limit", type=int, default=5, help="top moves per symbol to attribute")
    ap.add_argument("--min-move", type=float, default=3.0, help="only moves >= this abs %%")
    ap.add_argument("--refresh", action="store_true", help="re-attribute even if already stored")
    ap.add_argument("--budget", type=float, default=2.0,
                    help="USD cap on LLM spend this run (stops cleanly when reached)")
    args = ap.parse_args()

    import sqlite3
    from nse_data.parsers.extractors.llm_client import LLMClient
    from nse_data.research import move_causes as mc

    conn = sqlite3.connect(args.db)
    mc.ensure_table(conn)

    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        syms = [s for (s,) in conn.execute(
            "SELECT symbol FROM tradeable_universe WHERE grade IN "
            "('A core','B tradeable','C volatile')")]

    try:
        llm = LLMClient()
    except Exception as e:  # noqa: BLE001 — missing Azure creds
        print(f"LLM unavailable ({e}). Set AZURE_OPENAI_* in .env to run attribution.")
        return 2
    client = mc.make_client()

    done = {(r[0], r[1]) for r in conn.execute("SELECT symbol, date FROM move_causes")}
    total = spent = 0.0
    capped = False
    for sym in syms:
        if capped:
            break
        moves = conn.execute(
            "SELECT date, direction, move_pct FROM intraday_move_events "
            "WHERE symbol=? AND ABS(move_pct) >= ? "
            "ORDER BY ABS(move_pct) DESC, consistency DESC LIMIT ?",
            (sym, args.min_move, args.limit)).fetchall()
        for date, direction, move_pct in moves:
            if not args.refresh and (sym, date) in done:
                continue
            if spent >= args.budget:
                print(f"\n[budget] ${spent:.3f} ≥ cap ${args.budget:.2f} — stopping.", flush=True)
                capped = True
                break
            cause = mc.find_cause(conn, llm, client, symbol=sym, date=date,
                                  direction=direction, move_pct=move_pct)
            if cause:
                spent += cause.get("cost_usd", 0.0) or 0.0
                total += 1
                print(f"  {sym} {date} {direction} {move_pct:+.1f}%  -> "
                      f"[{cause['category']}] {cause['cause_summary']} "
                      f"(conf {cause['confidence']}, {cause['n_articles']} articles)", flush=True)
            else:
                print(f"  {sym} {date} {direction} {move_pct:+.1f}%  -> (no cause found)", flush=True)

    print(f"\nDONE: attributed {int(total)} moves, LLM spend ${spent:.3f}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
