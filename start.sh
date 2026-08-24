#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

PEAP_BACKEND_HOST="${PEAP_BACKEND_HOST:-127.0.0.1}"
PEAP_BACKEND_PORT="${PEAP_BACKEND_PORT:-42679}"
PEAP_FRONTEND_PORT="${PEAP_FRONTEND_PORT:-5173}"
export PEAP_FRONTEND_BACKEND_TARGET="http://${PEAP_BACKEND_HOST}:${PEAP_BACKEND_PORT}"

# Keep runtime coordination outside the source tree.  The launcher is a
# process boundary in its own right, so port probing alone is insufficient:
# two invocations can both observe a free port before either one binds it.
PEAP_RUNTIME_HOME="${PEAP_RUNTIME_HOME:-${PEAP_WORKSPACE_ROOT:-${PEAP_APP_HOME:-${PEAP_DOCUMENTS_HOME:-$HOME/Documents/PEAP}}}}"
PEAP_RUNTIME_LOCK_ROOT="${PEAP_RUNTIME_LOCK_ROOT:-${PEAP_RUNTIME_HOME}/run}"
LAUNCHER_LOCK_DIR="${PEAP_RUNTIME_LOCK_ROOT}/launcher.lock"
LAUNCHER_LOCK_PID_FILE="${LAUNCHER_LOCK_DIR}/pid"
LAUNCHER_LOCK_COMMAND_FILE="${LAUNCHER_LOCK_DIR}/command"
LAUNCHER_LOCK_HELD=0
LAUNCHER_LOCK_OWNER_FILE=""
LAUNCHER_LOCK_OWNER_TOKEN=""
LAUNCHER_LOCK_OWNER_START=""

BACKEND_PID=""
FRONTEND_PID=""
BACKEND_START_IDENTITY=""
FRONTEND_START_IDENTITY=""
PYTHON_COMMAND=()

select_python_command() {
  local requested_python="${PEAP_PYTHON:-}"
  if [ -n "$requested_python" ]; then
    if ! command -v "$requested_python" >/dev/null 2>&1; then
      echo "PEAP_PYTHON is not executable: $requested_python" >&2
      return 1
    fi
    PYTHON_COMMAND=("$requested_python")
    return 0
  fi
  if ! command -v uv >/dev/null 2>&1; then
    echo "Python runtime unavailable: set PEAP_PYTHON or install uv." >&2
    return 1
  fi
  PYTHON_COMMAND=(uv run python)
}

launcher_pid_is_alive() {
  local pid="$1"
  [[ "$pid" =~ ^[0-9]+$ ]] && [ "$pid" -gt 0 ] && kill -0 "$pid" 2>/dev/null
}

launcher_process_start_identity() {
  local pid="$1"
  ps -p "$pid" -o lstart= 2>/dev/null | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' | head -n 1
}

launcher_owner_is_alive() {
  local pid="$1"
  local expected_start="${2:-}"
  if ! launcher_pid_is_alive "$pid"; then
    return 1
  fi
  if [ -z "$expected_start" ]; then
    return 0
  fi
  local observed_start
  observed_start="$(launcher_process_start_identity "$pid")"
  [ -z "$observed_start" ] || [ "$observed_start" = "$expected_start" ]
}

launcher_pending_file_pid() {
  local pending_file="$1"
  local name="${pending_file##*/}"
  local prefix=".launcher.lock.pending-"
  [[ "$name" == "$prefix"* ]] || return 1
  local suffix="${name#"$prefix"}"
  local pid="${suffix%%-*}"
  [[ "$pid" =~ ^[0-9]+$ ]] && [ "$pid" -gt 0 ] || return 1
  printf '%s\n' "$pid"
}

