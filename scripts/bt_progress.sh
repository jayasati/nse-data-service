#!/usr/bin/env bash
# Continuously stream progress of the backtest jobs running on EC2.
# Run from repo root (the `!` prefix drops the live output into your session):
#
#     ! ./scripts/bt_progress.sh            # refresh every 15s until jobs finish
#     INTERVAL=30 ! ./scripts/bt_progress.sh
#
# Prints a status block each tick (scored N/104 → result line), and stops when no
# backtest is running. Ctrl-C to quit early. Auto re-allows SSH if your IP rotated.
set -uo pipefail
cd "$(dirname "$0")/.."

EC2="${EC2:-ubuntu@13.207.114.161}"
KEY="${SSH_KEY:-stock-key.pem}"
INTERVAL="${INTERVAL:-15}"
MAX_TICKS="${MAX_TICKS:-60}"          # safety cap (~15 min at 15s)
SSH="ssh -i $KEY -o ConnectTimeout=12 -o BatchMode=yes"

# remote: count running backtest procs + one clean line per recent bt log
# (result headline if finished, else the live progress line)
read -r -d '' REMOTE <<'EOF' || true
n=$(ps -eo args | grep -E '\.venv/bin/python.*(backtest_|strategy_sweep|strategy_pit)' | grep -v grep | wc -l)
echo "RUNNING=$n"
for f in $(ls -t /tmp/bt_*.log 2>/dev/null | head -5); do
  res=$(grep -E 'CAGR=|^  trades=' "$f" 2>/dev/null | tail -1 | sed 's/^ *//')
  prog=$(grep -E 'scored [0-9]+/|loading candles|universe gate' "$f" 2>/dev/null | tail -1 | sed 's/^ *//')
  printf '%-20s %s\n' "$(basename "$f")" "${res:-${prog:-starting...}}"
done
EOF

reach() { timeout 14 $SSH "$EC2" true 2>/dev/null; }
if ! reach; then
  echo "==> SSH unreachable; re-allowing IP..."
  ./scripts/allow_ssh.sh >/dev/null 2>&1 || echo "   (allow_ssh failed — check AWS creds)"
fi

tick=0
while [ "$tick" -lt "$MAX_TICKS" ]; do
  out="$(timeout 25 $SSH "$EC2" "$REMOTE" 2>/dev/null)"
  echo "================ BACKTESTS @ $(date +%H:%M:%S) ================"
  if [ -z "$out" ]; then
    echo "(no response — retrying next tick)"
  else
    echo "$out"
    running="$(printf '%s\n' "$out" | sed -n 's/^RUNNING=//p')"
    if [ "${running:-0}" = "0" ]; then
      echo "=> all backtests finished."
      break
    fi
  fi
  tick=$((tick + 1))
  sleep "$INTERVAL"
done
