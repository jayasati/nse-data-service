#!/usr/bin/env bash
# Transfer data/nse.db (~5 GB) from this laptop to the VPS — safely & resumably.
#
#   ./scripts/transfer_db.sh ubuntu@<VPS_IP> [remote_dir] [ssh_key.pem]
#
# Examples:
#   ./scripts/transfer_db.sh ubuntu@13.200.1.2
#   ./scripts/transfer_db.sh ubuntu@13.200.1.2 /opt/nse-data-service/data ~/Downloads/nse-key.pem
#
# Why a snapshot first: the collector may be writing to nse.db. rsync'ing a live
# SQLite file can copy a torn page and land a corrupt DB on the server. So we
# take a consistent copy with `sqlite3 .backup` (safe while the DB is in use),
# then rsync the snapshot. rsync is resumable (--partial), so a dropped SSH
# connection mid-transfer just picks up where it left off on the next run.
#
# Set SKIP_SNAPSHOT=1 to rsync data/nse.db directly — ONLY do this if the local
# collector is stopped (otherwise you risk a torn copy).
set -euo pipefail
cd "$(dirname "$0")/.."

DEST="${1:?usage: transfer_db.sh user@host [remote_dir] [ssh_key]}"
REMOTE_DIR="${2:-/opt/nse-data-service/data}"
SSH_KEY="${3:-}"

DB="data/nse.db"
SNAP="data/nse.db.transfer-snapshot"

[ -f "$DB" ] || { echo "ERROR: $DB not found (run from repo root, on the laptop)"; exit 1; }
command -v rsync   >/dev/null || { echo "ERROR: rsync not installed"; exit 1; }

SSH_CMD="ssh"
[ -n "$SSH_KEY" ] && SSH_CMD="ssh -i $SSH_KEY"

SRC="$DB"
if [ "${SKIP_SNAPSHOT:-0}" != "1" ]; then
  command -v sqlite3 >/dev/null || { echo "ERROR: sqlite3 not installed (or set SKIP_SNAPSHOT=1 with the collector stopped)"; exit 1; }
  echo "==> [1/3] taking a consistent snapshot via VACUUM INTO (single-pass, safe while running)"
  # NOT `.backup`: on a continuously-written WAL DB, sqlite3 .backup restarts on every
  # concurrent change and can livelock for HOURS, holding a read txn that pins the WAL
  # (seen: a backup ran 32h → 7.2 GB WAL). VACUUM INTO is one atomic pass that finishes.
  rm -f "$SNAP"
  sqlite3 "$DB" "VACUUM INTO '$SNAP'"
  SRC="$SNAP"
else
  echo "==> [1/3] SKIP_SNAPSHOT=1 — rsyncing the live DB directly (ensure collector is stopped!)"
fi

echo "==> [2/3] ensuring remote dir exists: $REMOTE_DIR"
$SSH_CMD "${DEST}" "mkdir -p '${REMOTE_DIR}'"

echo "==> [3/3] rsync $(du -h "$SRC" | cut -f1) → ${DEST}:${REMOTE_DIR}/nse.db  (compressed, resumable)"
# -P = --partial --progress (resume on reconnect, show a progress bar)
# -z = compress on the wire   --inplace = write directly (no +5GB temp on server)
rsync -hP -z --inplace -e "$SSH_CMD" "$SRC" "${DEST}:${REMOTE_DIR}/nse.db"

if [ "${SKIP_SNAPSHOT:-0}" != "1" ]; then
  rm -f "$SNAP"
fi

echo "==> done. Verify on the server:"
echo "    ${SSH_CMD} ${DEST} \"sqlite3 ${REMOTE_DIR}/nse.db 'PRAGMA integrity_check;'\""
echo "    (expect: ok)"