launcher_pending_is_active() {
  local pending_file="$1"
  local metadata_pid pending_start filename_pid filename_start
  metadata_pid="$(sed -n '1p' "$pending_file" 2>/dev/null || true)"
  pending_start="$(sed -n '2p' "$pending_file" 2>/dev/null || true)"
  filename_pid="$(launcher_pending_file_pid "$pending_file" 2>/dev/null || true)"

  # New pending records are atomically published only after all metadata has
  # been written. For an older truncated record, retain it if either possible
  # owner is still alive; otherwise it is safe to remove as stale.
  if launcher_owner_is_alive "$metadata_pid" "$pending_start"; then
    return 0
  fi
  filename_start=""
  if [ -n "$filename_pid" ] && [ "$filename_pid" = "$metadata_pid" ]; then
    filename_start="$pending_start"
  fi
  if launcher_owner_is_alive "$filename_pid" "$filename_start"; then
    return 0
  fi
  [ -z "$metadata_pid" ] && [ -z "$filename_pid" ]
}

launcher_lock_is_initializing() {
  local pending_file
  for pending_file in "$PEAP_RUNTIME_LOCK_ROOT"/.launcher.lock.pending-*; do
    [ -f "$pending_file" ] || continue
    if launcher_pending_is_active "$pending_file"; then
      return 0
    fi
    rm -f -- "$pending_file"
  done
  return 1
}

