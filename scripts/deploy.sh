#!/usr/bin/env bash
# Update a running deployment to the latest code. Run ON the server, from the
# repo directory:  ./scripts/deploy.sh [systemd-instance-user]
#
# Code comes from git; data/nse.db and .env are gitignored and never touched —
# the DB persists across every deploy. Migrations are forward-only, so the DB is
# backed up first (rollback = restore that backup + git checkout the old tag).
set -euo pipefail
cd "$(dirname "$0")/.."

UNIT="${1:-$USER}"          # systemd instance suffix, e.g. ./scripts/deploy.sh jay
BK="data/archive/db_backups"

echo "==> [1/5] backing up DB (schema changes are forward-only)"
if [ -f data/nse.db ]; then
  mkdir -p "$BK"
  cp -a data/nse.db "$BK/nse.db.$(date +%Y%m%d-%H%M%S)"
  ls -1t "$BK"/nse.db.* | tail -n +31 | xargs -r rm -f   # keep last 30
else
  echo "    (no DB yet — skipping)"
fi

echo "==> [2/5] pulling latest code"
git fetch --all --tags --quiet
git pull --ff-only

echo "==> [3/5] syncing dependencies"
.venv/bin/pip install -q -e ".[dashboard,broker]"

echo "==> [4/5] applying pending migrations"
.venv/bin/python scripts/migrate.py --status
.venv/bin/python scripts/migrate.py

echo "==> [5/5] restarting services (on-boot catch-up recovers the brief gap)"
sudo systemctl restart "nse-collector@${UNIT}"
sudo systemctl restart "nse-dashboard@${UNIT}" 2>/dev/null || true

echo "==> done · journalctl -u nse-collector@${UNIT} -f"
