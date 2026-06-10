"""Consensus-estimate driver (P6 / S8) — manual entry, CSV import, live fetch.

The manual path is the highest-ranked source (``consensus.SOURCE_RANK``):
broker-preview numbers entered here outrank Moneycontrol/Yahoo, and it is the
ONLY source for BFSI NII/NIM estimates (the lines that actually decide a bank
print).

    # one estimate by hand (broker preview):
    python scripts/load_consensus.py set SBIN 2026-06-30 --rev 117000 --pat 18500 \\
        --nii 44000 --nim 3.0

    # a CSV (columns: symbol, period_ending, rev_est_cr, pat_est_cr, eps_est,
    # nii_est_cr, nim_est_pct — aliases like 'eps', 'nim' also accepted):
    python scripts/load_consensus.py csv estimates_q1fy27.csv

    # live sources, for upcoming reporters (default) or named symbols:
    python scripts/load_consensus.py fetch
    python scripts/load_consensus.py fetch --symbols INFY,TCS --sources yahoo

    # what's stored (all sources side by side — the cross-validation view):
    python scripts/load_consensus.py show INFY 2026-06-30
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nse_data.events import consensus  # noqa: E402
from nse_data.events.consensus_job import run_consensus_pass, upcoming_symbols  # noqa: E402
from nse_data.events.estimate_scraper import ingest_records  # noqa: E402
from nse_data.storage.db import apply_migrations, open_db  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/nse.db")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_set = sub.add_parser("set", help="enter one estimate by hand (source=manual)")
    p_set.add_argument("symbol")
    p_set.add_argument("period_ending", help="quarter end, YYYY-MM-DD")
    p_set.add_argument("--rev", type=float, help="revenue estimate, ₹ cr")
    p_set.add_argument("--pat", type=float, help="PAT estimate, ₹ cr")
    p_set.add_argument("--eps", type=float, help="EPS estimate, ₹")
    p_set.add_argument("--nii", type=float, help="NII estimate, ₹ cr (BFSI)")
    p_set.add_argument("--nim", type=float, help="NIM estimate, %% (BFSI)")

    p_csv = sub.add_parser("csv", help="import a CSV of estimates (source=manual)")
    p_csv.add_argument("path")

    p_fetch = sub.add_parser("fetch", help="pull live estimates (news + moneycontrol + yahoo)")
    p_fetch.add_argument("--symbols", help="comma-separated; default = upcoming reporters")
    p_fetch.add_argument("--sources", default="news,moneycontrol,yahoo")

    p_show = sub.add_parser("show", help="stored estimates, all sources side by side")
    p_show.add_argument("symbol")
    p_show.add_argument("period_ending", nargs="?")

    args = ap.parse_args()
    conn = open_db(args.db)
    apply_migrations(conn, "migrations")   # idempotent; the table/columns must exist
    try:
        if args.cmd == "set":
            consensus.upsert_estimate(
                conn, symbol=args.symbol.upper(), period_ending=args.period_ending,
                rev_est_cr=args.rev, pat_est_cr=args.pat, eps_est=args.eps,
                nii_est_cr=args.nii, nim_est_pct=args.nim, source="manual",
            )
            print(f"stored manual estimate: {args.symbol.upper()} {args.period_ending}")
        elif args.cmd == "csv":
            with open(args.path, newline="") as f:
                n = ingest_records(conn, list(csv.DictReader(f)), source="manual")
            print(f"ingested {n} manual estimates from {args.path}")
        elif args.cmd == "fetch":
            symbols = ([s.strip().upper() for s in args.symbols.split(",") if s.strip()]
                       if args.symbols else upcoming_symbols(conn))
            report = run_consensus_pass(
                conn, symbols, sources=tuple(args.sources.split(",")),
            )
            print(report)
        elif args.cmd == "show":
            if args.period_ending:
                rows = consensus.estimates_by_source(conn, args.symbol.upper(), args.period_ending)
            else:
                rows = [consensus._row_to_dict(r) for r in conn.execute(  # noqa: SLF001
                    f"SELECT {consensus._EST_COLS} FROM consensus_estimates "  # noqa: SLF001
                    "WHERE symbol=? ORDER BY period_ending DESC, source",
                    (args.symbol.upper(),)).fetchall()]
            if not rows:
                print("no estimates stored")
            for r in rows:
                print(f"{r['period_ending']}  [{r['source']:<12}] "
                      f"rev {r['rev_est_cr']!s:>10} cr | pat {r['pat_est_cr']!s:>9} cr | "
                      f"eps {r['eps_est']!s:>7} | nii {r['nii_est_cr']!s:>9} | nim {r['nim_est_pct']!s:>5}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
