#!/usr/bin/env bash
# Update a running deployment to the latest code. Run ON the server, from the
# repo directory:  ./scripts/deploy.sh [systemd-instance-user]
#
# Code comes from git; data/nse.db and .env are gitignored and never touched —
# the DB persists across every deploy. Migrations are forward-only. NOTE: no DB
# backup is taken (backups disabled 2026-06-18) — a bad migration is not
# recoverable, so review migrations before deploying.
set -euo pipefail
cd "$(dirname "$0")/.."

UNIT="${1:-$USER}"          # systemd instance suffix, e.g. ./scripts/deploy.sh jay

echo "==> [1/4] pulling latest code"
git fetch --all --tags --quiet
git pull --ff-only

echo "==> [2/4] syncing dependencies"
.venv/bin/pip install -q -e ".[dashboard,broker,ml,macro]"

echo "==> [3/4] applying pending migrations"
.venv/bin/python scripts/migrate.py --status
.venv/bin/python scripts/migrate.py

echo "==> [4/4] restarting services (on-boot catch-up recovers the brief gap)"
sudo systemctl restart "nse-collector@${UNIT}"
sudo systemctl restart "nse-bot@${UNIT}" 2>/dev/null || true
sudo systemctl restart "nse-dashboard@${UNIT}" 2>/dev/null || true

echo "==> done · journalctl -u nse-collector@${UNIT} -f"
