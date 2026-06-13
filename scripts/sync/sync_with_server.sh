#!/usr/bin/env bash
# Two-way laptop <-> EC2 data sync. Run FROM THE LAPTOP, repo root.
#
#   ./scripts/sync/sync_with_server.sh push-candles   # laptop candles -> server (delete + clean reload + indicator recompute)
#   ./scripts/sync/sync_with_server.sh pull-data      # server-collected tables -> laptop (non-clobbering merge)
#   ./scripts/sync/sync_with_server.sh all            # both, in that order
#
# Push-candles sequence:
#   1. export local raw_intraday_candles -> data/candles_export.db (+ manifest)
#   2. rsync it to the server (compressed, resumable — re-run on a dropped link)
#   3. on the server: stop nse-collector, snapshot-backup the DB, delete the
#      exported symbols' candle rows, load the new ones, verify counts
#   4. recompute intraday indicators from the new candles (last 35 days — the
#      5-min indicator tables only retain ~30, everything older is EOD/bhavcopy)
#   5. restart the collector (its on-boot catch-up recovers the gap)
#
# Pull-data sequence:
#   1. scp export_tables.py up, run it on the server -> server_export.db
#   2. rsync it back, merge into the laptop DB by natural keys (local rows win;
#      the signals/ML-archive cluster is id-remapped so its joins survive)
#
# Defaults match docs/DEPLOY.md; override via env:
#   EC2=ubuntu@13.200.215.86  SSH_KEY=stock-key.pem  REMOTE_DIR=/opt/nse-data-service  UNIT=ubuntu
#
# If ssh hangs: your home IP changed — run ./scripts/allow_ssh.sh first.
set -euo pipefail
cd "$(dirname "$0")/../.."

EC2="${EC2:-ubuntu@13.200.215.86}"
SSH_KEY="${SSH_KEY:-stock-key.pem}"
REMOTE_DIR="${REMOTE_DIR:-/opt/nse-data-service}"
UNIT="${UNIT:-ubuntu}"
MODE="${1:?usage: sync_with_server.sh push-candles|pull-data|all}"

SSH="ssh -i $SSH_KEY -o ConnectTimeout=15 $EC2"
RSYNC_SSH="ssh -i $SSH_KEY"

push_candles() {
  echo "==> [push 1/5] exporting local candles"
  python3 scripts/sync/export_candles.py --src data/nse.db --out data/candles_export.db
  du -h data/candles_export.db

  echo "==> [push 2/5] rsync export -> server (resumable; re-run if it drops)"
  rsync -hP -z --inplace -e "$RSYNC_SSH" data/candles_export.db \
      "$EC2:$REMOTE_DIR/data/candles_export.db"
  rsync -e "$RSYNC_SSH" scripts/sync/import_candles.py "$EC2:/tmp/import_candles.py"

  echo "==> [push 3/5] server: stop collector + backup + dry-run preview"
  $SSH "sudo systemctl stop nse-collector@$UNIT nse-bot@$UNIT 2>/dev/null || sudo systemctl stop nse-collector@$UNIT"
  $SSH "cd $REMOTE_DIR && mkdir -p data/archive/db_backups \
        && sqlite3 data/nse.db \".backup 'data/archive/db_backups/nse.db.pre-candle-sync'\" \
        && df -h data | tail -1"
  $SSH "cd $REMOTE_DIR && python3 /tmp/import_candles.py --db data/nse.db --export data/candles_export.db --dry-run"

  echo "==> [push 4/5] server: delete + clean reload + verify"
  $SSH "cd $REMOTE_DIR && python3 /tmp/import_candles.py --db data/nse.db --export data/candles_export.db"

  echo "==> [push 5/5] server: recompute intraday indicators + restart services"
  $SSH "cd $REMOTE_DIR && (.venv/bin/python scripts/recompute_indicators.py --days 35 --cadence intraday \
        || echo 'WARN: recompute failed/missing — run it manually after deploying latest code')"
  $SSH "sudo systemctl start nse-collector@$UNIT && (sudo systemctl start nse-bot@$UNIT 2>/dev/null || true) \
        && rm -f $REMOTE_DIR/data/candles_export.db"
  echo "==> push-candles done"
}

pull_data() {
  echo "==> [pull 1/3] server: export collected tables"
  rsync -e "$RSYNC_SSH" scripts/sync/export_tables.py "$EC2:/tmp/export_tables.py"
  $SSH "cd $REMOTE_DIR && rm -f data/server_export.db \
        && python3 /tmp/export_tables.py --src data/nse.db --out data/server_export.db \
        && du -h data/server_export.db"

  echo "==> [pull 2/3] rsync export -> laptop (resumable)"
  rsync -hP -z --inplace -e "$RSYNC_SSH" "$EC2:$REMOTE_DIR/data/server_export.db" \
      data/server_export.db

  echo "==> [pull 3/3] merging into local DB (existing local rows win)"
  python3 scripts/sync/import_tables.py --db data/nse.db --export data/server_export.db
  $SSH "rm -f $REMOTE_DIR/data/server_export.db"
  echo "==> pull-data done"
}

case "$MODE" in
  push-candles) push_candles ;;
  pull-data)    pull_data ;;
  all)          push_candles; pull_data ;;
  *) echo "unknown mode: $MODE"; exit 1 ;;
esac
