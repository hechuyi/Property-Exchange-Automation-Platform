# Desktop Hardening And Modularization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the desktop app's local trust boundary, turn records into a real paginated view, and split the renderer into maintainable modules without changing the existing product information architecture.

**Architecture:** First add a process-generated desktop API token that flows from Electron main process to the local backend and renderer, then enforce it at the HTTP layer. Next extend the records API with page-based pagination and total counts so the UI stops presenting silently truncated data. Finally keep the same HTML layout but decompose the renderer into focused browser modules for state, API access, records, and polling.

**Tech Stack:** Python, Electron, vanilla JS modules, unittest, Node test runner

---

### Task 1: Lock the local backend trust boundary with failing tests

**Files:**
- Modify: `tests/test_app_backend.py`
- Modify: `desktop_app/backend_ready.test.js`

- [ ] **Step 1: Write the failing backend test for rejecting requests without the desktop token**
- [ ] **Step 2: Write the failing backend test for allowing requests with the desktop token**
- [ ] **Step 3: Write the failing Node test for readiness probes carrying the token header**
- [ ] **Step 4: Run the focused tests and verify red**

Run: `.venv-desktop/bin/python -m unittest tests.test_app_backend -v`

Run: `node --test desktop_app/backend_ready.test.js`

Expected: FAIL because the backend currently trusts any local request and the readiness probe does not send a token.

### Task 2: Implement main-process token propagation and backend enforcement

**Files:**
- Modify: `desktop_backend/app_backend.py`
- Modify: `desktop_app/main.js`
- Modify: `desktop_app/preload.js`
- Modify: `desktop_app/backend_ready.js`

- [ ] **Step 1: Generate a random desktop API token in Electron main process**
- [ ] **Step 2: Pass the token into the backend launch environment and expose it through preload**
- [ ] **Step 3: Require the token for HTTP API requests while preserving startup readiness**
- [ ] **Step 4: Re-run the focused tests and verify green**

Run: `.venv-desktop/bin/python -m unittest tests.test_app_backend -v`

Run: `node --test desktop_app/backend_ready.test.js`

Expected: PASS

### Task 3: Lock records pagination semantics with failing tests

**Files:**
- Modify: `tests/test_app_service.py`

- [ ] **Step 1: Write the failing service test for returning total count, page, and has_more**
- [ ] **Step 2: Write the failing service test for slicing rows by page and page_size**
- [ ] **Step 3: Run the focused service tests and verify red**

Run: `.venv-desktop/bin/python -m unittest tests.test_app_service -v`

Expected: FAIL because records currently only honor a hard limit and do not expose pagination metadata.

### Task 4: Implement records pagination end-to-end

**Files:**
- Modify: `desktop_backend/app_backend.py`
- Modify: `desktop_backend/app_service.py`
- Modify: `desktop_app/index.html`
- Modify: `desktop_app/styles.css`
- Modify: `desktop_app/renderer.js`

- [ ] **Step 1: Add `page` and `page_size` request parameters to the backend records endpoint**
- [ ] **Step 2: Return `total_count`, `page`, `page_size`, `page_count`, and `has_more` from the service**
- [ ] **Step 3: Add records pagination controls and summary text to the UI**
- [ ] **Step 4: Re-run the focused service tests and front-end syntax checks**

Run: `.venv-desktop/bin/python -m unittest tests.test_app_service -v`

Run: `node --check desktop_app/renderer.js`

Expected: PASS

### Task 5: Lock renderer modular boundaries with failing module tests

**Files:**
- Create: `desktop_app/renderer/api.test.js`
- Create: `desktop_app/renderer/records.test.js`

- [ ] **Step 1: Write the failing API helper test for injecting the desktop token header**
- [ ] **Step 2: Write the failing records helper test for computing pagination summary text**
- [ ] **Step 3: Run the focused Node tests and verify red**

Run: `node --test desktop_app/renderer/api.test.js desktop_app/renderer/records.test.js`

Expected: FAIL because the modules do not exist yet.

### Task 6: Split renderer responsibilities without changing product layout

**Files:**
- Create: `desktop_app/renderer/api.js`
- Create: `desktop_app/renderer/state.js`
- Create: `desktop_app/renderer/records.js`
- Create: `desktop_app/renderer/polling.js`
- Modify: `desktop_app/index.html`
- Modify: `desktop_app/renderer.js`

- [ ] **Step 1: Extract API access and backend config bootstrap into a dedicated module**
- [ ] **Step 2: Extract shared renderer state into a dedicated module**
- [ ] **Step 3: Extract records rendering and pagination helpers into a dedicated module**
- [ ] **Step 4: Extract polling scheduling into a dedicated module and keep `renderer.js` as the entrypoint**
- [ ] **Step 5: Re-run Node module tests and front-end syntax checks**

Run: `node --test desktop_app/renderer/api.test.js desktop_app/renderer/records.test.js`

Run: `node --check desktop_app/renderer.js`

Expected: PASS

### Task 7: Full verification and product sanity review

**Files:**
- Modify: `todo.md`

- [ ] **Step 1: Run the targeted Python regression suite**
- [ ] **Step 2: Run the targeted Node regression suite**
- [ ] **Step 3: Run the full Python regression sweep**
- [ ] **Step 4: Update `todo.md` with the new hardening and modularization notes**

Run: `.venv-desktop/bin/python -m unittest tests.test_app_backend tests.test_app_service tests.test_app_backend_entry -q`

Run: `node --test desktop_app/backend_launch.test.js desktop_app/backend_ready.test.js desktop_app/renderer/api.test.js desktop_app/renderer/records.test.js`

Run: `.venv-desktop/bin/python -m unittest discover -s tests -q`

Expected: PASS
