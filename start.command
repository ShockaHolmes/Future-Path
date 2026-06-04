#!/bin/zsh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="$ROOT/.venv/bin/python"

if [[ -x "$VENV_PYTHON" ]]; then
  PYTHON="$VENV_PYTHON"
else
  PYTHON="$(command -v python3)"
fi

typeset -a STREAMLIT_PIDS=()

cleanup() {
  if (( ${#STREAMLIT_PIDS[@]} > 0 )); then
    kill "${STREAMLIT_PIDS[@]}" 2>/dev/null || true
  fi
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

launch_streamlit() {
  local label="$1"
  local script_path="$2"
  local port="$3"
  local log_path="$ROOT/.${label}.log"

  echo "Starting ${label} on port ${port}..."
  "$PYTHON" -m streamlit run "$ROOT/$script_path" --server.port "$port" --server.headless true >"$log_path" 2>&1 &
  STREAMLIT_PIDS+=("$!")
}

trap cleanup EXIT INT TERM

cd "$ROOT"

echo "Running schema migration..."
"$PYTHON" src/migrate_database_schema.py --database database/future_path.db

echo "Running data pipeline..."
"$PYTHON" src/run_data_pipeline.py

launch_streamlit "overview" "dashboard/overview.py" 8501
launch_streamlit "profile_lookup" "dashboard/profile_lookup.py" 8502
launch_streamlit "ai_assistant" "dashboard/ai_assistant.py" 8503
launch_streamlit "caseworker_dashboard" "dashboard/caseworker_dashboard.py" 8504
launch_streamlit "youth_dashboard" "dashboard/youth_dashboard.py" 8505

wait_for_http 8501 "Overview"
wait_for_http 8502 "Profile Lookup"
wait_for_http 8503 "AI Assistant"
wait_for_http 8504 "Caseworker Dashboard"
wait_for_http 8505 "Youth Dashboard"

echo
echo "Future Path is running."
echo "Overview:       http://localhost:8501"
echo "Profile Lookup: http://localhost:8502"
echo "AI Assistant:   http://localhost:8503"
echo "Caseworker:     http://localhost:8504"
echo "Youth:          http://localhost:8505"
echo
echo "Press Ctrl+C here to stop all Future Path apps."

open "http://localhost:8501" >/dev/null 2>&1 || true
open "http://localhost:8502" >/dev/null 2>&1 || true
open "http://localhost:8503" >/dev/null 2>&1 || true
open "http://localhost:8504" >/dev/null 2>&1 || true
open "http://localhost:8505" >/dev/null 2>&1 || true

wait