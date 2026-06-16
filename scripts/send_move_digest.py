"""The "why-it-moved" digest — the inform-don't-trade product.

For a given day, the aggressive moves on tracked A/B names with their attributed
cause ("ICICIBANK +6% — bagged ₹X order" / "+5% — brokerage upgrade"). This is
NOT a trade signal: no edge is claimed, so it is NOT gated by G12. It informs;
the human decides. Reuses move detection + cause engine + universe gate.

Threshold: ≥5% is the readable sweet spot (~30 moves/day on A/B; below that it's
noise). Attribution: the rich LLM "why" only pre-exists for big moves, so we run
find_cause on the SHOWN moves that lack one — bounded to the surfaced set and a
USD --budget cap (well under the daily LLM cap). Gap A (BSE) + Gap B (ratings)
widen what the engine can name.

The same moves are scored in the background by the P4 paper log; this surfaces
them to a human now, while that evidence clock runs.

    # print only, no LLM spend (testing):
    PYTHONPATH=src .venv/bin/python -u scripts/send_move_digest.py --dry-run --no-attribute
    # send for the latest move-events day (attributes shown moves, budget-capped):
    PYTHONPATH=src .venv/bin/python -u scripts/send_move_digest.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)

TRACKED = ("A core", "B tradeable")
_HEADING = {"A core": "CORE", "B tradeable": "TRADEABLE", "C volatile": "VOLATILE"}

# human labels for the internal catalyst bucket (fallback when no LLM summary)
_CATALYST_LABEL = {
    "ann:result": "earnings/result", "ann:other": "announcement (order/deal)",
    "result": "earnings/result", "rating_action": "rating change",
    "brokerage": "brokerage call", "corporate_action": "corporate action",
    "block_deal": "block/bulk deal", "board_meeting": "board meeting",
    "52w_break": "52-week break", "oi_spurt": "OI spurt",
    "delivery_spike": "delivery spike", "fii_dii": "FII/DII flow",
    "none": "no internal catalyst found",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--date", help="move date (default: latest in intraday_move_events)")
    ap.add_argument("--min-move", type=float, default=5.0,
                    help="abs %% floor for inclusion (≥5%% is the readable sweet spot)")
    ap.add_argument("--grades", default=",".join(TRACKED))
    ap.add_argument("--top", type=int, default=20, help="cap movers shown per direction")
    ap.add_argument("--window-days", type=int, default=4)
    ap.add_argument("--attribute", dest="attribute", action="store_true", default=True,
                    help="run LLM find_cause on shown moves lacking a 'why' (default on)")
    ap.add_argument("--no-attribute", dest="attribute", action="store_false")
    ap.add_argument("--budget", type=float, default=3.0, help="USD cap on LLM spend this run")
    ap.add_argument("--dry-run", action="store_true", help="print instead of sending")
    args = ap.parse_args()
    grades = tuple(g.strip() for g in args.grades.split(",") if g.strip())

    from nse_data.storage.db import open_db
    from nse_data.research.cause_engine import primary_catalyst
    from nse_data.research.move_causes import ensure_table as ensure_causes

    conn = open_db(args.db)
    ensure_causes(conn)   # created lazily by the cause pipeline; may be absent here
    date = args.date or conn.execute("SELECT MAX(date) FROM intraday_move_events").fetchone()[0]
    if not date:
        print("no move events found"); return 0

    placeholders = ",".join("?" * len(grades))
    rows = conn.execute(
        f"""SELECT e.symbol, e.direction, e.move_pct, t.grade, c.cause_summary
            FROM intraday_move_events e
            JOIN tradeable_universe t ON t.symbol = e.symbol
            LEFT JOIN move_causes c ON c.symbol = e.symbol AND c.date = e.date
            WHERE e.date = ? AND ABS(e.move_pct) >= ? AND t.grade IN ({placeholders})
            ORDER BY ABS(e.move_pct) DESC""",
        (date, args.min_move, *grades),
    ).fetchall()

    # only the SHOWN set (top N per direction) is eligible for paid attribution
    up_rows = [r for r in rows if r[1] == "up"][:args.top]
    down_rows = [r for r in rows if r[1] == "down"][:args.top]
    shown = up_rows + down_rows

    if args.attribute:
        _attribute_shown(conn, shown, date, args)

    def render(rec):
        symbol, _direction, move_pct, grade, cause_summary = rec
        # re-read cause_summary in case attribution just filled it
        if not cause_summary:
            row = conn.execute(
                "SELECT cause_summary FROM move_causes WHERE symbol=? AND date=?",
                (symbol, date)).fetchone()
            cause_summary = row[0] if row else None
        why = cause_summary or _CATALYST_LABEL.get(
            primary_catalyst(conn, symbol, date, window_days=args.window_days), "—")
        return f"[{_HEADING.get(grade, '')}] {symbol} {move_pct:+.1f}% — {why}"

    ups = [render(r) for r in up_rows]
    downs = [render(r) for r in down_rows]
    conn.close()

    if not ups and not downs:
        print(f"{date}: no qualifying moves (≥{args.min_move}% on {list(grades)})")
        return 0

    parts = [f"📊 Why it moved — {date}", f"(tracked {'/'.join(grades)}, |move| ≥ {args.min_move}%)"]
    if ups:
        parts.append(f"\n🟢 UP ({len(ups)})")
        parts += ups
    if downs:
        parts.append(f"\n🔴 DOWN ({len(downs)})")
        parts += downs
    parts.append("\nℹ️ Information, not a trade call. You decide.")
    text = "\n".join(parts)

    if args.dry_run:
        print(text)
        return 0

    from nse_data.bot.dispatcher import load_telegram_config, send_telegram, _topic_id
    token, chat_id = load_telegram_config()
    thread = _topic_id("digest") or _topic_id("intraday")
    ok = send_telegram(token, chat_id, text, thread)
    print(f"digest {date}: {'sent' if ok else 'NOT sent (telegram unconfigured/failed)'} "
          f"— {len(ups)} up / {len(downs)} down")
    return 0


def _attribute_shown(conn, shown, date, args) -> None:
    """Run find_cause (news + LLM) on shown moves that lack a stored 'why',
    bounded by --budget. Fails soft if the LLM is unconfigured (laptop dev)."""
    need = [r for r in shown if not r[4]]
    if not need:
        return
    try:
        from nse_data.parsers.extractors.llm_client import LLMClient
        from nse_data.research import move_causes as mc
        llm = LLMClient()
    except Exception as e:  # noqa: BLE001 — missing Azure creds / import
        print(f"[attribute] LLM unavailable ({e}); using internal labels only.")
        return
    client = mc.make_client()
    spent = 0.0
    for symbol, direction, move_pct, _grade, _cs in need:
        if spent >= args.budget:
            print(f"[attribute] budget ${args.budget:.2f} reached — stopping.")
            break
        cause = mc.find_cause(conn, llm, client, symbol=symbol, date=date,
                              direction=direction, move_pct=move_pct,
                              window_days=args.window_days)
        if cause:
            spent += cause.get("cost_usd", 0.0) or 0.0
    print(f"[attribute] attributed {len(need)} shown moves, LLM spend ${spent:.3f}")


if __name__ == "__main__":
    raise SystemExit(main())
