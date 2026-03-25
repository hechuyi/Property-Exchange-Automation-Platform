# Mapping Refresh And Homepage Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add conflict-aware mapping saves with explicit overwrite semantics, provide one-click reprocessing for all pending mappings from the mappings page, and make the homepage first screen expose the core download/export/manual-import actions without scrolling.

**Architecture:** First lock the new overwrite and preview semantics at the postprocess and service layers with failing tests. Then extend the desktop backend with preview and batch-refresh endpoints and wire the mappings UI to use a preflight-confirm-save flow. Finally rearrange the homepage action cards so `一键执行 / 导出 Excel / 手动导入解析` remain visible above the fold while batch reprocessing stays in the mappings page.

**Tech Stack:** Python, Electron, vanilla JS, HTML, CSS, unittest, Node test runner

---

### Task 1: Lock overwrite-aware mapping behavior with failing tests

**Files:**
- Modify: `tests/test_streaming_postprocess.py`
- Modify: `tests/test_app_service.py`

- [ ] **Step 1: Write the failing postprocess test showing a `group -> source_type` rule can overwrite an existing type when overwrite mode is enabled**
- [ ] **Step 2: Write the failing service test for mapping preview returning `create`, `update`, and `overwrite` modes**
- [ ] **Step 3: Write the failing service test for batch reprocessing all current `pending_mapping` records**
- [ ] **Step 4: Run the focused tests and verify red**

Run: `.venv-desktop/bin/python -m unittest tests.test_streaming_postprocess tests.test_app_service -v`

Expected: FAIL because the current mapping logic is fill-only, there is no preview endpoint behavior, and there is no batch pending refresh launcher.

### Task 2: Implement overwrite-capable mapping semantics and preview analysis

**Files:**
- Modify: `peap/streaming_postprocess.py`
- Modify: `desktop_backend/app_service.py`
- Modify: `peap/streaming_store.py`

- [ ] **Step 1: Add overwrite-aware mapping application support to record postprocess helpers**
- [ ] **Step 2: Add service helpers that preview mapping saves, detect conflicting existing rules, and count affected records**
- [ ] **Step 3: Add a batch launcher for reprocessing all current `pending_mapping` records**
- [ ] **Step 4: Run the focused Python tests and verify green**

Run: `.venv-desktop/bin/python -m unittest tests.test_streaming_postprocess tests.test_app_service -v`

Expected: PASS

### Task 3: Expose preview and batch-refresh APIs with failing handler tests

**Files:**
- Modify: `tests/test_app_backend.py`
- Modify: `desktop_backend/app_backend.py`

- [ ] **Step 1: Write the failing backend test for `POST /api/mappings/preview`**
- [ ] **Step 2: Write the failing backend test for `POST /api/mappings/reprocess-pending`**
- [ ] **Step 3: Implement both endpoints in the desktop backend handler**
- [ ] **Step 4: Run the focused backend tests**

Run: `.venv-desktop/bin/python -m unittest tests.test_app_backend -v`

Expected: PASS

### Task 4: Lock mappings UI flow and homepage layout with failing Node tests

**Files:**
- Modify: `desktop_app/layout_contract.test.js`
- Create: `desktop_app/renderer/mappings.test.js`

- [ ] **Step 1: Write the failing layout test showing homepage first-screen includes `一键执行 / 导出 Excel / 手动导入解析`**
- [ ] **Step 2: Write the failing mappings test showing save flow performs preview before final save**
- [ ] **Step 3: Run the focused Node tests and verify red**

Run: `node --test desktop_app/layout_contract.test.js desktop_app/renderer/mappings.test.js`

Expected: FAIL because the homepage still stacks actions vertically and the mappings flow has no preview/confirm step.

### Task 5: Implement mappings UI preview flow and batch refresh action

**Files:**
- Create: `desktop_app/renderer/mappings.mjs`
- Modify: `desktop_app/index.html`
- Modify: `desktop_app/renderer.js`
- Modify: `desktop_app/styles.css`

- [ ] **Step 1: Extract mappings-specific preview/save helpers into a dedicated module**
- [ ] **Step 2: Add a conflict confirmation dialog and batch pending refresh button to the mappings panel**
- [ ] **Step 3: Wire the renderer to preview before save and to start batch pending refresh jobs**
- [ ] **Step 4: Run the focused Node tests and syntax checks**

Run: `node --test desktop_app/layout_contract.test.js desktop_app/renderer/mappings.test.js`

Run: `node --check desktop_app/renderer.js`

Run: `node --check desktop_app/renderer/mappings.mjs`

Expected: PASS

### Task 6: Rebuild homepage first-screen action hierarchy

**Files:**
- Modify: `desktop_app/index.html`
- Modify: `desktop_app/styles.css`
- Modify: `desktop_app/renderer.js`

- [ ] **Step 1: Rebuild the homepage primary action row around `一键执行 / 导出 Excel / 手动导入解析`**
- [ ] **Step 2: Keep batch reprocessing accessible only from the mappings page while preserving a clear pending-mapping shortcut**
- [ ] **Step 3: Keep desktop navigation unchanged while making the first screen usable on common laptop heights**
- [ ] **Step 4: Re-run the homepage layout tests**

Run: `node --test desktop_app/layout_contract.test.js`

Expected: PASS

### Task 7: Full verification and handoff update

**Files:**
- Modify: `todo.md`

- [ ] **Step 1: Run the desktop Node regression suite**
- [ ] **Step 2: Run the full Python regression suite**
- [ ] **Step 3: Update handoff notes with the new mapping governance and homepage action model**

Run: `cd desktop_app && npm test`

Run: `.venv-desktop/bin/python -m unittest discover -s tests -q`

Expected: PASS
