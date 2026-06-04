#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="$ROOT/.venv/bin/python"

if [[ -x "$VENV_PYTHON" ]]; then
  PYTHON="$VENV_PYTHON"
else
  PYTHON="$(command -v python3)"
fi

cleanup() {
  "$PYTHON" "$ROOT/src/manage_dashboards.py" stop-all >/dev/null 2>&1 || true
}

close_terminal_window_if_needed() {
  if [[ "${TERM_PROGRAM:-}" != "Apple_Terminal" ]]; then
    return
  fi

  local tty_device
  tty_device="$(tty 2>/dev/null || true)"
  if [[ -z "$tty_device" || "$tty_device" == "not a tty" ]]; then
    return
  fi

  osascript >/dev/null 2>&1 <<EOF || true
tell application "Terminal"
  set targetTTY to "$tty_device"
  repeat with w in windows
    repeat with t in tabs of w
      if tty of t is targetTTY then
        close w
        return
      end if
    end repeat
  end repeat
end tell
EOF
}

wait_for_http() {
  local port="$1"
  local label="$2"
  local attempt=0
  local max_attempts=120

  echo "Waiting for ${label} to respond on port ${port}..."
  while (( attempt < max_attempts )); do
    if curl --silent --fail --max-time 1 "http://127.0.0.1:${port}" >/dev/null 2>&1; then
      echo "${label} is ready."
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 0.5
  done

  echo "Timed out waiting for ${label} on port ${port}."
  return 1
}

trap cleanup EXIT INT TERM

cd "$ROOT"

echo "Running schema migration..."
"$PYTHON" src/migrate_database_schema.py --database database/future_path.db

echo "Running data pipeline..."
"$PYTHON" src/run_data_pipeline.py

echo "Stopping any stale dashboard servers..."
"$PYTHON" src/manage_dashboards.py stop-all >/dev/null 2>&1 || true

echo "Starting Overview dashboard on port 8501..."
"$PYTHON" src/manage_dashboards.py start overview >/dev/null

wait_for_http 8501 "Overview"

echo
echo "Future Path is running."
echo "Overview:       http://localhost:8501"
echo "Other dashboards launch on demand when you click navigation buttons."
echo "Servers auto-stop after all dashboard tabs are closed."
echo
echo "Press Ctrl+C here to stop all Future Path dashboard servers."

open "http://localhost:8501" >/dev/null 2>&1 || true

"$PYTHON" src/dashboard_idle_watcher.py --idle-seconds 1 --startup-grace-seconds 45
close_terminal_window_if_needed