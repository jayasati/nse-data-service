"""Merge a server_export.db into this machine's nse.db without clobbering.

Stdlib-only. Strategy per table, driven by the LOCAL schema's PRAGMA info:

  * natural-key PK (fingerprint, (date,symbol,series), (symbol,as_of), ...)
        → INSERT OR IGNORE over the intersection of columns: existing local
          rows always win, server rows fill the gaps.
  * single autoincrement `id` PK
        → insert only rows whose full non-id column tuple isn't already
          present (NULL-safe NOT EXISTS) — re-running the import is a no-op.
  * signals + signal_features/signal_outcomes (id-linked cluster)
        → signals merge by their natural key (symbol, signal_type,
          detected_at); an old-id→new-id map is built and the child tables'
          signal_id is REMAPPED before the same merge — the ML archive's
          joins stay intact across machines.

Tables in the export but absent locally are skipped with a warning (schema
drift between deploys is expected; run migrations and re-import to pick the
stragglers up).

    python3 scripts/sync/import_tables.py --db data/nse.db --export data/server_export.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time

SIGNALS_NATURAL_KEY = ("symbol", "signal_type", "detected_at")
SIGNAL_CHILDREN = ("signal_features", "signal_outcomes")


def _cols(conn, db: str, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f'PRAGMA {db}.table_info("{table}")')]


def _pk_cols(conn, table: str) -> list[str]:
    info = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return [r[1] for r in sorted((r for r in info if r[5] > 0), key=lambda r: r[5])]


def _merge_generic(conn, table: str, exp_cols: list[str]) -> int:
    local_cols = _cols(conn, "main", table)
    cols = [c for c in local_cols if c in exp_cols]
    pk = _pk_cols(conn, table)
    collist = ", ".join(f'"{c}"' for c in cols)

    if pk and not (len(pk) == 1 and pk[0].lower() in ("id", "rowid")):
        cur = conn.execute(
            f'INSERT OR IGNORE INTO "{table}" ({collist}) '
            f'SELECT {collist} FROM exp."{table}"')
        return cur.rowcount

    # id-PK table: dedupe on the full non-id tuple (NULL-safe via IS). OR
    # IGNORE on top, because a row can differ in SOME column yet still collide
    # on a UNIQUE constraint (e.g. raw_rating_actions.announcement_fingerprint
    # when the two machines parsed the same filing with different code
    # versions) — the local row wins, same as everywhere else.
    data_cols = [c for c in cols if c.lower() != "id"]
    collist = ", ".join(f'"{c}"' for c in data_cols)
    match = " AND ".join(f't."{c}" IS e."{c}"' for c in data_cols)
    cur = conn.execute(
        f'INSERT OR IGNORE INTO "{table}" ({collist}) '
        f'SELECT {collist} FROM exp."{table}" e '
        f'WHERE NOT EXISTS (SELECT 1 FROM "{table}" t WHERE {match})')
    return cur.rowcount


def _merge_signals_cluster(conn, exported: dict[str, list[str]]) -> dict[str, int]:
    """signals by natural key, then children with signal_id remapped."""
    out: dict[str, int] = {}
    cols = [c for c in _cols(conn, "main", "signals")
            if c in exported["signals"] and c.lower() != "id"]
    collist = ", ".join(f'"{c}"' for c in cols)
    nk = " AND ".join(f't."{c}" IS e."{c}"' for c in SIGNALS_NATURAL_KEY)
    cur = conn.execute(
        f'INSERT INTO signals ({collist}) SELECT {collist} FROM exp.signals e '
        f'WHERE NOT EXISTS (SELECT 1 FROM signals t WHERE {nk})')
    out["signals"] = cur.rowcount

    # old server id -> local id, joined on the natural key (MIN local id wins
    # if a same-minute refire ever produced a duplicate key).
    conn.execute("""
        CREATE TEMP TABLE _sigmap AS
        SELECT e.id AS old_id, (
            SELECT MIN(t.id) FROM signals t
            WHERE t.symbol IS e.symbol AND t.signal_type IS e.signal_type
              AND t.detected_at IS e.detected_at
        ) AS new_id
        FROM exp.signals e""")

    for child in SIGNAL_CHILDREN:
        if child not in exported:
            continue
        ccols = [c for c in _cols(conn, "main", child) if c in exported[child]]
        sel = ", ".join("m.new_id" if c == "signal_id" else f'e."{c}"' for c in ccols)
        collist = ", ".join(f'"{c}"' for c in ccols)
        cur = conn.execute(
            f'INSERT OR IGNORE INTO "{child}" ({collist}) '
            f'SELECT {sel} FROM exp."{child}" e '
            f'JOIN _sigmap m ON m.old_id = e.signal_id WHERE m.new_id IS NOT NULL')
        out[child] = cur.rowcount
    conn.execute("DROP TABLE _sigmap")
    return out


def import_tables(db: str, export: str) -> int:
    conn = sqlite3.connect(db, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"ATTACH DATABASE 'file:{export}?mode=ro' AS exp")

    exported = {r[0]: _cols(conn, "exp", r[0]) for r in conn.execute(
        "SELECT name FROM exp.sqlite_master WHERE type='table'")}
    local = {r[0] for r in conn.execute(
        "SELECT name FROM main.sqlite_master WHERE type='table'")}

    started = time.time()
    report: dict[str, int] = {}
    cluster = [t for t in ("signals", *SIGNAL_CHILDREN) if t in exported]
    if "signals" in exported:
        if "signals" in local:
            report.update(_merge_signals_cluster(conn, exported))
            conn.commit()
        else:
            print("  -- signals: no local table, cluster skipped")

    for table, exp_cols in exported.items():
        if table in cluster:
            continue
        if table not in local:
            print(f"  -- {table}: no local table (migration drift?), skipped")
            continue
        report[table] = _merge_generic(conn, table, exp_cols)
        conn.commit()

    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    for table, n in sorted(report.items()):
        print(f"  {table}: +{n:,} rows")
    print(f"done: {sum(report.values()):,} new rows in {time.time() - started:,.0f}s")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/nse.db")
    ap.add_argument("--export", default="data/server_export.db")
    args = ap.parse_args()
    return import_tables(args.db, args.export)


if __name__ == "__main__":
    sys.exit(main())
