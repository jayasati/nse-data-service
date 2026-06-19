"""Late-July reminder: has consensus-backed surprise activated? In June 2026 we seeded
Yahoo analyst consensus for the upcoming June/Sep quarters, but the Surprise factor only
uses it once those quarters REPORT (~late July). This script counts how many stocks now
have a genuine consensus-backed surprise (latest reported result has a matching consensus
row whose as_of precedes the print) and Telegrams the status.

    PYTHONPATH=src .venv/bin/python scripts/consensus_surprise_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> int:
    from nse_data.storage.db import open_db
    from nse_data.research.surprise_engine import _bdt_epoch
    from nse_data.bot.dispatcher import load_telegram_config, send_telegram

    conn = open_db("data/nse.db")
    backed = total_cons = 0
    for (sym,) in conn.execute("SELECT DISTINCT symbol FROM consensus_estimates"):
        total_cons += 1
        r = conn.execute("SELECT period_ending, broadcast_dt FROM extracted_financials "
                         "WHERE symbol=? ORDER BY period_ending DESC LIMIT 1", (sym,)).fetchone()
        if not r:
            continue
        pe, bdt = r
        bep = _bdt_epoch(bdt)
        ce = conn.execute("SELECT as_of FROM consensus_estimates WHERE symbol=? AND period_ending=?",
                          (sym, pe)).fetchone()
        if ce and bep:
            aep = _bdt_epoch(ce[0]) if isinstance(ce[0], str) else (int(ce[0]) if ce[0] else None)
            if aep and aep < bep:
                backed += 1
    conn.close()

    if backed > 0:
        msg = (f"🔔 Consensus-surprise CHECK: {backed} stocks now have REAL consensus-backed "
               f"surprise (was 0 in June — the June quarter has reported). The Surprise half is "
               f"live. Re-run the OOS / lean ranking and compare consensus vs proxy.\n"
               f"  PYTHONPATH=src .venv/bin/python scripts/lean_rank.py")
    else:
        msg = ("🔔 Consensus-surprise check: still 0 consensus-backed surprises — the June quarter "
               f"may not have reported yet (have consensus for {total_cons} names, staged). "
               "Check again in a few days.")
    token, chat_id = load_telegram_config()
    ok = send_telegram(token, chat_id, msg)
    print(msg)
    print("telegram sent:", ok, "| consensus-backed:", backed, "/", total_cons)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
