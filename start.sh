#!/usr/bin/env bash
#
# Restart kalshibot3 cleanly. Thin wrapper around systemctl.
#
# Why: process management lives in systemd, not in this script. systemd
# guarantees single-instance by design (it tracks our processes by cgroup,
# not by PID file or pgrep), auto-restarts on crash, and starts the app on
# boot. The old bash kill-then-launch-then-verify dance is gone — it was
# fragile (race windows, loose pgrep patterns) and unnecessary.
#
# Units that drive this (live in /etc/systemd/system/, not in the repo):
#   kalshibot3.service           — FastAPI backend on 127.0.0.1:8000
#   kalshibot3-dashboard.service — `vite build --watch` rebuilding dist/
#
# Useful commands:
#   ./start.sh                          restart everything (this script)
#   systemctl status kalshibot3         backend status + recent log lines
#   systemctl status kalshibot3-dashboard
#   journalctl -u kalshibot3 -f         live backend logs
#   journalctl -u kalshibot3-dashboard -f
#   systemctl stop kalshibot3{,-dashboard}.service     stop everything
#
set -euo pipefail

UNITS=(kalshibot3.service kalshibot3-dashboard.service)

# Refuse to run if the units aren't installed — silently doing nothing would
# be worse than a clear error. The deploy doc covers installation.
for unit in "${UNITS[@]}"; do
  if ! systemctl list-unit-files "$unit" >/dev/null 2>&1; then
    echo "FATAL: $unit not installed. See deploy/SYSTEMD.md."
    exit 1
  fi
done

echo "=== Restarting kalshibot3 ==="
# `systemctl restart` is atomic per unit: stop, wait, start. No race window,
# no chance of a duplicate child being spawned. If a unit was already in a
# failed state from a crash loop, reset-failed clears that first so restart
# can actually proceed.
systemctl reset-failed "${UNITS[@]}" 2>/dev/null || true
systemctl restart "${UNITS[@]}"

# Give the dashboard's ExecStartPre (initial npm run build) a moment to
# complete before we report status — otherwise the dashboard shows as
# "activating" and the summary looks misleading.
sleep 4

echo ""
echo "=== Status ==="
for unit in "${UNITS[@]}"; do
  state=$(systemctl is-active "$unit" 2>&1 || true)
  pid=$(systemctl show -p MainPID --value "$unit")
  printf "  %-32s %-12s PID=%s\n" "$unit" "$state" "$pid"
done

echo ""
echo "=== Sanity check: one backend process ==="
count=$(pgrep -fc "uvicorn src\.main:app" || true)
echo "  uvicorn count: $count (expected: 1)"
if [ "$count" != "1" ]; then
  echo "  WARNING — expected exactly 1 uvicorn process"
fi

echo ""
echo "=== App ==="
echo "  URL:  https://lutz.bot"
echo "  Logs: journalctl -u kalshibot3 -f"
echo "        journalctl -u kalshibot3-dashboard -f"
