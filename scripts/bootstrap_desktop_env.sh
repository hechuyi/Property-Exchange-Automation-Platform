#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
PYTHON_VERSION_FILE="$ROOT_DIR/.python-version"
VENV_DIR="$ROOT_DIR/.venv"
FRONTEND_DIR="$ROOT_DIR/frontend"
WORKSPACE_ROOT="${PEAP_WORKSPACE_ROOT:-${PEAP_APP_HOME:-${PEAP_DOCUMENTS_HOME:-$HOME/Documents/PEAP}}}"
PLAYWRIGHT_CACHE_DIR="${PEAP_PLAYWRIGHT_BROWSERS_PATH:-$WORKSPACE_ROOT/cache/ms-playwright}"
MIN_NODE_MAJOR=18

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

if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  echo "Node.js ${MIN_NODE_MAJOR}+ and npm are required."
  echo "Install the current Node.js LTS release, then run this script again."
  exit 1
fi

NODE_MAJOR="$(node -p 'Number(process.versions.node.split(".")[0])' 2>/dev/null || true)"
if [[ ! "$NODE_MAJOR" =~ ^[0-9]+$ ]] || (( NODE_MAJOR < MIN_NODE_MAJOR )); then
  echo "Node.js ${MIN_NODE_MAJOR}+ is required; found $(node --version 2>/dev/null || echo unknown)."
  exit 1
fi

if [[ ! -f "$FRONTEND_DIR/package.json" || ! -f "$FRONTEND_DIR/package-lock.json" ]]; then
  echo "Frontend package metadata is incomplete: $FRONTEND_DIR"
  exit 1
fi

uv python install "$TARGET_PYTHON_VERSION"
uv sync --locked
(cd "$FRONTEND_DIR" && npm ci)
mkdir -p "$PLAYWRIGHT_CACHE_DIR"
PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_CACHE_DIR" \
PEAP_PLAYWRIGHT_BROWSERS_PATH="$PLAYWRIGHT_CACHE_DIR" \
uv run python -m playwright install chromium

echo "Desktop environment ready:"
echo "  python: $VENV_DIR/bin/python"
echo "  node: $(node --version)"
echo "  frontend: $FRONTEND_DIR/node_modules"
echo "  browser cache: $PLAYWRIGHT_CACHE_DIR"
