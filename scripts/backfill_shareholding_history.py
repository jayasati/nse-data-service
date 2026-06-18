"""Backfill MULTI-QUARTER shareholding history into raw_shareholding_quarterly.

The quarterly table is normally fed one quarter at a time by
`fill_shareholding_xbrl.py` (it parses the latest-quarter XBRL the master row
points at). NSE's per-symbol master endpoint, however, returns the FULL filing
history — one record per quarter back to ~2005, each with its own XBRL link:

    /api/corporate-share-holdings-master?index=<equities|sme>&symbol=<SYM>

So we can backfill several years at once instead of waiting a quarter at a time.
Per symbol: pull the history list, take the most recent --quarters records, run
each quarter's XBRL through the shared `parse_shp`, upsert by (symbol, qe_date).

Default depth is 12 quarters (≈3y) — matches our price history, beyond which an
ownership signal has no returns to validate against. Use --quarters 0 for all.

XBRL downloads are on the nsearchives CDN (no Akamai); the master API needs the
NSE cookie warm-up, both handled by SessionManager. Idempotent (--only-missing).

    PYTHONPATH=src .venv/bin/python -u scripts/backfill_shareholding_history.py \
        --quarters 12 --sleep 0.4
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# index values to try for a symbol when its segment is unknown (equities first,
# SME is the long tail).
_SEGMENTS = ("equities", "sme")


def _qdate(s: str) -> dt.datetime:
    for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    return dt.datetime.min


def _history(sm, sym: str, segment: str | None):
    """The symbol's quarterly filing records, newest first. Tries the known
    segment, then the other, so a mis-tagged symbol still resolves."""
    from nse_data.collectors.shareholding import SHP_REFERER

    order = [segment] + [s for s in _SEGMENTS if s != segment] if segment else list(_SEGMENTS)
    for idx in order:
        try:
            data = sm.get_json(
                "shp_history", "/api/corporate-share-holdings-master",
                referer=SHP_REFERER, params={"index": idx, "symbol": sym})
        except Exception:  # noqa: BLE001 — NSE hiccup; try next segment / skip
            data = None
        if isinstance(data, dict):
            data = data.get("data") or data.get("rows") or []
        if data:
            data.sort(key=lambda r: _qdate(r.get("date", "")), reverse=True)
            return data
    return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--symbols", help="comma list (default: all in the master)")
    ap.add_argument("--quarters", type=int, default=12,
                    help="most-recent quarters per symbol (0 = full history)")
    ap.add_argument("--sleep", type=float, default=0.4, help="pause between XBRL fetches")
    ap.add_argument("--refresh", action="store_true",
                    help="re-fetch + overwrite rows already in the table (heals "
                         "old scale/taxonomy bugs); default skips existing")
    args = ap.parse_args()
    only_missing = not args.refresh

    from nse_data.collectors.shareholding import SHP_REFERER
    from nse_data.parsers.shp_xbrl import parse_shp
    from nse_data.session.manager import SessionManager
    from nse_data.storage.db import apply_migrations, open_db

    conn = open_db(args.db)
    conn.execute("PRAGMA busy_timeout=60000")
    apply_migrations(conn)

    # symbol -> segment from the master (segment column may be absent in old DBs).
    try:
        master = conn.execute(
            "SELECT DISTINCT symbol, segment FROM raw_shareholding_pattern").fetchall()
    except Exception:  # noqa: BLE001
        master = [(s, None) for (s,) in conn.execute(
            "SELECT DISTINCT symbol FROM raw_shareholding_pattern")]
    seg = {s: g for s, g in master}
    symbols = sorted(seg)
    if args.symbols:
        want = {s.strip().upper() for s in args.symbols.split(",")}
        symbols = [s for s in symbols if s in want]

    done = {(s, d) for s, d in conn.execute(
        "SELECT symbol, qe_date FROM raw_shareholding_quarterly")}

    sm = SessionManager()
    n_ins = n_skip = n_fail = n_empty = 0
    try:
        for i, sym in enumerate(symbols, 1):
            recs = _history(sm, sym, seg.get(sym))
            if not recs:
                n_empty += 1
            if args.quarters > 0:
                recs = recs[:args.quarters]
            for rec in recs:
                qe, url = rec.get("date"), rec.get("xbrl")
                if not qe or not url:
                    continue
                if only_missing and (sym, qe) in done:
                    n_skip += 1
                    continue
                try:
                    xml = sm.get_bytes("shp_xbrl_hist", url, referer=SHP_REFERER)
                    f = parse_shp(xml.decode("utf-8", "replace"))
                except Exception:  # noqa: BLE001
                    f = None
                if not f:
                    n_fail += 1
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO raw_shareholding_quarterly (symbol, qe_date, "
                    "promoter_pct, public_pct, fii_pct, dii_pct, mf_pct, xbrl_url, fetched_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (sym, qe, f.get("promoter_pct"), f.get("public_pct"), f.get("fii_pct"),
                     f.get("dii_pct"), f.get("mf_pct"), url, int(time.time())))
                done.add((sym, qe))
                n_ins += 1
                time.sleep(args.sleep)
            if i % 25 == 0:
                conn.commit()
                print(f"  [{i}/{len(symbols)}] {sym} ins={n_ins} skip={n_skip} "
                      f"fail={n_fail} empty={n_empty}", flush=True)
        conn.commit()
    finally:
        sm.close()

    tot = conn.execute("SELECT COUNT(*) FROM raw_shareholding_quarterly").fetchone()[0]
    qtrs = conn.execute(
        "SELECT COUNT(DISTINCT qe_date) FROM raw_shareholding_quarterly").fetchone()[0]
    print(f"DONE: inserted={n_ins} skipped={n_skip} parse_fail={n_fail} "
          f"no_history={n_empty}  table_total={tot} distinct_quarters={qtrs}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
