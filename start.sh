#!/usr/bin/env bash
#
# Single-instance launcher for kalshibot3 (backend + dashboard).
#
# Why this is paranoid:
#   On 2026-03-24 (in V1, Kalshi-Bot), two bot instances ran simultaneously because the old
#   process wasn't killed. Both placed orders against the same markets at the same millisecond,
#   doubling every position with real money. This script exists to make that impossible.
#
# What it does:
#   1. Lockfile prevents two start.sh invocations from racing.
#   2. Kills any prior backend/dashboard processes (graceful SIGTERM, then SIGKILL).
#   3. Frees the backend port if anything else is holding it.
#   4. Rotates the prior log so we never overwrite a session's history.
#   5. Launches uvicorn (backend) and vite (dashboard) as direct children.
#   6. Verifies after launch that exactly ONE backend process exists, owning the port.
#
# Usage: ./start.sh
#
set -euo pipefail
cd "$(dirname "$0")"

BACKEND_PORT=8000
DASHBOARD_PORT=5173
LOCKFILE=".bot.lock"
LOG_DIR="logs"
BACKEND_LOG="backend.log"
DASHBOARD_LOG="dashboard.log"
MAX_LOG_HISTORY=10

# === LOCKFILE — prevent concurrent start.sh invocations ===
if [ -f "$LOCKFILE" ]; then
  lock_pid=$(cat "$LOCKFILE" 2>/dev/null || true)
  if [ -n "$lock_pid" ] && kill -0 "$lock_pid" 2>/dev/null; then
    echo "FATAL: Another start.sh is running (PID $lock_pid). Aborting."
    exit 1
  fi
  echo "  Stale lockfile (PID $lock_pid dead). Removing."
  rm -f "$LOCKFILE"
fi
echo $$ > "$LOCKFILE"
trap 'rm -f "$LOCKFILE"' EXIT

# === LOG ROTATION ===
mkdir -p "$LOG_DIR"
for log in "$BACKEND_LOG" "$DASHBOARD_LOG"; do
  if [ -f "$log" ] && [ -s "$log" ]; then
    timestamp=$(date +%Y%m%d-%H%M%S)
    mv "$log" "$LOG_DIR/${log%.log}-${timestamp}.log"
    echo "  Rotated $log"
  fi
done
# Prune old logs
log_count=$(ls -1 "$LOG_DIR"/*.log 2>/dev/null | wc -l | tr -d ' ')
if [ "$log_count" -gt "$MAX_LOG_HISTORY" ]; then
  ls -1t "$LOG_DIR"/*.log | tail -n +"$((MAX_LOG_HISTORY + 1))" | xargs rm -f
  echo "  Pruned old logs (keeping last $MAX_LOG_HISTORY)"
fi

echo "=== Killing existing processes ==="

# Kill by PID files first — clean shutdown when possible.
for pidfile in backend.pid dashboard.pid; do
  if [ -f "$pidfile" ]; then
    pid=$(cat "$pidfile")
    if kill -0 "$pid" 2>/dev/null; then
      echo "  SIGTERM to PID $pid (from $pidfile)"
      kill "$pid" 2>/dev/null || true
      for i in $(seq 1 10); do
        if ! kill -0 "$pid" 2>/dev/null; then
          echo "  PID $pid exited after ${i}s"
          break
        fi
        sleep 1
      done
      if kill -0 "$pid" 2>/dev/null; then
        echo "  PID $pid didn't exit gracefully — SIGKILL"
        kill -9 "$pid" 2>/dev/null || true
        sleep 1
      fi
    fi
    rm -f "$pidfile"
  fi
done

# Catch-all: kill anything matching our process signatures.
# Patterns chosen to be broad enough to catch manual invocations.
for pattern in \
  "uvicorn.*src\.main:app" \
  "vite.*--port.*${DASHBOARD_PORT}" \
  "node.*dashboard.*vite"; do
  pids=$(pgrep -f "$pattern" 2>/dev/null || true)
  if [ -n "$pids" ]; then
    echo "  Killing processes matching '$pattern': $pids"
    echo "$pids" | xargs kill 2>/dev/null || true
  fi
done

sleep 2

# Force-kill survivors
for pattern in "uvicorn.*src\.main:app" "vite.*--port.*${DASHBOARD_PORT}"; do
  remaining=$(pgrep -f "$pattern" 2>/dev/null || true)
  if [ -n "$remaining" ]; then
    echo "  Force-killing stubborn processes: $remaining"
    echo "$remaining" | xargs kill -9 2>/dev/null || true
  fi
done
sleep 1

# Free backend port if anything still holds it
port_pid=$(lsof -ti :$BACKEND_PORT 2>/dev/null || true)
if [ -n "$port_pid" ]; then
  echo "  Port $BACKEND_PORT still held by PID $port_pid — killing"
  echo "$port_pid" | xargs kill -9 2>/dev/null || true
  sleep 1
fi

port_pid=$(lsof -ti :$BACKEND_PORT 2>/dev/null || true)
if [ -n "$port_pid" ]; then
  echo "FATAL: Port $BACKEND_PORT still in use by PID $port_pid after cleanup."
  exit 1
fi
echo "  All clear."

echo ""
echo "=== Starting backend ==="
cd backend
# Run uvicorn directly so $! captures the actual PID, not a shell wrapper.
.venv/bin/uvicorn src.main:app --host 127.0.0.1 --port $BACKEND_PORT --workers 1 \
  >> "../$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!
cd ..
echo "$BACKEND_PID" > backend.pid
echo "  Backend started, PID: $BACKEND_PID"

echo ""
echo "=== Starting dashboard ==="
cd dashboard
npm run dev > "../$DASHBOARD_LOG" 2>&1 &
DASHBOARD_PID=$!
cd ..
echo "$DASHBOARD_PID" > dashboard.pid
echo "  Dashboard started, PID: $DASHBOARD_PID"

# === POST-LAUNCH VERIFICATION ===
echo ""
echo "=== Verifying single instance ==="
sleep 3

if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
  echo "FATAL: Backend died immediately. Check $BACKEND_LOG."
  exit 1
fi

backend_count=$(pgrep -f "uvicorn.*src\.main:app" 2>/dev/null | wc -l | tr -d ' ')
if [ "$backend_count" -gt 1 ]; then
  echo "FATAL: $backend_count backend processes detected. Killing all."
  pgrep -f "uvicorn.*src\.main:app" 2>/dev/null | xargs kill -9 2>/dev/null || true
  rm -f backend.pid
  echo "  Investigate before restarting."
  exit 1
fi

running_pid=$(pgrep -f "uvicorn.*src\.main:app" 2>/dev/null || true)
if [ "$running_pid" != "$BACKEND_PID" ]; then
  echo "FATAL: Running PID ($running_pid) != launched PID ($BACKEND_PID). Killing all."
  pgrep -f "uvicorn.*src\.main:app" 2>/dev/null | xargs kill -9 2>/dev/null || true
  rm -f backend.pid
  exit 1
fi

echo "  Single instance verified: PID $BACKEND_PID"

echo ""
echo "=== Running ==="
echo "  Backend:   http://127.0.0.1:$BACKEND_PORT (PID $BACKEND_PID, log: $BACKEND_LOG)"
echo "  Dashboard: http://127.0.0.1:$DASHBOARD_PORT (PID $DASHBOARD_PID, log: $DASHBOARD_LOG)"
