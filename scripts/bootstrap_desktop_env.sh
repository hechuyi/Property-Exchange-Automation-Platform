#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_VERSION_FILE="$ROOT_DIR/.python-version"
VENV_DIR="$ROOT_DIR/.venv"
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

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is not installed."
  echo "Install it first: https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
fi

uv python install "$TARGET_PYTHON_VERSION"
uv sync --locked
mkdir -p "$PLAYWRIGHT_CACHE_DIR"
PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_CACHE_DIR" \
PEAP_PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_CACHE_DIR" \
uv run python -m playwright install chromium

echo "Desktop backend environment ready:"
echo "  python: $VENV_DIR/bin/python"
echo "  browser cache: $PLAYWRIGHT_CACHE_DIR"
