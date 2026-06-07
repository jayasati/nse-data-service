#!/usr/bin/env bash
# Nightly SQLite backup (FEATURE_CHECKLIST Week 6, task 6.2).
#
# Takes a *consistent* snapshot with `sqlite3 .backup` — safe to run while the
# collector is writing (unlike a raw `cp`, which can capture a torn file or a
# stale WAL). The snapshot is gzip-compressed and old backups are pruned.
#
# Run ON the server, from anywhere (paths resolve to the repo root):
#     ./scripts/backup_db.sh
#
# Wire it to 02:00 IST via cron — see docs/DEPLOY.md §12. The DB is ~5.5 GB, so
# each gzip'd snapshot is a few GB; tune retention with RETAIN (default 30) and
# consider off-box S3 copies for durability.
#
# Env overrides:
#     DB        path to the live DB           (default data/nse.db)
#     BK        backup directory              (default data/archive/db_backups)
#     RETAIN    how many snapshots to keep    (default 30)
set -euo pipefail
cd "$(dirname "$0")/.."

DB="${DB:-data/nse.db}"
BK="${BK:-data/archive/db_backups}"
RETAIN="${RETAIN:-30}"

if [ ! -f "$DB" ]; then
  echo "backup_db: no DB at $DB — nothing to back up" >&2
  exit 1
fi

mkdir -p "$BK"
stamp="$(date +%Y%m%d)"
snap="$BK/nse_${stamp}.db"

# Guard: a .backup needs roughly DB-size free space (snapshot) before gzip.
db_kb=$(du -k "$DB" | cut -f1)
free_kb=$(df -Pk "$BK" | awk 'NR==2 {print $4}')
if [ "$free_kb" -lt "$db_kb" ]; then
  echo "backup_db: not enough free space in $BK (need ~${db_kb}K, have ${free_kb}K)" >&2
  exit 1
fi

echo "==> snapshotting $DB -> $snap (consistent .backup)"
sqlite3 "$DB" ".backup '$snap'"

# Integrity check. On a multi-GB DB a full `integrity_check` is slow (minutes),
# so it's tunable via CHECK:
#   quick  (default) -> PRAGMA quick_check — fast, catches most corruption
#   full             -> PRAGMA integrity_check — exhaustive, slow
#   skip             -> no verification (fastest; use only for manual tests)
CHECK="${CHECK:-quick}"
case "$CHECK" in
  skip)
    echo "==> integrity check skipped (CHECK=skip)" ;;
  full)
    echo "==> integrity check (full — this can take minutes on a large DB)"
    ok=$(sqlite3 "$snap" 'PRAGMA integrity_check;') ;;
  *)
    echo "==> integrity check (quick)"
    ok=$(sqlite3 "$snap" 'PRAGMA quick_check;') ;;
esac
if [ "$CHECK" != "skip" ] && [ "$ok" != "ok" ]; then
  echo "backup_db: integrity check FAILED on $snap: $ok" >&2
  rm -f "$snap"
  exit 1
fi

echo "==> compressing"
gzip -f "$snap"            # -> nse_<stamp>.db.gz (re-running same day overwrites)

echo "==> pruning to last $RETAIN snapshots"
# Newest-first; delete everything past RETAIN. Count-based so it's deterministic
# regardless of how often the job runs (a missed night won't drop the window).
ls -1t "$BK"/nse_*.db.gz 2>/dev/null | tail -n +"$((RETAIN + 1))" | xargs -r rm -f

kept=$(ls -1 "$BK"/nse_*.db.gz 2>/dev/null | wc -l)
echo "==> done: $(ls -lh "$snap.gz" | awk '{print $5}') · $kept snapshot(s) retained in $BK"
