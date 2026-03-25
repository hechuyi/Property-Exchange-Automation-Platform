#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_VERSION_FILE="$ROOT_DIR/.python-version"
VENV_DIR="$ROOT_DIR/.venv-desktop"
REQUIREMENTS_FILE="$ROOT_DIR/desktop_backend/requirements.lock.txt"
WORKSPACE_ROOT="${PEAP_WORKSPACE_ROOT:-${PEAP_APP_HOME:-${PEAP_DOCUMENTS_HOME:-$HOME/Documents/PEAP}}}"
PLAYWRIGHT_CACHE_DIR="${PEAP_PLAYWRIGHT_BROWSERS_PATH:-$WORKSPACE_ROOT/cache/ms-playwright}"

if [[ ! -f "$PYTHON_VERSION_FILE" ]]; then
  echo "Missing .python-version"
  exit 1
fi

TARGET_PYTHON_VERSION="$(tr -d '[:space:]' < "$PYTHON_VERSION_FILE")"
if [[ -z "$TARGET_PYTHON_VERSION" ]]; then
  echo ".python-version is empty"
  exit 1
fi

if command -v pyenv >/dev/null 2>&1; then
  export PYENV_VERSION="$TARGET_PYTHON_VERSION"
  if ! pyenv versions --bare | grep -Fxq "$TARGET_PYTHON_VERSION"; then
    echo "pyenv version $TARGET_PYTHON_VERSION is not installed."
    echo "Please run: pyenv install $TARGET_PYTHON_VERSION"
    exit 1
  fi
  PYTHON_BIN="$(pyenv which python)"
else
  echo "pyenv is not installed."
  echo "Please run: brew install pyenv"
  exit 1
fi

"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info[:2] != (3, 11):
    raise SystemExit(f"Expected Python 3.11.x, got {sys.version}")
PY

"$PYTHON_BIN" -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r "$REQUIREMENTS_FILE"
mkdir -p "$PLAYWRIGHT_CACHE_DIR"
PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_CACHE_DIR" PEAP_PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_CACHE_DIR" "$VENV_DIR/bin/python" -m playwright install chromium

echo "Desktop backend environment ready:"
echo "  python: $VENV_DIR/bin/python"
echo "  browser cache: $PLAYWRIGHT_CACHE_DIR"
