# Desktop App Storage Layout

The desktop product now defaults to a single workspace root so operators do not need to reason about separate app-data, archive, export, cache, or browser-runtime directories.

## Default Layout

The default workspace root is:

- `PEAP_WORKSPACE_ROOT`, otherwise
- `PEAP_APP_HOME`, otherwise
- `PEAP_DOCUMENTS_HOME`, otherwise
- the platform documents directory under `PEAP`

Typical defaults:

- macOS: `~/Documents/PEAP`
- Windows: `%USERPROFILE%\\Documents\\PEAP`

Everything below is stored under that same root:

- Database: `<workspace_root>/data/streaming_ingest.sqlite3`
- Auto-saved web archives: `<workspace_root>/submission/`
- Manual import staging: `<workspace_root>/data/raw/manual/`
- Logs: `<workspace_root>/logs/`
- Cache root: `<workspace_root>/cache/`
- Download chunk cache: `<workspace_root>/cache/download_chunks/`
- Playwright browser cache: `<workspace_root>/cache/ms-playwright/`
- Excel exports: `<workspace_root>/exports/`

Notes:

- Desktop downloader jobs now write directly into the canonical archive tree under `<workspace_root>/submission/`.
- `PEAP_AUTO_HTML_ROOT` remains available as a compatibility override, but the product-facing desktop flow no longer depends on a separate raw auto-download tree.
- Manual imports may originate outside the workspace, but imported files are canonicalized into the workspace before the database points at them.

## Startup Migration

When no per-path override is set, desktop startup will merge old data into the unified workspace from:

- legacy platform app-data roots such as `~/Library/Application Support/PEAP` or `%LOCALAPPDATA%\\PEAP`
- older document roots such as `~/Documents/PEAP/submission` and `~/Documents/PEAP/exports`
- legacy repo-local browser cache at `<project_root>/.cache/ms-playwright`

This migration is additive and non-destructive: existing target files are kept, and only missing files are moved into the workspace.

## Configurable Overrides

The product now treats the workspace root as the main knob. Per-path overrides still exist for debugging and migration, but they should be considered advanced escape hatches rather than normal operator configuration.

Supported overrides:

- `PEAP_WORKSPACE_ROOT`
- `PEAP_APP_HOME`
- `PEAP_DOCUMENTS_HOME`
- `PEAP_DATA_ROOT`
- `PEAP_CACHE_DIR`
- `PEAP_LOG_DIR`
- `PEAP_MANUAL_HTML_ROOT`
- `PEAP_AUTO_HTML_ROOT`
- `PEAP_ARCHIVE_ROOT`
- `PEAP_EXPORT_ROOT`
- `PEAP_STREAMING_DB_PATH`
- `PEAP_DOWNLOAD_CHUNK_STATE_DIR`
- `PEAP_PLAYWRIGHT_BROWSERS_PATH`

In development, `scripts/bootstrap_desktop_env.sh` now installs Playwright Chromium into the same workspace browser cache used by the desktop runtime, so “installed in bootstrap but missing in app” no longer comes from a default path mismatch.
