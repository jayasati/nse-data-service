#!/usr/bin/env bash
# Show the shareholding-history backfill progress on EC2, on demand.
# Run it from the repo root (the `!` prefix drops the output into your session):
#
#     ! ./scripts/shp_backfill_status.sh
#
# Handles the SSH IP re-allow automatically if your home IP rotated.
set -uo pipefail
cd "$(dirname "$0")/.."

EC2="${EC2:-ubuntu@13.207.114.161}"
KEY="${SSH_KEY:-stock-key.pem}"
LOG="${LOG:-/tmp/shp_backfill3.log}"
RD="${REMOTE_DIR:-/opt/nse-data-service}"
SSH="ssh -i $KEY -o ConnectTimeout=12 -o BatchMode=yes"

# One remote command: status line + progress + table stats (light, no full scans).
remote_cmd="
  if pgrep -f 'backfill_shareholding_history.py' >/dev/null; then echo 'STATUS: RUNNING'; else echo 'STATUS: FINISHED'; fi
  echo -n 'PROGRESS: '; grep -E '^\s*\[[0-9]+/' '$LOG' 2>/dev/null | tail -1 || echo '(no progress line yet)'
  grep -E 'retrying|recovered|^DONE:' '$LOG' 2>/dev/null | tail -2
  cd '$RD' && .venv/bin/python -c \"
import sqlite3
c=sqlite3.connect('file:data/nse.db?mode=ro',uri=True)
rows=c.execute('SELECT COUNT(*) FROM raw_shareholding_quarterly').fetchone()[0]
syms=c.execute('SELECT COUNT(DISTINCT symbol) FROM raw_shareholding_quarterly').fetchone()[0]
sc=c.execute('SELECT COUNT(*) FROM (SELECT symbol FROM raw_shareholding_quarterly GROUP BY symbol HAVING COUNT(*)>=2)').fetchone()[0]
print(f'TABLE: rows={rows}  symbols={syms}  scoreable(>=2q)={sc}')
\" 2>/dev/null || echo 'TABLE: (db busy — try again)'
"

run() { timeout 30 $SSH "$EC2" "$remote_cmd" 2>/dev/null; }

out="$(run)"
if [ -z "$out" ]; then
  echo "==> SSH unreachable; re-allowing this laptop's IP..."
  ./scripts/allow_ssh.sh >/dev/null 2>&1 || echo "   (allow_ssh failed — check AWS creds)"
  out="$(run)"
fi

if [ -z "$out" ]; then
  echo "Could not reach EC2. Run ./scripts/allow_ssh.sh manually, then retry."
  exit 1
fi

echo "================ SHP BACKFILL ================"
echo "$out"
# % complete from the progress line, if present
pct="$(echo "$out" | grep -oE '\[[0-9]+/[0-9]+\]' | tail -1 | tr -d '[]')"
if [ -n "$pct" ]; then
  done_n="${pct%%/*}"; total_n="${pct##*/}"
  [ "$total_n" -gt 0 ] 2>/dev/null && echo "PERCENT: ~$(( done_n * 100 / total_n ))% ($pct symbols)"
fi
echo "=============================================="
