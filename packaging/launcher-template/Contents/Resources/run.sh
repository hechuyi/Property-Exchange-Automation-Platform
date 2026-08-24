#!/bin/bash
set -euo pipefail

SOURCE_PROJECT_ROOT="${1:-}"
PROJECT_ROOT="$SOURCE_PROJECT_ROOT"
RESOURCE_ROOT="$(cd "$(dirname "$0")" && pwd)"
RELEASE_ID_FILE="$RESOURCE_ROOT/release-id.txt"
INSTALL_OWNED_ROOT=""
INSTALL_OWNERSHIP_MARKER=""
INSTALL_OWNERSHIP_TOKEN=""
START_PID=""
OPEN_FRONTEND_PID=""

show_error() {
  echo "$1" >&2
  if [[ "${PEAP_LAUNCHER_NO_DIALOGS:-0}" == "1" ]]; then
    return 0
  fi
  /usr/bin/osascript - "$1" <<'APPLESCRIPT'
on run argv
  display dialog (item 1 of argv) with title "PEAP Launcher" buttons {"OK"} default button "OK" with icon stop
end run
APPLESCRIPT
}

cleanup_owned_install() {
  local observed_token=""
  if [[ -n "$INSTALL_OWNERSHIP_MARKER" && -f "$INSTALL_OWNERSHIP_MARKER" ]]; then
    observed_token="$(/bin/cat "$INSTALL_OWNERSHIP_MARKER")"
  fi
  if [[ -n "$INSTALL_OWNED_ROOT" &&
        -n "$INSTALL_OWNERSHIP_TOKEN" &&
        "$observed_token" == "$INSTALL_OWNERSHIP_TOKEN" ]]; then
    rm -rf -- "$INSTALL_OWNED_ROOT"
  fi
}

cleanup_run() {
  local status=$?
  trap - EXIT INT TERM HUP

  # Forward termination to start.sh, which owns backend/frontend cleanup and
  # the runtime lock. Also stop the browser-opening readiness watcher.
  if [[ -n "$START_PID" ]] && kill -0 "$START_PID" 2>/dev/null; then
    kill -TERM "$START_PID" 2>/dev/null || true
    wait "$START_PID" 2>/dev/null || true
  fi
  if [[ -n "$OPEN_FRONTEND_PID" ]] && kill -0 "$OPEN_FRONTEND_PID" 2>/dev/null; then
    kill -TERM "$OPEN_FRONTEND_PID" 2>/dev/null || true
    wait "$OPEN_FRONTEND_PID" 2>/dev/null || true
  fi
  cleanup_owned_install
  exit "$status"
}

trap cleanup_run EXIT INT TERM HUP

if [[ -z "$SOURCE_PROJECT_ROOT" || ! -f "$SOURCE_PROJECT_ROOT/start.sh" ]]; then
  show_error "启动器资源不完整：缺少内置项目文件。"
  exit 1
fi
if [[ ! -s "$RELEASE_ID_FILE" ]]; then
  show_error "启动器资源不完整：缺少发行标识。"
  exit 1
fi

if [[ "${PEAP_LAUNCHER_ALLOW_INTERNAL_OVERRIDES:-0}" == "1" && -n "${PEAP_LAUNCHER_RELEASE_ID:-}" ]]; then
  RELEASE_ID="$PEAP_LAUNCHER_RELEASE_ID"
else
  RELEASE_ID="$(/bin/cat "$RELEASE_ID_FILE")"
