#!/bin/bash

initialize_peap_environment() {
  local project_root="$1"
  local resource_root
  local runtime_root
  local workspace_root
  local browser_cache_dir
  local python_bin
  local node_major
  local machine_arch

  resource_root="${PEAP_LAUNCHER_RESOURCE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
  runtime_root="$resource_root/Runtime/arm64"
  workspace_root="${PEAP_WORKSPACE_ROOT:-${PEAP_APP_HOME:-${PEAP_DOCUMENTS_HOME:-$HOME/Documents/PEAP}}}"
  browser_cache_dir="$runtime_root/ms-playwright"
  python_bin="$runtime_root/python/bin/python3.11"

  machine_arch="$(uname -m)"
  if [[ "$machine_arch" != "arm64" && "$machine_arch" != "aarch64" ]]; then
    echo "启动失败：此离线包适用于 Apple Silicon Mac。" >&2
    return 1
  fi
  if [[ ! -x "$python_bin" || ! -x "$runtime_root/node/bin/node" || ! -x "$runtime_root/node/bin/npm" ]]; then
    echo "启动失败：App 内置 Python 或 Node.js 不完整。" >&2
    return 1
  fi
  if ! find "$browser_cache_dir" -maxdepth 1 -type d -name 'chromium-*' -print -quit 2>/dev/null | grep -q . ||
     ! find "$browser_cache_dir" -maxdepth 1 -type d -name 'chromium_headless_shell-*' -print -quit 2>/dev/null | grep -q .; then
    echo "启动失败：App 内置 Chromium 不完整。" >&2
    return 1
  fi
  if [[ ! -x "$project_root/frontend/node_modules/.bin/vite" ]]; then
    echo "启动失败：App 内置前端依赖不完整。" >&2
    return 1
  fi

  export PATH="$runtime_root/node/bin:/usr/bin:/bin:/usr/sbin:/sbin"
  export PEAP_PYTHON="$python_bin"
  export PYTHONPATH="$project_root"
  export PYTHONNOUSERSITE=1
  # Keep the signed app bundle immutable.  Python otherwise creates
  # __pycache__ files next to bundled modules when diagnostics import them.
  export PYTHONDONTWRITEBYTECODE=1
  # npm is only used to start the bundled Vite process; do not perform update,
  # audit, or funding network checks on an offline/air-gapped workstation.
  export NPM_CONFIG_UPDATE_NOTIFIER=false
  export NPM_CONFIG_AUDIT=false
  export NPM_CONFIG_FUND=false
  export PEAP_WORKSPACE_ROOT="$workspace_root"
  export PEAP_PLAYWRIGHT_BROWSERS_PATH="$browser_cache_dir"
  export PLAYWRIGHT_BROWSERS_PATH="$browser_cache_dir"
  # The browser is shipped inside the app bundle.  Runtime code must not try
  # to run ``playwright install`` against the (often signed/read-only) bundle.
  export PEAP_BUNDLED_RUNTIME_READ_ONLY=1

  if ! mkdir -p "$workspace_root"; then
    echo "启动失败：无法创建工作区 ${workspace_root}。" >&2
    return 1
  fi
  if ! node_major="$(node -p 'Number(process.versions.node.split(".")[0])' 2>/dev/null)" ||
     ! [[ "$node_major" =~ ^[0-9]+$ ]] || (( node_major < 18 )); then
    echo "启动失败：App 内置 Node.js 无法运行。" >&2
    return 1
  fi
  if ! "$python_bin" -c 'import bs4, certifi, chardet, openpyxl, pandas, playwright, yaml' >/dev/null 2>&1; then
    echo "启动失败：App 内置 Python 依赖不完整。" >&2
    return 1
  fi
  if ! "$python_bin" -c '
import os
from playwright.sync_api import sync_playwright
with sync_playwright() as playwright:
    path = playwright.chromium.executable_path
raise SystemExit(0 if os.path.isfile(path) else 1)
' >/dev/null 2>&1; then
    echo "启动失败：App 内置 Chromium 无法识别。" >&2
    return 1
  fi

  echo "正在初始化 PEAP 工作区..."
  if ! (cd "$project_root" && "$python_bin" -c '
from desktop_backend.app_config import AppConfig
from peap.streaming_store import StreamingStore
config = AppConfig.from_env()
StreamingStore(config.STREAMING_DB_PATH, auto_migrate=True)
print(f"工作区：{config.APP_HOME}")
print(f"数据库：{config.STREAMING_DB_PATH}")
'); then
    echo "启动失败：PEAP 工作区初始化失败。" >&2
    return 1
  fi

  echo "离线运行环境已就绪。"
}
