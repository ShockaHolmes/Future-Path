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

START_DASHBOARD="${1:-overview}"
case "$START_DASHBOARD" in
  overview)
    DASHBOARD_LABEL="Overview"
    DASHBOARD_PORT=8601
    ;;
  profile_lookup)
    DASHBOARD_LABEL="Youth Profiles"
    DASHBOARD_PORT=8602
    ;;
  ai_assistant)
    DASHBOARD_LABEL="AI Assistant"
    DASHBOARD_PORT=8603
    ;;
  caseworker_dashboard)
    DASHBOARD_LABEL="Caseworker Dashboard"
    DASHBOARD_PORT=8604
    ;;
  youth_dashboard)
    DASHBOARD_LABEL="Youth Dashboard"
    DASHBOARD_PORT=8605
    ;;
  *)
    echo "Unknown dashboard key: $START_DASHBOARD"
    echo "Valid options: overview, profile_lookup, ai_assistant, caseworker_dashboard, youth_dashboard"
    exit 1
    ;;
esac

echo "Running schema migration..."
"$PYTHON" src/migrate_database_schema.py --database database/future_path.db

echo "Running data pipeline..."
"$PYTHON" src/run_data_pipeline.py

echo "Stopping any stale dashboard servers..."
"$PYTHON" src/manage_dashboards.py stop-all >/dev/null 2>&1 || true

echo "Starting all dashboards..."
"$PYTHON" src/manage_dashboards.py start overview
"$PYTHON" src/manage_dashboards.py start profile_lookup
"$PYTHON" src/manage_dashboards.py start ai_assistant
"$PYTHON" src/manage_dashboards.py start caseworker_dashboard
"$PYTHON" src/manage_dashboards.py start youth_dashboard

wait_for_http 8601 "Overview"
wait_for_http 8602 "Youth Profiles"
wait_for_http 8603 "AI Assistant"
wait_for_http 8604 "Caseworker Dashboard"
wait_for_http 8605 "Youth Dashboard"

echo
echo "Future Path is running."
echo "Overview:               http://localhost:8601"
echo "Youth Profiles:         http://localhost:8602"
echo "AI Assistant:           http://localhost:8603"
echo "Caseworker Dashboard:   http://localhost:8604"
echo "Youth Dashboard:        http://localhost:8605"
echo "All dashboards stay running so switching is instant."
echo "Servers auto-stop after all dashboard tabs are closed."
echo
echo "Press Ctrl+C here to stop all Future Path dashboard servers."

open "http://localhost:${DASHBOARD_PORT}" >/dev/null 2>&1 || true
for port in 8601 8602 8603 8604 8605; do
  if [[ "$port" != "$DASHBOARD_PORT" ]]; then
    open "http://localhost:${port}" >/dev/null 2>&1 || true
  fi
done

"$PYTHON" src/dashboard_idle_watcher.py --idle-seconds 20 --startup-grace-seconds 45
close_terminal_window_if_needed