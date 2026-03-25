# Desktop Ingest Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix mapping refresh semantics, add real manual-import parsing, and collapse download output into direct canonical saves without a second archive copy.

**Architecture:** Keep the desktop SQLite/streaming task model, but move file-path ownership down into the download/import boundary and move record refresh ownership up into service-managed jobs. Manual import reuses CLI file discovery and parser dispatch while landing in the same streaming ingest/store path as downloader-produced pages.

**Tech Stack:** Python, SQLite, Electron, vanilla JS, unittest, Node syntax checks

---

### Task 1: Lock mapping-refresh behavior with failing tests

**Files:**
- Modify: `tests/test_app_service.py`
- Modify: `tests/test_streaming_store.py`

- [ ] **Step 1: Write the failing service test for mapping save triggering a background refresh job**
- [ ] **Step 2: Write the failing store/service test for affected records beyond the selected row being reprocessed**
- [ ] **Step 3: Run targeted tests and verify red**

Run: `python3 -m unittest tests.test_app_service tests.test_streaming_store -v`

Expected: FAIL because mapping save still only refreshes explicit `record_id`s.

### Task 2: Lock manual-import behavior with failing tests

**Files:**
- Modify: `tests/test_app_service.py`
- Modify: `tests/test_streaming_ingest.py`

- [ ] **Step 1: Write the failing service test for starting a manual-import job from a directory**
- [ ] **Step 2: Write the failing ingest/service test for recursive HTML/HTM/MHTML discovery**
- [ ] **Step 3: Run targeted tests and verify red**

Run: `python3 -m unittest tests.test_app_service tests.test_streaming_ingest -v`

Expected: FAIL because no manual-import API/job exists.

### Task 3: Lock direct-save and small-window defaults with failing tests

**Files:**
- Modify: `tests/test_download_oneclick.py`
- Modify: `tests/test_streaming_ingest.py`

- [ ] **Step 1: Write the failing orchestration test for default `max_pages=10` on small date windows**
- [ ] **Step 2: Write the failing ingest test showing canonical source files are no longer copied into archive**
- [ ] **Step 3: Run targeted tests and verify red**

Run: `python3 -m unittest tests.test_download_oneclick tests.test_streaming_ingest -v`

Expected: FAIL because the current flow still performs archive copying and leaves `max_pages` unset.

### Task 4: Implement mapping-refresh jobs

**Files:**
- Modify: `desktop_backend/app_service.py`
- Modify: `desktop_backend/app_backend.py`
- Modify: `peap/streaming_store.py`
- Modify: `peap/streaming_models.py`

- [ ] **Step 1: Add service logic to find affected latest records for a mapping rule**
- [ ] **Step 2: Add a background `mapping_refresh` job runner that re-ingests affected records**
- [ ] **Step 3: Expose job metadata/events so the UI can show refresh progress**
- [ ] **Step 4: Run targeted tests**

Run: `python3 -m unittest tests.test_app_service tests.test_streaming_store -v`

Expected: PASS

### Task 5: Implement manual-import parsing jobs

**Files:**
- Modify: `desktop_backend/app_service.py`
- Modify: `desktop_backend/app_backend.py`
- Modify: `peap/streaming_daily_pipeline.py`
- Modify: `peap/streaming_ingest.py`

- [ ] **Step 1: Add recursive file discovery for HTML/HTM/MHTML imports**
- [ ] **Step 2: Add a `manual_import` background job that ingests discovered files**
- [ ] **Step 3: Keep source-file canonicalization inside the import boundary**
- [ ] **Step 4: Run targeted tests**

Run: `python3 -m unittest tests.test_app_service tests.test_streaming_ingest -v`

Expected: PASS

### Task 6: Collapse archive copy into direct canonical saves

**Files:**
- Modify: `peap/streaming_ingest.py`
- Modify: `peap/download_oneclick.py`
- Modify: `peap/download_runner.py`
- Modify: `peap/downloaders/cbex_physical.py`
- Modify: `peap/downloaders/cquae.py`
- Modify: `peap/downloaders/sse_physical.py`
- Modify: `peap/downloaders/tpre.py`

- [ ] **Step 1: Move canonical output-path resolution into download/import save boundaries**
- [ ] **Step 2: Stop copying downloaded files inside `StreamingIngestRunner`**
- [ ] **Step 3: Set small-window default `max_pages=10` when the user did not override**
- [ ] **Step 4: Run targeted tests**

Run: `python3 -m unittest tests.test_download_oneclick tests.test_streaming_ingest -v`

Expected: PASS

### Task 7: Wire desktop UI to the new jobs

**Files:**
- Modify: `desktop_app/index.html`
- Modify: `desktop_app/renderer.js`

- [ ] **Step 1: Add a manual-import action that launches the new backend job**
- [ ] **Step 2: Show mapping-refresh and manual-import jobs in the task sidebar**
- [ ] **Step 3: Remove browser-side single-record reprocess assumptions from mapping save flows**
- [ ] **Step 4: Run frontend syntax checks**

Run: `node --check desktop_app/main.js`

Run: `node --check desktop_app/preload.js`

Run: `node --check desktop_app/renderer.js`

Expected: PASS

### Task 8: Final regression sweep and handoff notes

**Files:**
- Modify: `README.md`
- Modify: `todo.md`

- [ ] **Step 1: Run the focused Python regression suite**
- [ ] **Step 2: Run compile checks**
- [ ] **Step 3: Run Node syntax/tests**
- [ ] **Step 4: Update docs for mapping refresh, manual import, and direct-save semantics**

Run: `python3 -m unittest tests.test_app_service tests.test_streaming_store tests.test_streaming_ingest tests.test_download_oneclick tests.test_streaming_daily_pipeline tests.test_streaming_export tests.test_runtime_dependencies -v`

Run: `python3 -m compileall desktop_backend peap`

Run: `node --check desktop_app/main.js`

Run: `node --check desktop_app/preload.js`

Run: `node --check desktop_app/renderer.js`

Run: `node --test desktop_app/backend_launch.test.js`

Expected: PASS
