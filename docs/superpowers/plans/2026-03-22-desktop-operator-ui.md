# Desktop Operator UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify desktop data into one workspace root, replace the old split-plan one-click flow with a single collect-then-execute pipeline, and rebuild the desktop UI around one date-range-driven action with reliable business-facing status.

**Architecture:** First lock the new workspace layout and one-click orchestration with failing tests. Then refactor backend config and service boundaries so the desktop app exposes one coherent workspace and one coherent task ledger. Finally reshape the Electron UI to consume the new view-model and remove the separate download flow and yesterday-backfill semantics.

**Tech Stack:** Python, SQLite, Electron, vanilla JS, HTML, CSS, unittest, Node test runner

---

### Task 1: Lock single-workspace behavior with failing tests

**Files:**
- Modify: `tests/test_app_config.py`
- Modify: `tests/test_app_service.py`

- [ ] **Step 1: Write the failing config test for a single default workspace root**
- [ ] **Step 2: Write the failing config test for legacy app-support migration into the workspace root**
- [ ] **Step 3: Write the failing service test showing one-click no longer carries `with_refresh`**
- [ ] **Step 4: Run targeted tests and verify red**

Run: `python3 -m unittest tests.test_app_config tests.test_app_service -v`

Expected: FAIL because config still splits data roots and service still exposes refresh semantics.

### Task 2: Refactor backend config into one workspace root

**Files:**
- Modify: `desktop_backend/app_config.py`
- Modify: `desktop_app/backend_launch.js`
- Modify: `desktop_app/backend_launch.test.js`
- Modify: `README.md`
- Modify: `docs/desktop_storage_layout.md`

- [ ] **Step 1: Replace app-home/documents split defaults with one workspace-root default**
- [ ] **Step 2: Add legacy directory merge migration into the workspace root**
- [ ] **Step 3: Align Electron backend launch and Playwright cache defaults to the same workspace root**
- [ ] **Step 4: Update storage documentation and launcher tests**
- [ ] **Step 5: Run targeted tests**

Run: `python3 -m unittest tests.test_app_config -v`

Run: `node --test desktop_app/backend_launch.test.js`

Expected: PASS

### Task 3: Replace split-plan one-click orchestration with collect-then-execute

**Files:**
- Modify: `peap/download_oneclick.py`
- Modify: `peap/download_runner.py`
- Modify: `tests/test_download_oneclick.py`
- Modify: `tests/test_streaming_daily_pipeline.py`

- [ ] **Step 1: Write the failing orchestration test for collect-first then execute-second**
- [ ] **Step 2: Write the failing pipeline test showing default date range is today-to-today**
- [ ] **Step 3: Implement task collection using downloader `list_only=True` and cached candidates**
- [ ] **Step 4: Execute downloads from prefetched candidates without refresh-backfill**
- [ ] **Step 5: Emit stable prepare/save phase payloads with task labels, counts, and percent**
- [ ] **Step 6: Run targeted tests**

Run: `python3 -m unittest tests.test_download_oneclick tests.test_streaming_daily_pipeline -v`

Expected: PASS

### Task 4: Simplify desktop service and task progress projection

**Files:**
- Modify: `desktop_backend/app_service.py`
- Modify: `desktop_backend/app_backend.py`
- Modify: `peap/streaming_daily_pipeline.py`
- Modify: `tests/test_app_service.py`

- [ ] **Step 1: Remove one-click refresh semantics and stop auto-export from one-click**
- [ ] **Step 2: Rebuild overview progress projection around the new task ledger payloads**
- [ ] **Step 3: Collapse settings around one workspace root and derived subpaths**
- [ ] **Step 4: Make manual export use user-facing rebuild semantics**
- [ ] **Step 5: Run targeted tests**

Run: `python3 -m unittest tests.test_app_service tests.test_streaming_daily_pipeline -v`

Expected: PASS

### Task 5: Rebuild the desktop UI around one action and stable state

**Files:**
- Modify: `desktop_app/index.html`
- Modify: `desktop_app/renderer.js`
- Modify: `desktop_app/styles.css`

- [ ] **Step 1: Remove the separate download card and yesterday-backfill controls**
- [ ] **Step 2: Add a compact workspace header and a single date-range one-click card**
- [ ] **Step 3: Render today-aware date hints and the new phase/count payloads**
- [ ] **Step 4: Keep mappings page stable while editing and preserve the saved-rules view**
- [ ] **Step 5: Make settings paths read as one workspace and derived subdirectories**
- [ ] **Step 6: Run frontend syntax checks**

Run: `node --check desktop_app/main.js`

Run: `node --check desktop_app/preload.js`

Run: `node --check desktop_app/renderer.js`

Expected: PASS

### Task 6: Final regression sweep and handoff notes

**Files:**
- Modify: `todo.md`

- [ ] **Step 1: Run the focused Python regression suite**
- [ ] **Step 2: Run the focused Node regression suite**
- [ ] **Step 3: Update `todo.md` with the new desktop architecture and remaining manual validation items**

Run: `python3 -m unittest tests.test_app_config tests.test_app_service tests.test_download_oneclick tests.test_streaming_daily_pipeline tests.test_streaming_store tests.test_streaming_export tests.test_runtime_dependencies -v`

Run: `python3 -m compileall desktop_backend peap`

Run: `node --check desktop_app/main.js`

Run: `node --check desktop_app/preload.js`

Run: `node --check desktop_app/renderer.js`

Run: `node --test desktop_app/backend_launch.test.js`

Expected: PASS
