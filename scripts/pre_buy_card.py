"""Pre-buy conviction card for a symbol (PROFITABILITY_PLAN R16).

One screen of "what to know before you buy" — valuation, quality, balance-sheet strength,
promoter pledge, catalyst, delivery, surveillance, the ATR risk plan, and the paper-book
track record. Every line degrades to n/a on missing data.

    PYTHONPATH=src .venv/bin/python -u scripts/pre_buy_card.py RELIANCE
    PYTHONPATH=src .venv/bin/python -u scripts/pre_buy_card.py TCS INFY HDFCBANK
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> int:
    import argparse

    from nse_data.research.pre_buy_card import build_card, format_card
    from nse_data.storage.db import open_db

    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="+", help="one or more NSE symbols")
    ap.add_argument("--db", default="data/nse.db")
    args = ap.parse_args()

    conn = open_db(args.db)
    try:
        for sym in args.symbols:
            print(format_card(build_card(conn, sym.upper())))
            print()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
