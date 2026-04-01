# Property Exchange Automation Platform

## Current Status

This repository now tracks the desktop product mainline rather than the legacy source-tree CLI. The active product path is:

- `desktop_app/` — Electron shell and renderer
- `desktop_backend/` — local backend API for the desktop shell
- `peap_core/` — shared runtime contracts and canonical source metadata
- `peap/`, `peap_parsers/`, `peap_postprocess/` — engine modules still used at runtime

The current target is a **pure development mainline**: finish the frontend/backend product path first, keep runtime semantics explicit, and keep a single repo-root development runtime. The latest strict real Electron smoke is recorded in `docs/desktop_electron_smoke_report_2026-03-28.md`, and the current mainline gate definition lives in `docs/release_gate.md`.

## Development Prerequisites

Local development assumes all of the following are available on the host machine:

- `uv`
- Node.js and `npm`
- network access on first bootstrap so `uv`, `npm`, and Playwright Chromium downloads can complete

The Python environment is managed from the repo root. The desktop app is not a standalone dev project when copied out of this repository.

## Local Desktop Development

```bash
uv sync
bash scripts/bootstrap_desktop_env.sh

cd desktop_app
npm install
npm start
```

What this does:

- `uv sync` materializes the repo-managed `.venv`
- `bootstrap_desktop_env.sh` installs the pinned Python toolchain and Playwright Chromium into the workspace cache
- `npm start` rebuilds the Vite renderer and then launches Electron

Important coupling for development mode:

- Electron resolves the backend from the repo root `.venv`
- the backend entry is `python -m desktop_backend.app_backend`
- the backend working directory defaults to the repo root
- if the repo root environment is missing, startup fails explicitly before the main window is shown

For backend-only local debugging:

```bash
uv run python -m desktop_backend.app_backend --host 127.0.0.1 --port 42679
```

## Workspace Layout

By default the product uses `~/Documents/PEAP/` as its workspace root, unless overridden by `PEAP_APP_HOME`, `PEAP_WORKSPACE_ROOT`, or `PEAP_DOCUMENTS_HOME`.

- SQLite store: `<workspace_root>/data/streaming_ingest.sqlite3`
- Archived pages: `<workspace_root>/submission/`
- Manual import staging: `<workspace_root>/data/raw/manual/`
- Export root: `<workspace_root>/exports/`
- Logs: `<workspace_root>/logs/`
- Browser cache: `<workspace_root>/cache/ms-playwright/`

More storage details are documented in `docs/desktop_storage_layout.md`.

## Runtime Boundary Notes

- shared failed-record identity and source metadata now live in `peap_core/`
- downstream export compatibility is bounded by `peap/compat_payload.py`, rather than raw payload passthrough
- legacy listing-date / pending-mapping normalization runs through `peap/streaming_store_maintenance.py`
- ordinary read paths in `desktop_backend.app_service` are intentionally side-effect free
- parser-layer redesign remains a separate track and is not part of this runtime-boundary slice

## Product Boundary

The desktop product is the only supported operator-facing workflow on `main`.

- Supported product entry: `desktop_app/` + `desktop_backend/`
- Runtime engine modules retained for the desktop path: `peap_core/`, `peap/`, `peap_parsers/`, `peap_postprocess/`
- Legacy source-tree CLI wrappers are no longer the main product path
- Repository scope is limited to the repo-root development runtime and product source tree
- `docs/superpowers/` contains AI planning and handoff material, not release documentation

## Key Docs

- `docs/release_gate.md`
- `docs/desktop_electron_smoke_report_2026-03-28.md`
- `docs/desktop_product_runbook_2026-03-26.md`
- `docs/project_layout.md`
- `docs/development_plan.md`
