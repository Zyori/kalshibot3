#!/usr/bin/env bash
#
# Restart the dashboard build watcher if it has silently stopped rebuilding.
#
# `kalshibot3-dashboard.service` runs `vite build --watch`. Observed failure
# (2026-06-08, twice): the process stays alive and systemd sees it as healthy,
# but its file watch wedges and stops rebuilding dist/ after a source change —
# so the browser keeps getting a stale bundle while everything looks fine.
# Restart=on-failure doesn't catch this because nothing crashes.
#
# Detection is a pure mtime comparison: if the newest source file under
# dashboard/src/ is newer than the newest built file in dashboard/dist/ by more
# than GRACE_S, a rebuild is overdue → the watcher is wedged → restart it. The
# grace window covers a build that's legitimately in flight (vite writes dist/
# a second or two after the source save).
#
# Idempotent and cheap: two find calls + a subtraction. Run by a systemd timer
# every minute. Does nothing when dist/ is current — the steady state.
set -euo pipefail

DASH_DIR="/var/www/lutz-bot/dashboard"
SRC_DIR="$DASH_DIR/src"
DIST_DIR="$DASH_DIR/dist"
SERVICE="kalshibot3-dashboard.service"
GRACE_S=90  # a rebuild older than this past a source change = wedged, not in-flight

newest_mtime() {
  # Highest mtime (epoch seconds, integer) of any regular file under $1, or 0
  # if none. Integer-truncated so the comparison is whole seconds.
  find "$1" -type f -printf '%T@\n' 2>/dev/null | sort -rn | head -1 | cut -d. -f1
}

src_mtime="$(newest_mtime "$SRC_DIR")"
dist_mtime="$(newest_mtime "$DIST_DIR")"
src_mtime="${src_mtime:-0}"
dist_mtime="${dist_mtime:-0}"

# Source ahead of the build by more than the grace window → rebuild overdue.
if (( src_mtime > dist_mtime + GRACE_S )); then
  lag=$(( src_mtime - dist_mtime ))
  logger -t dashboard-build-watchdog \
    "dist stale by ${lag}s (src=${src_mtime} dist=${dist_mtime}); restarting ${SERVICE}"
  systemctl restart "$SERVICE"
fi

# --- Driven by these units (live in /etc/systemd/system/, like the others) ---
#
# kalshibot3-dashboard-watchdog.service:
#   [Unit]
#   Description=kalshibot3 dashboard build watchdog (restart vite watcher if dist goes stale)
#   After=kalshibot3-dashboard.service
#   [Service]
#   Type=oneshot
#   ExecStart=/var/www/lutz-bot/scripts/dashboard-build-watchdog.sh
#
# kalshibot3-dashboard-watchdog.timer:
#   [Unit]
#   Description=Run the kalshibot3 dashboard build watchdog every minute
#   [Timer]
#   OnBootSec=2min
#   OnUnitActiveSec=1min
#   AccuracySec=15s
#   [Install]
#   WantedBy=timers.target
#
# Enable:  systemctl enable --now kalshibot3-dashboard-watchdog.timer
# Watch:   journalctl -t dashboard-build-watchdog -f