fi
if (( ${#RELEASE_ID} < 1 || ${#RELEASE_ID} > 80 )) ||
   ! [[ "$RELEASE_ID" =~ ^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$ ]]; then
  show_error "启动器资源不完整：发行标识格式无效。"
  exit 1
fi
DEFAULT_PROJECT_ROOT="$HOME/Documents/PEAP/source/$RELEASE_ID"
if [[ "${PEAP_LAUNCHER_ALLOW_CUSTOM_ROOT:-0}" == "1" && -n "${PEAP_LAUNCHER_PROJECT_ROOT:-}" ]]; then
  PROJECT_ROOT="$PEAP_LAUNCHER_PROJECT_ROOT"
else
  # Ignore inherited/internal override variables during normal distribution
  # launches. Custom roots are an explicit operator/test opt-in only.
  PROJECT_ROOT="$DEFAULT_PROJECT_ROOT"
fi
if [[ "$PROJECT_ROOT" != /* || "$PROJECT_ROOT" == "/" || "$PROJECT_ROOT" == "$HOME" ]]; then
  show_error "源码目录必须是安全的绝对路径。"
  exit 1
fi
READY_MARKER="$PROJECT_ROOT/.peap-launcher-source-$RELEASE_ID"

source_is_ready() {
  local marker_value=""
  if [[ -f "$READY_MARKER" ]]; then
    marker_value="$(/bin/cat "$READY_MARKER")"
  fi
  [[ "$marker_value" == "$RELEASE_ID" &&
     -f "$PROJECT_ROOT/DISTRIBUTION_MANIFEST.json" &&
     -f "$PROJECT_ROOT/start.sh" &&
     -f "$PROJECT_ROOT/pyproject.toml" &&
     -f "$PROJECT_ROOT/uv.lock" &&
     -f "$PROJECT_ROOT/desktop_backend/requirements.lock.txt" &&
     -f "$PROJECT_ROOT/frontend/package-lock.json" &&
     -f "$PROJECT_ROOT/scripts/_paths.py" &&
     -x "$PROJECT_ROOT/frontend/node_modules/.bin/vite" ]]
}

if [[ -e "$PROJECT_ROOT" || -L "$PROJECT_ROOT" ]]; then
  if [[ -L "$PROJECT_ROOT" || ! -d "$PROJECT_ROOT" ]] || ! source_is_ready; then
    show_error "源码目录 ${PROJECT_ROOT} 已存在但不属于这个完整发行版。为保护其中的文件，启动器不会覆盖或删除它。"
    exit 1
  fi
else
  echo "首次运行：正在准备可编辑源码..."
  if ! mkdir -p "$(dirname "$PROJECT_ROOT")"; then
    show_error "无法创建源码目录的父目录。"
    exit 1
  fi
  INSTALL_OWNERSHIP_TOKEN="$$-$(date +%s)-${RANDOM:-0}"
  INSTALL_OWNERSHIP_MARKER="$PROJECT_ROOT/.peap-launcher-install-$INSTALL_OWNERSHIP_TOKEN"
  if ! mkdir "$PROJECT_ROOT"; then
    show_error "源码目录 ${PROJECT_ROOT} 在安装期间被占用；启动器没有覆盖其中的文件。"
    exit 1
  fi
  INSTALL_OWNED_ROOT="$PROJECT_ROOT"
  if ! (set -o noclobber; printf '%s\n' "$INSTALL_OWNERSHIP_TOKEN" > "$INSTALL_OWNERSHIP_MARKER") ||
     ! /usr/bin/ditto "$SOURCE_PROJECT_ROOT" "$PROJECT_ROOT" ||
     ! printf '%s\n' "$RELEASE_ID" > "$READY_MARKER"; then
    show_error "无法把内置项目文件准备到 ${PROJECT_ROOT}。"
    exit 1
  fi
  if ! source_is_ready; then
    show_error "无法安装可编辑源码 ${PROJECT_ROOT}。"
    exit 1
  fi
  rm -f -- "$INSTALL_OWNERSHIP_MARKER"
  INSTALL_OWNED_ROOT=""
  INSTALL_OWNERSHIP_MARKER=""
  INSTALL_OWNERSHIP_TOKEN=""
fi
echo "源码目录：$PROJECT_ROOT"

export PEAP_LAUNCHER_RESOURCE_ROOT="$RESOURCE_ROOT"
source "$RESOURCE_ROOT/initialize.sh"

if ! initialize_peap_environment "$PROJECT_ROOT"; then
  show_error "PEAP 初始化失败。请查看终端中的错误信息。"
  echo
  read -r -p "按回车键关闭此窗口..." _
  exit 1
fi

if [[ "${PEAP_LAUNCHER_ALLOW_INTERNAL_OVERRIDES:-0}" == "1" && "${PEAP_LAUNCHER_INIT_ONLY:-0}" == "1" ]]; then
  exit 0
fi

port_is_free() {
  local port="$1"
  if ! command -v lsof >/dev/null 2>&1; then
    return 0
  fi
  ! lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
}

select_runtime_port() {
  local requested="$1"
  local candidate="$requested"
  local attempt

  if ! [[ "$candidate" =~ ^[0-9]+$ ]] || (( candidate < 1024 || candidate > 65535 )); then
    return 1
  fi
  if [[ "${PEAP_FIXED_PORTS:-0}" == "1" ]]; then
    printf '%s\n' "$candidate"
    return 0
  fi
  for ((attempt = 0; attempt < 100; attempt++)); do
    if port_is_free "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
    candidate=$((candidate + 1))
    if (( candidate > 65535 )); then
      break
    fi
  done
  return 1
}

requested_backend_port="${PEAP_BACKEND_PORT:-42679}"
requested_frontend_port="${PEAP_FRONTEND_PORT:-5173}"
if ! PEAP_BACKEND_PORT="$(select_runtime_port "$requested_backend_port")"; then
  show_error "找不到可用的后端端口。请关闭占用端口的程序后重试。"
  exit 1
fi
if ! PEAP_FRONTEND_PORT="$(select_runtime_port "$requested_frontend_port")"; then
  show_error "找不到可用的前端端口。请关闭占用端口的程序后重试。"
  exit 1
fi
if [[ "$PEAP_FRONTEND_PORT" == "$PEAP_BACKEND_PORT" ]]; then
  next_frontend_port=$((PEAP_FRONTEND_PORT + 1))
  if ! PEAP_FRONTEND_PORT="$(select_runtime_port "$next_frontend_port")"; then
    show_error "无法为前端分配独立端口。"
    exit 1
  fi
fi
export PEAP_BACKEND_PORT PEAP_FRONTEND_PORT
echo "使用后端端口：$PEAP_BACKEND_PORT"
echo "使用前端端口：$PEAP_FRONTEND_PORT"

open_frontend_when_ready() {
  local frontend_url="http://127.0.0.1:${PEAP_FRONTEND_PORT}"
  local backend_ready_url="http://127.0.0.1:${PEAP_BACKEND_PORT}/api/ready"
  local attempt
  for attempt in $(seq 1 480); do
    if curl -fsS --max-time 1 "$backend_ready_url" >/dev/null 2>&1 &&
       curl -fsS --max-time 1 "$frontend_url" >/dev/null 2>&1; then
      open "$frontend_url"
      return 0
    fi
    sleep 0.25
  done
  return 1
}

set +e
(cd "$PROJECT_ROOT" && exec bash start.sh) &
START_PID=$!

open_frontend_when_ready &
OPEN_FRONTEND_PID=$!

wait "$START_PID"
status=$?
START_PID=""
set -e

if [[ "$status" -ne 0 && "$status" -ne 130 ]]; then
  show_error "PEAP 启动失败（退出码 ${status}）。请查看终端中的错误信息。"
  echo
  read -r -p "按回车键关闭此窗口..." _
fi
exit "$status"
