"""Live test-gate runner + tracker — automates plans/GRAND_TESTING_STRATEGY.md.

Each gate either has an objective **auto** check (runs live against the DB and
returns pass/fail/warn) or is a **manual** judgement recorded via the CLI. Results
persist in `gate_results` and surface at `/gates`. Run on EC2 (where the live data
is); on the laptop most auto gates read stale → that's expected.

    PYTHONPATH=src python -m nse_data.ops.gates run            # run all auto checks, persist
    PYTHONPATH=src python -m nse_data.ops.gates set G4 pass "15/15 verdicts matched hand-read"
    PYTHONPATH=src python -m nse_data.ops.gates show
"""
from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sqlite3
import sys
from pathlib import Path

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DB_PATH = "data/nse.db"
SHELVED_SIGNALS = ("orb_breakout", "vwap_reclaim", "breakout_52wh", "long_buildup")


def _has(conn, table) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _today() -> str:
    return dt.datetime.now(IST).date().isoformat()


# --- auto checks: (conn) -> (status, detail). status in pass|fail|warn ---------

def _g0_ops(conn):
    used = shutil.disk_usage("/")
    pct = used.used / used.total * 100
    free_gb = used.free / 1e9
    st = "pass" if pct < 80 else ("warn" if pct < 90 else "fail")
    return st, f"disk {pct:.0f}% used, {free_gb:.0f} GB free (<80% = pass)"


def _g1_feeds(conn):
    # heartbeat feeds (all keyed by as_of epoch) should have a row today/last session
    tables = ("raw_oi_spurts", "raw_high_low_52w", "raw_india_vix")
    today = _today()
    stale = []
    for tbl in tables:
        if not _has(conn, tbl):
            stale.append(f"{tbl}:missing"); continue
        try:
            d = conn.execute(
                f"SELECT MAX(date(as_of,'unixepoch','+05:30')) FROM {tbl}").fetchone()[0]
        except sqlite3.OperationalError as e:
            stale.append(f"{tbl}:err({e})"); continue
        if not d or d < today:
            stale.append(f"{tbl}:{d}")
    if not stale:
        return "pass", f"all heartbeat feeds fresh ({today})"
    return ("fail" if len(stale) == len(tables) else "warn"), "stale: " + ", ".join(stale)


def _g2_candle(conn):
    if not _has(conn, "raw_intraday_candles"):
        return "fail", "no raw_intraday_candles"
    row = conn.execute(
        "SELECT MAX(date(ts,'unixepoch','+05:30')) FROM raw_intraday_candles "
        "WHERE symbol='RELIANCE' AND interval='minute'").fetchone()
    last = row[0] if row else None
    # last trading day = today, or Friday if weekend
    wd = dt.datetime.now(IST).weekday()
    expect = _today() if wd < 5 else (dt.datetime.now(IST).date()
                                      - dt.timedelta(days=wd - 4)).isoformat()
    st = "pass" if last and last >= expect else "fail"
    return st, f"RELIANCE 1-min latest={last} (expect ≥{expect})"


def _g3_indicators(conn):
    if not _has(conn, "indicator_live"):
        return "fail", "no indicator_live table"
    n = conn.execute("SELECT COUNT(*) FROM indicator_live").fetchone()[0]
    return ("pass" if n > 0 else "fail"), f"indicator_live rows={n}"


def _g5_universe(conn):
    if not _has(conn, "tradeable_universe"):
        return "fail", "tradeable_universe not built"
    tracked = conn.execute(
        "SELECT COUNT(*) FROM tradeable_universe WHERE grade IN "
        "('A core','B tradeable','C volatile')").fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM tradeable_universe").fetchone()[0]
    return ("pass" if tracked > 0 else "fail"), f"{tracked} tracked / {total} graded"


def _g6_moves(conn):
    if not _has(conn, "intraday_move_events"):
        return "fail", "no intraday_move_events"
    n, mx = conn.execute(
        "SELECT COUNT(*), MAX(date) FROM intraday_move_events").fetchone()
    # sane if not everything is a zero-consistency open-spike
    good = conn.execute(
        "SELECT COUNT(*) FROM intraday_move_events WHERE consistency > 0.3").fetchone()[0]
    st = "pass" if n and good else ("warn" if n else "fail")
    return st, f"{n} events (latest {mx}), {good} with consistency>0.3"


def _g9_signals(conn):
    if not _has(conn, "signals"):
        return "warn", "no signals table (laptop)"
    q = ",".join("?" * len(SHELVED_SIGNALS))
    n = conn.execute(
        f"SELECT COUNT(*) FROM signals WHERE dispatched=1 AND signal_type IN ({q}) "
        f"AND detected_at >= ?", (*SHELVED_SIGNALS, _today())).fetchone()[0]
    # PASS = the shelved net-negative TA signals are NO LONGER dispatched
    return ("pass" if n == 0 else "fail"), f"shelved-TA alerts dispatched today={n} (want 0)"


