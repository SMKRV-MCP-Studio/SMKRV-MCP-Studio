#!/bin/sh
# MCP container entrypoint — runs generated server.py with stop-flag watchdog.
#
# Behaviour:
#   1. If .stop flag exists → sleep 5s and exit (Docker restarts us,
#      we see the flag again, sleep, exit — effectively staying stopped).
#   2. Otherwise start server.py in the background.
#   3. Poll for .stop flag every 2s; kill server if flag appears.
#   4. Also exit if the server process dies on its own.

GENERATED_DIR="${GENERATED_DIR:-/shared/generated}"
STOP_FLAG="$GENERATED_DIR/.stop"
SERVER="$GENERATED_DIR/server.py"
SERVER_PID=""

# --- Signal handling ---
cleanup() {
  echo "[entrypoint] Received signal — shutting down."
  if [ -n "$SERVER_PID" ]; then
    kill "$SERVER_PID" 2>/dev/null
    wait "$SERVER_PID" 2>/dev/null
  fi
  exit 0
}
trap cleanup TERM INT QUIT

# --- 1. Check stop flag before starting ---
if [ -f "$STOP_FLAG" ]; then
  echo "[entrypoint] Stop flag detected — server will not start."
  sleep 5
  exit 0
fi

# --- 2. Wait for server.py to exist ---
attempts=0
while [ ! -f "$SERVER" ]; do
  attempts=$((attempts + 1))
  if [ "$attempts" -ge 30 ]; then
    echo "[entrypoint] server.py not found after 60s — exiting."
    exit 1
  fi
  echo "[entrypoint] Waiting for server.py to be generated..."
  sleep 2
done

# --- 3. Start server ---
echo "[entrypoint] Starting MCP server..."
python "$SERVER" &
SERVER_PID=$!

# --- 4. Watchdog loop ---
while true; do
  sleep 2

  # Check if stop flag appeared
  if [ -f "$STOP_FLAG" ]; then
    echo "[entrypoint] Stop flag detected — shutting down server (PID $SERVER_PID)."
    kill "$SERVER_PID" 2>/dev/null
    wait "$SERVER_PID" 2>/dev/null
    exit 0
  fi

  # Check if server process is still alive
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "[entrypoint] Server process exited."
    wait "$SERVER_PID"
    exit $?
  fi
done
