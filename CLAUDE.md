# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development commands

### Repo root Python environment

- `uv sync` — materialize the repo-managed `.venv`
- `uv sync --locked` — CI/strict sync
- `uv lock --check` — validate `uv.lock`
- `uv run ruff check peap peap_core peap_parsers peap_postprocess tests bin config.py scripts/init_runtime_config.py scripts/prepare_submission.py` — Python lint command used in CI
- `uv run python -m pytest tests` — run the full Python test suite
- `uv run pytest tests/test_app_service.py -q` — run a single Python test file
- `uv run pytest tests/test_app_service.py -q -k settings` — run a filtered Python test selection
- `uv run python -m desktop_backend.app_backend --host 127.0.0.1 --port 42679` — run the local backend directly
- `uv run python scripts/check_release_gate.py` — run the current mainline development gate checks
- `uv run python scripts/prepare_submission.py` — build submission artifacts under the workspace output tree

### Desktop app (`desktop_app/`)

- `npm install` — install Electron/renderer dependencies
- `npm run bootstrap:backend` — bootstrap the repo-root Python runtime from the desktop app
- `npm start` — build the renderer and launch Electron
- `npm run build` — production renderer build
- `npm test` — run the desktop app test suite
- `node --test ./main.test.js` — run a single desktop app Node test file

### Local desktop startup sequence

From the repo root:

```bash
uv sync
bash scripts/bootstrap_desktop_env.sh
cd desktop_app
npm install
npm start
```

`desktop_app/` is not a standalone dev project. Electron resolves the backend from the repo-root `.venv`, launches `python -m desktop_backend.app_backend`, and uses the repo root as backend working directory.

## Architecture overview

### Product boundary

The supported operator-facing product on `main` is the desktop app:

- `desktop_app/` — Electron shell and React/Vite renderer
- `desktop_backend/` — local Python HTTP API used by the desktop app

The engine packages below are still part of the runtime path, but they are no longer separate product entrypoints:

- `peap_core/` — shared runtime contracts and canonical source metadata
- `peap/` — downloader, ingest/store, export, and pipeline orchestration
- `peap_parsers/` — exchange-specific parsers selected by the engine
- `peap_postprocess/` — postprocess rules and runner

Legacy source-tree CLI wrappers are not the main product path anymore.

### Runtime shape

This repository is one root Python project plus one Electron app:

- the repo root `pyproject.toml` manages the Python environment with `uv`
- `desktop_app/package.json` owns the Node/Electron toolchain
- `desktop_backend` is part of the root Python package set, not a separate Python project

### Desktop app flow

The desktop product runs as:

1. Electron main process starts in `desktop_app/main.js`
2. Electron resolves Python launch settings in `desktop_app/backend_launch.js`
3. Electron starts `desktop_backend.app_backend` from the repo-root `.venv`
4. The renderer talks to the backend over local HTTP, not over heavy IPC
5. The backend coordinates jobs, settings, records, mappings, exports, and runtime dependency checks
6. The backend persists product state in the SQLite `StreamingStore`

Important boundary: preload exposes only a small bridge; product data and commands flow primarily through the backend API.

### Engine flow

The Python runtime is layered:

- `peap/download_cli.py` and `peap/download_runner.py` orchestrate download tasks
- `peap/parser_runner.py` and `peap/parsing.py` select parsers from `peap_parsers/`
- `peap/streaming_ingest.py` writes records into the streaming store
- `peap/streaming_export.py` exports ready records
- `peap/daily_pipeline.py` and `peap/streaming_daily_pipeline.py` compose the end-to-end runs used by backend jobs and batch workflows

### Shared runtime contracts

`peap_core/` holds the cross-cutting contracts that both the backend and engine rely on:

- `source_catalog.py` is the canonical source metadata catalog
- `record_identity.py` centralizes failed-record identity and evidence-path logic
- `runtime.py` contains shared runtime/path helpers

For runtime-boundary work, prefer shared contracts in `peap_core/` over duplicating source or record metadata inside `desktop_backend/` or `peap/`.

### Store and export boundaries

A few repo-specific boundaries matter when modifying data flow:

- `peap/streaming_store.py` is the central persistence layer for jobs, job events, records, revisions, exports, mappings, settings, and audit data
- `peap/compat_payload.py` defines the bounded downstream export payload instead of passing arbitrary raw parser/postprocess fields through
- `peap/streaming_store_maintenance.py` is the explicit place for legacy normalization work; ordinary read paths in `desktop_backend.app_service` are intended to stay side-effect free

### Workspace model

The desktop product uses a single workspace root. By default it is `~/Documents/PEAP`, overridable via:

- `PEAP_APP_HOME`
- `PEAP_WORKSPACE_ROOT`
- `PEAP_DOCUMENTS_HOME`

Common paths under the workspace root:

- `data/streaming_ingest.sqlite3` — SQLite store
- `submission/` — archived pages
- `data/raw/manual/` — manual import staging
- `exports/` — export root
- `logs/` — desktop app and backend logs
- `cache/ms-playwright/` — browser runtime cache

## Tests and validation

- Root `tests/` covers Python engine/backend behavior
- `desktop_app` has its own Node-based tests for Electron main/preload/renderer flows
- CI currently checks `uv lock --check`, `uv sync --locked`, Ruff, and `uv run python -m pytest tests`
- The active mainline gate also expects `cd desktop_app && npm test` and `cd desktop_app && npm run build`
- `uv run python scripts/check_release_gate.py` is the current mainline gate entrypoint; its automated baseline is `uv lock --check`, `uv run pytest tests/test_environment_tooling.py tests/test_release_gate.py -q`, `uv run python -m desktop_backend.app_backend --help`, `cd desktop_app && npm test`, and `cd desktop_app && npm run build`

## Runtime boundary notes

- The parser subsystem is layered as snapshot capture → decode/classify → page parse → assemble → normalize → policy → sink projection
- `peap/parsing.py` is a compatibility facade; the active parser runtime is composed from `peap/parser_subsystem.py`, `peap_parsers/parser_registry.py`, and `peap_parsers/family_runtime.py`
- Replay/reprocess paths reuse stored snapshot metadata such as `source_url` and snapshot ids/digests when present, rather than rebuilding context from raw parser payloads only
- Parse cache invalidation is runtime-version aware across decoder/classifier/family/variant/assembler/normalizer/policy stages

## Important docs

Prefer these docs for current product context:

- `README.md`
- `docs/project_layout.md`
- `docs/desktop_electron_smoke_report_2026-03-28.md`
- `docs/desktop_product_runbook_2026-03-26.md`
- `docs/release_gate.md`
- `docs/development_plan.md`
- `docs/submission_guide.md`

`docs/superpowers/` contains AI planning and handoff material, not release documentation.

## Repository-specific notes

- The active workflow is `uv`-based; treat old `requirements*.txt` / lock references as legacy unless current code or docs require them.
- First-time bootstrap needs network access for `uv`, `npm`, and Playwright Chromium installation.
- The current renderer path is the Vite/React app under `desktop_app/src/`, even though older renderer files still exist in `desktop_app/`.