GATES = [
    ("G0", "Ops foundation", "auto", _g0_ops),
    ("G1", "Data integrity (feeds)", "auto", _g1_feeds),
    ("G2", "Candle pipeline (1-min)", "auto", _g2_candle),
    ("G3", "Indicators", "auto", _g3_indicators),
    ("G4", "Sector result engine", "manual", None),
    ("G5", "Universe gate", "auto", _g5_universe),
    ("G6", "Move-detection", "auto", _g6_moves),
    ("G7", "Cause engine", "manual", None),
    ("G8", "Earnings / consensus", "manual", None),
    ("G9", "Signal generation", "auto", _g9_signals),
    ("G10", "Dispatch / Telegram", "manual", None),
    ("G11", "Paper-trade + labeling", "manual", None),
    ("G12", "PROFIT GATE (go-live)", "manual", None),
]
_GATE = {g[0]: g for g in GATES}


def ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS gate_results (
            gate_id TEXT PRIMARY KEY, name TEXT, mode TEXT,
            status TEXT, detail TEXT, checked_at TEXT
        )""")


def _store(conn, gid, name, mode, status, detail):
    ensure_table(conn)
    conn.execute(
        "INSERT OR REPLACE INTO gate_results (gate_id, name, mode, status, detail, checked_at) "
        "VALUES (?,?,?,?,?,?)",
        (gid, name, mode, status, detail, dt.datetime.now(IST).isoformat(timespec="seconds")))
    conn.commit()


def run_auto(conn) -> list[dict]:
    """Run every auto gate's live check and persist. Returns the results."""
    out = []
    for gid, name, mode, check in GATES:
        if mode != "auto" or check is None:
            continue
        try:
            status, detail = check(conn)
        except Exception as e:  # noqa: BLE001 — a broken check is a fail, not a crash
            status, detail = "fail", f"check error: {e!r}"
        _store(conn, gid, name, mode, status, detail)
        out.append({"gate_id": gid, "name": name, "mode": mode, "status": status, "detail": detail})
    return out


def build_report(conn) -> dict:
    """Full ladder for the UI: live auto results + stored manual verdicts, in order."""
    ensure_table(conn)
    stored = {r[0]: r for r in conn.execute(
        "SELECT gate_id, status, detail, checked_at FROM gate_results")}
    rows = []
    for gid, name, mode, check in GATES:
        if mode == "auto" and check is not None:
            try:
                status, detail = check(conn)
            except Exception as e:  # noqa: BLE001
                status, detail = "fail", f"check error: {e!r}"
            checked = dt.datetime.now(IST).isoformat(timespec="seconds")
        else:
            s = stored.get(gid)
            status = s[1] if s else "untested"
            detail = s[2] if s else "no verdict recorded"
            checked = s[3] if s else None
        rows.append({"gate_id": gid, "name": name, "mode": mode,
                     "status": status, "detail": detail, "checked_at": checked})
    passed = sum(1 for r in rows if r["status"] == "pass")
    return {"gates": rows, "summary": {"total": len(rows), "passed": passed}}


def main(argv=None) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("run", "set", "show"))
    ap.add_argument("gate_id", nargs="?")
    ap.add_argument("status", nargs="?", choices=("pass", "fail", "warn", "untested"))
    ap.add_argument("note", nargs="?", default="")
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args(argv)
    conn = sqlite3.connect(args.db)

    if args.cmd == "set":
        if not args.gate_id or args.gate_id not in _GATE or not args.status:
            print("usage: gates set <G0..G12> <pass|fail|warn> \"evidence note\"", file=sys.stderr)
            return 2
        name = _GATE[args.gate_id][1]
        _store(conn, args.gate_id, name, "manual", args.status, args.note)
        print(f"recorded {args.gate_id} {name}: {args.status} — {args.note}")
        return 0

    if args.cmd == "run":
        for r in run_auto(conn):
            print(f"  {r['gate_id']:>3} {r['status']:>5}  {r['name']:<26} {r['detail']}")

    rep = build_report(conn)
    print(f"\n=== GATE LADDER ({rep['summary']['passed']}/{rep['summary']['total']} pass) ===")
    icon = {"pass": "✅", "fail": "❌", "warn": "⚠️ ", "untested": "▫️"}
    for r in rep["gates"]:
        print(f"  {icon.get(r['status'],'?')} {r['gate_id']:>3} {r['name']:<26} "
              f"[{r['mode']}] {r['detail']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