remove_stale_launcher_lock() {
  local observed_owner_file="${1:-}"
  local legacy_owner="${2:-0}"
  local child child_name
  for child in "$LAUNCHER_LOCK_DIR"/*; do
    [ -e "$child" ] || continue
    child_name="$(basename "$child")"
    if [ -n "$observed_owner_file" ]; then
      [ "$child" = "$observed_owner_file" ] || {
        echo "PEAP launcher lock exists but is not removable: $LAUNCHER_LOCK_DIR" >&2
        return 1
      }
    elif [ "$legacy_owner" -eq 1 ]; then
      case "$child_name" in
        pid|command) ;;
        *)
          echo "PEAP launcher lock exists but is not removable: $LAUNCHER_LOCK_DIR" >&2
          return 1
          ;;
      esac
    else
      echo "PEAP launcher lock exists but is not removable: $LAUNCHER_LOCK_DIR" >&2
      return 1
    fi
  done
  if [ -n "$observed_owner_file" ]; then
    rm -f -- "$observed_owner_file"
  elif [ "$legacy_owner" -eq 1 ]; then
    rm -f "$LAUNCHER_LOCK_PID_FILE" "$LAUNCHER_LOCK_COMMAND_FILE"
  fi
  rmdir "$LAUNCHER_LOCK_DIR" 2>/dev/null || {
    echo "PEAP launcher lock exists but is not removable: $LAUNCHER_LOCK_DIR" >&2
    return 1
  }
}

write_exclusive_launcher_file() {
  local path="$1"
  local content="$2"
  ( set -o noclobber; printf '%s' "$content" > "$path" ) 2>/dev/null
}

write_atomic_launcher_pending_file() {
  local path="$1"
  local temp_path="$2"
  local content="$3"
  if ! write_exclusive_launcher_file "$temp_path" "$content"; then
    return 1
  fi
  if ! mv "$temp_path" "$path"; then
    rm -f -- "$temp_path"
    return 1
  fi
}

inspect_existing_launcher_lock() {
  local pending_file
  for pending_file in "$PEAP_RUNTIME_LOCK_ROOT"/.launcher.lock.pending-*; do
    [ -f "$pending_file" ] || continue
    if launcher_pending_is_active "$pending_file"; then
      echo "PEAP launcher is initializing; another process owns the acquisition." >&2
      return 1
    fi
    rm -f -- "$pending_file"
  done

  local existing_pid=""
  local existing_start=""
  local existing_command=""
  local existing_owner_file=""
  local legacy_owner=0
  local owner_candidate
  for owner_candidate in "$LAUNCHER_LOCK_DIR"/owner-*; do
    [ -f "$owner_candidate" ] || continue
    if [ -n "$existing_owner_file" ]; then
      echo "PEAP launcher lock has multiple owners: $LAUNCHER_LOCK_DIR" >&2
      return 1
    fi
    existing_owner_file="$owner_candidate"
    existing_pid="$(sed -n '1p' "$owner_candidate" 2>/dev/null || true)"
    existing_start="$(sed -n '2p' "$owner_candidate" 2>/dev/null || true)"
    local existing_token
    existing_token="$(sed -n '3p' "$owner_candidate" 2>/dev/null || true)"
    if [ -z "$existing_token" ]; then
      existing_command="$(sed -n '2p' "$owner_candidate" 2>/dev/null || true)"
    else
      existing_command="$(sed -n '4p' "$owner_candidate" 2>/dev/null || true)"
    fi
  done
  if [ -z "$existing_owner_file" ] && [ -f "$LAUNCHER_LOCK_PID_FILE" ]; then
    legacy_owner=1
    existing_pid="$(sed -n '1p' "$LAUNCHER_LOCK_PID_FILE" 2>/dev/null || true)"
    existing_command="$(sed -n '1p' "$LAUNCHER_LOCK_COMMAND_FILE" 2>/dev/null || true)"
  fi
  if launcher_owner_is_alive "$existing_pid" "$existing_start"; then
    echo "PEAP launcher is already running (PID $existing_pid)." >&2
    [ -z "$existing_command" ] || echo "Existing launcher command: $existing_command" >&2
    return 1
  fi
  remove_stale_launcher_lock "$existing_owner_file" "$legacy_owner"
}

acquire_launcher_lock() {
  mkdir -p "$PEAP_RUNTIME_LOCK_ROOT"
  local token="$(printf '%s%s' "$(date +%s%N 2>/dev/null || date +%s)" "${RANDOM:-0}" | shasum 2>/dev/null | cut -c1-32)"
  [ -n "$token" ] || token="$$-$(date +%s)-${RANDOM:-0}"
  local start_identity
  start_identity="$(launcher_process_start_identity "$$")"
  local command="$0 $*"
  local pending_file="${PEAP_RUNTIME_LOCK_ROOT}/.launcher.lock.pending-$$-${token}"
  local pending_temp_file="${PEAP_RUNTIME_LOCK_ROOT}/.launcher.lock.write-pending-$$-${token}"
  local pending_content
  pending_content="$(printf '%s\n%s\n%s\n%s\n' "$$" "$start_identity" "$token" "$command")"
  while true; do
    # A pending record exists before mkdir, so another invocation cannot steal
    # the lock while this shell is still initializing its owner metadata.
    if launcher_lock_is_initializing; then
      echo "PEAP launcher is initializing; another process owns the acquisition." >&2
      return 1
    fi
    if ! write_atomic_launcher_pending_file "$pending_file" "$pending_temp_file" "$pending_content"; then
      token="$$-$(date +%s)-${RANDOM:-0}"
      pending_file="${PEAP_RUNTIME_LOCK_ROOT}/.launcher.lock.pending-$$-${token}"
      pending_temp_file="${PEAP_RUNTIME_LOCK_ROOT}/.launcher.lock.write-pending-$$-${token}"
      pending_content="$(printf '%s\n%s\n%s\n%s\n' "$$" "$start_identity" "$token" "$command")"
      continue
    fi
    if mkdir "$LAUNCHER_LOCK_DIR" 2>/dev/null; then
      LAUNCHER_LOCK_OWNER_FILE="${LAUNCHER_LOCK_DIR}/owner-$$-${token}"
      if ! mv "$pending_file" "$LAUNCHER_LOCK_OWNER_FILE"; then
        rm -f -- "$pending_file"
        rmdir "$LAUNCHER_LOCK_DIR" 2>/dev/null || true
        LAUNCHER_LOCK_OWNER_FILE=""
        echo "Cannot initialize PEAP launcher lock: $LAUNCHER_LOCK_DIR" >&2
        return 1
      fi
      LAUNCHER_LOCK_OWNER_TOKEN="$token"
      LAUNCHER_LOCK_OWNER_START="$start_identity"
      LAUNCHER_LOCK_HELD=1
      return 0
    fi
    rm -f -- "$pending_file"
    inspect_existing_launcher_lock
  done
}

release_launcher_lock() {
  if [ "$LAUNCHER_LOCK_HELD" -ne 1 ]; then
    return 0
  fi
  LAUNCHER_LOCK_HELD=0
  local owner_pid=""
  local owner_token=""
  local owner_start=""
  if [ -n "$LAUNCHER_LOCK_OWNER_FILE" ] && [ -f "$LAUNCHER_LOCK_OWNER_FILE" ]; then
    owner_pid="$(sed -n '1p' "$LAUNCHER_LOCK_OWNER_FILE" 2>/dev/null || true)"
    owner_start="$(sed -n '2p' "$LAUNCHER_LOCK_OWNER_FILE" 2>/dev/null || true)"
    owner_token="$(sed -n '3p' "$LAUNCHER_LOCK_OWNER_FILE" 2>/dev/null || true)"
  fi
  if [ "$owner_pid" != "$$" ] || [ "$owner_token" != "$LAUNCHER_LOCK_OWNER_TOKEN" ] || {
    [ -n "$LAUNCHER_LOCK_OWNER_START" ] && [ "$owner_start" != "$LAUNCHER_LOCK_OWNER_START" ];
  }; then
    return 0
  fi
  rm -f -- "$LAUNCHER_LOCK_OWNER_FILE"
  LAUNCHER_LOCK_OWNER_FILE=""
  LAUNCHER_LOCK_OWNER_TOKEN=""
  LAUNCHER_LOCK_OWNER_START=""
  rmdir "$LAUNCHER_LOCK_DIR" 2>/dev/null || true
}

port_owner() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
  fi
}

require_free_port() {
  local port="$1"
  local label="$2"
  local owner
  owner="$(port_owner "$port")"
  if [ -n "$owner" ]; then
    echo "$label port $port is already in use. Stop the stale process before running start.sh." >&2
    echo "$owner" >&2
    exit 1
  fi
}

kill_tree() {
  local pid="$1"
  local signal_name="${2:-TERM}"
  local expected_start="${3:-}"
  if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  if [ -n "$expected_start" ]; then
    local observed_start
    observed_start="$(launcher_process_start_identity "$pid" || true)"
    if [ -z "$observed_start" ] || [ "$observed_start" != "$expected_start" ]; then
      return 0
    fi
  fi
  if command -v pgrep >/dev/null 2>&1; then
    local child child_start
    for child in $(pgrep -P "$pid" 2>/dev/null || true); do
      child_start="$(launcher_process_start_identity "$child" || true)"
      if [ -n "$expected_start" ] && [ -z "$child_start" ]; then
        continue
      fi
      kill_tree "$child" "$signal_name" "$child_start"
    done
  fi
  if [ -n "$expected_start" ]; then
    local current_start
    current_start="$(launcher_process_start_identity "$pid" || true)"
    if [ -z "$current_start" ] || [ "$current_start" != "$expected_start" ]; then
      return 0
    fi
  fi
  kill -s "$signal_name" "$pid" 2>/dev/null || true
}

wait_for_process_exit() {
  local pid="$1"
  local expected_start="${2:-}"
  local attempt
  if [ -z "$pid" ]; then
    return 0
  fi
  for attempt in $(seq 1 40); do
    if ! kill -0 "$pid" 2>/dev/null; then
      wait "$pid" 2>/dev/null || true
      return 0
    fi
    if [ -n "$expected_start" ]; then
      local observed_start
      observed_start="$(launcher_process_start_identity "$pid" || true)"
      if [ -n "$observed_start" ] && [ "$observed_start" != "$expected_start" ]; then
        return 0
      fi
    fi
    local process_state
    process_state="$(ps -p "$pid" -o stat= 2>/dev/null || true)"
    if [ -z "$process_state" ] || [[ "$process_state" == Z* ]]; then
      wait "$pid" 2>/dev/null || true
      return 0
    fi
    sleep 0.1
  done
  return 1
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if [ -n "$FRONTEND_PID" ] && [ -n "$FRONTEND_START_IDENTITY" ]; then
    kill_tree "$FRONTEND_PID" TERM "$FRONTEND_START_IDENTITY"
    if ! wait_for_process_exit "$FRONTEND_PID" "$FRONTEND_START_IDENTITY"; then
      kill_tree "$FRONTEND_PID" KILL "$FRONTEND_START_IDENTITY"
      wait_for_process_exit "$FRONTEND_PID" "$FRONTEND_START_IDENTITY" || true
    fi
  fi
  if [ -n "$BACKEND_PID" ] && [ -n "$BACKEND_START_IDENTITY" ]; then
    kill_tree "$BACKEND_PID" TERM "$BACKEND_START_IDENTITY"
    if ! wait_for_process_exit "$BACKEND_PID" "$BACKEND_START_IDENTITY"; then
      kill_tree "$BACKEND_PID" KILL "$BACKEND_START_IDENTITY"
      wait_for_process_exit "$BACKEND_PID" "$BACKEND_START_IDENTITY" || true
    fi
  fi
  release_launcher_lock
  exit "$status"
}

wait_for_ready() {
  local url="$1"
  local pid="$2"
  local expected_start="$3"
  local attempt
  for attempt in $(seq 1 80); do
    if ! launcher_owner_is_alive "$pid" "$expected_start"; then
      wait "$pid" || true
      echo "Backend exited before it became ready." >&2
      return 1
    fi
    local payload
    payload="$(curl -fsS --max-time 1 "$url" 2>/dev/null || true)"
    if [ -n "$payload" ] && ready_payload_ok "$payload"; then
      return 0
    fi
    sleep 0.25
  done
  echo "Backend did not become ready at $url." >&2
  return 1
}

ready_payload_ok() {
  local payload="$1"
  printf '%s' "$payload" | "${PYTHON_COMMAND[@]}" -c '
import json
import sys

try:
    envelope = json.load(sys.stdin)
except Exception:
    sys.exit(1)
data = envelope.get("data") if isinstance(envelope, dict) else {}
schema = data.get("schema") if isinstance(data, dict) else {}
if bool(envelope.get("ok")) and bool(data.get("ok")) and bool(schema.get("ready")):
    sys.exit(0)
sys.exit(1)
'
}

wait_for_processes() {
  while true; do
    if [ -n "$BACKEND_PID" ] &&
       ! launcher_owner_is_alive "$BACKEND_PID" "$BACKEND_START_IDENTITY"; then
      wait "$BACKEND_PID"
      return $?
    fi
    if [ -n "$FRONTEND_PID" ] &&
       ! launcher_owner_is_alive "$FRONTEND_PID" "$FRONTEND_START_IDENTITY"; then
      wait "$FRONTEND_PID"
      return $?
    fi
    sleep 0.5
  done
}

trap cleanup EXIT INT TERM

acquire_launcher_lock
select_python_command

require_free_port "$PEAP_BACKEND_PORT" "Backend"
require_free_port "$PEAP_FRONTEND_PORT" "Frontend"

echo "Starting backend..."
"${PYTHON_COMMAND[@]}" -m desktop_backend.app_backend --host "$PEAP_BACKEND_HOST" --port "$PEAP_BACKEND_PORT" &
BACKEND_PID=$!
BACKEND_START_IDENTITY="$(launcher_process_start_identity "$BACKEND_PID")"
if [ -z "$BACKEND_START_IDENTITY" ]; then
  echo "Cannot record the backend process identity; stopping this launch." >&2
  kill "$BACKEND_PID" 2>/dev/null || true
  wait "$BACKEND_PID" 2>/dev/null || true
  BACKEND_PID=""
  exit 1
fi

wait_for_ready \
  "http://${PEAP_BACKEND_HOST}:${PEAP_BACKEND_PORT}/api/ready" \
  "$BACKEND_PID" \
  "$BACKEND_START_IDENTITY"

echo "Starting frontend..."
(cd frontend && npm run dev -- --host 127.0.0.1 --port "$PEAP_FRONTEND_PORT" --strictPort) &
FRONTEND_PID=$!
FRONTEND_START_IDENTITY="$(launcher_process_start_identity "$FRONTEND_PID")"
if [ -z "$FRONTEND_START_IDENTITY" ]; then
  echo "Cannot record the frontend process identity; stopping this launch." >&2
  kill "$FRONTEND_PID" 2>/dev/null || true
  wait "$FRONTEND_PID" 2>/dev/null || true
  FRONTEND_PID=""
  exit 1
fi

echo "Backend PID: $BACKEND_PID"
echo "Frontend PID: $FRONTEND_PID"
echo "Backend URL: http://${PEAP_BACKEND_HOST}:${PEAP_BACKEND_PORT}"
echo "Frontend URL: http://127.0.0.1:${PEAP_FRONTEND_PORT}"
echo "Press Ctrl+C to stop both"

wait_for_processes
