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
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# index values to try for a symbol when its segment is unknown (equities first,
# SME is the long tail).
_SEGMENTS = ("equities", "sme")


def _qdate(s: str | None) -> dt.datetime:
    for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
        try:
            return dt.datetime.strptime(s or "", fmt)
        except (ValueError, TypeError):
            continue
    return dt.datetime.min


def _history(sm, sym: str, segment: str | None, attempts: int = 4):
    """The symbol's quarterly filing records, newest first. Tries the known
    segment then the other (mis-tagged symbols still resolve). Retries with
    backoff on FETCH FAILURE (NSE throttle / open circuit) so a transient block
    is never mistaken for 'no history' — that exact confusion zeroed 706/755
    symbols on the first run. Returns [] for a genuine empty response, None if
    every attempt errored (caller treats None as retryable, [] as no-data)."""
    from nse_data.collectors.shareholding import SHP_REFERER

    order = ([segment] + [s for s in _SEGMENTS if s != segment]) if segment else list(_SEGMENTS)
    errored = False
    for idx in order:
        for a in range(attempts):
            try:
                data = sm.get_json(
                    "shp_history", "/api/corporate-share-holdings-master",
                    referer=SHP_REFERER, params={"index": idx, "symbol": sym})
            except Exception:  # noqa: BLE001 — throttle / open circuit → back off, retry
                errored = True
                time.sleep(1.5 * (a + 1))
                continue
            if isinstance(data, dict):
                data = data.get("data") or data.get("rows") or []
            if data:
                data.sort(key=lambda r: _qdate(r.get("date", "")), reverse=True)
                return data
            break                       # genuine empty for this segment → try the other
    return None if errored else []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--symbols", help="comma list (default: all in the master)")
    ap.add_argument("--quarters", type=int, default=12,
                    help="most-recent quarters per symbol (0 = full history)")
    ap.add_argument("--sleep", type=float, default=0.0, help="(legacy) per-XBRL pause; "
                    "0 = none, XBRLs now download in parallel on the CDN")
    ap.add_argument("--workers", type=int, default=4,
                    help="parallel XBRL downloads per symbol (CDN, not throttled)")
    ap.add_argument("--pace", type=float, default=0.6,
                    help="base pause per symbol on the master-list call")
    ap.add_argument("--max-cooldown", type=float, default=30.0,
                    help="cap for the escalating cooldown after master-call errors")
    ap.add_argument("--skip-covered", type=int, default=0,
                    help="skip symbols that already have >= N quarters (no master "
                         "call) — use on a re-run to work only the gaps")
    ap.add_argument("--refresh", action="store_true",
                    help="re-fetch + overwrite rows already in the table (heals "
                         "old scale/taxonomy bugs); default skips existing")
    ap.add_argument("--all-symbols", action="store_true",
                    help="ignore the tracked-universe gate and backfill every SHP "
                         "master symbol (default: tracked universe only)")
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

    # THE universe gate — every downstream consumer passes through it. Ownership
    # is downstream, so we only spend NSE budget on tracked names (drops SME /
    # illiquid / ETF). filter_tracked fails open if the table is missing/empty.
    if not args.all_symbols:
        from nse_data import universe
        g = universe.gate(args.db)          # bind the singleton to our db first
        before = len(symbols)
        symbols = g.filter_tracked(symbols)
        gated = "FAIL-OPEN (no universe table)" if g.loaded_empty \
            else f"{len(symbols)}/{before} tracked"
        print(f"universe gate: {gated}", flush=True)

    done = {(s, d) for s, d in conn.execute(
        "SELECT symbol, qe_date FROM raw_shareholding_quarterly")}
    have = Counter(s for (s, _d) in done)           # quarters already stored per symbol

    sm = SessionManager()
    stats = {"ins": 0, "skip": 0, "fail": 0, "empty": 0, "covered": 0}

    def _fetch_parse(url):
        """Download one XBRL (throttle-free CDN) and parse it → fields | None."""
        try:
            xml = sm.get_bytes("shp_xbrl_hist", url, referer=SHP_REFERER)
            return parse_shp(xml.decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            return None

    def process(sym) -> bool:
        """Fetch + store one symbol's history. Returns False only on a master-call
        ERROR (None) so the caller can retry; True for stored / genuinely empty.
        XBRLs download in parallel (CDN), so a symbol's quarters land in ~1 fetch
        of wall-time instead of N serial ones."""
        recs = _history(sm, sym, seg.get(sym))
        if recs is None:
            return False                            # transient block → retryable
        if not recs:
            stats["empty"] += 1
            return True
        todo = []
        for rec in (recs[:args.quarters] if args.quarters > 0 else recs):
            qe, url = rec.get("date"), rec.get("xbrl")
            if not qe or not url:
                continue
            if only_missing and (sym, qe) in done:
                stats["skip"] += 1
                continue
            todo.append((qe, url))
        if todo:
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                fields = list(ex.map(lambda t: _fetch_parse(t[1]), todo))
            for (qe, url), f in zip(todo, fields):
                if not f:
                    stats["fail"] += 1
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO raw_shareholding_quarterly (symbol, qe_date, "
                    "promoter_pct, public_pct, fii_pct, dii_pct, mf_pct, xbrl_url, fetched_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (sym, qe, f.get("promoter_pct"), f.get("public_pct"), f.get("fii_pct"),
                     f.get("dii_pct"), f.get("mf_pct"), url, int(time.time())))
                done.add((sym, qe))
                stats["ins"] += 1
        return True

    errored: list[str] = []
    consec_err = 0
    try:
        for i, sym in enumerate(symbols, 1):
            # skip symbols already well-covered — no master call (less throttle exposure)
            if args.skip_covered and have.get(sym, 0) >= args.skip_covered:
                stats["covered"] += 1
                continue
            ok = process(sym)
            if not ok:
                errored.append(sym)
                consec_err += 1
                # escalating cooldown lets a throttle WAVE dissipate instead of
                # hammering through it (the self-reinforcing-error fix)
                time.sleep(min(args.pace * (2 ** consec_err), args.max_cooldown))
            else:
                consec_err = 0
                time.sleep(args.pace)
            if i % 25 == 0:
                conn.commit()
                print(f"  [{i}/{len(symbols)}] {sym} ins={stats['ins']} skip={stats['skip']} "
                      f"fail={stats['fail']} empty={stats['empty']} covered={stats['covered']} "
                      f"err={len(errored)}", flush=True)
        conn.commit()
        # one retry pass for symbols whose master call errored (transient throttle)
        if errored:
            print(f"retrying {len(errored)} errored symbols...", flush=True)
            n_retry = len(errored)
            still = []
            for s in errored:
                if process(s):
                    time.sleep(args.pace)
                else:
                    still.append(s)
                    time.sleep(min(args.pace * 4, args.max_cooldown))
            conn.commit()
            errored = still
            print(f"  recovered={n_retry - len(still)} unrecovered={len(still)}"
                  + (f" ({', '.join(still[:12])})" if still else ""), flush=True)
    finally:
        sm.close()

    tot = conn.execute("SELECT COUNT(*) FROM raw_shareholding_quarterly").fetchone()[0]
    qtrs = conn.execute(
        "SELECT COUNT(DISTINCT qe_date) FROM raw_shareholding_quarterly").fetchone()[0]
    print(f"DONE: inserted={stats['ins']} skipped={stats['skip']} parse_fail={stats['fail']} "
          f"no_history={stats['empty']} covered={stats['covered']} unrecovered_err={len(errored)}  "
          f"table_total={tot} distinct_quarters={qtrs}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
