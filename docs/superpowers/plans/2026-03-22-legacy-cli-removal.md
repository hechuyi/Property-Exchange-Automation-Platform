# Legacy CLI Removal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the legacy CLI wrapper surface, keep the desktop product path intact, and enforce backend task exclusivity on the server side.

**Architecture:** Delete only wrapper entrypoints and their repo-level contract surface, not the engine modules still used by the desktop workflow. Add server-side guards in `desktop_backend` so the single supported product path has explicit runtime invariants instead of relying on renderer state.

**Tech Stack:** Python 3.11, Electron, Node test runner, `unittest`, SQLite-backed desktop service

---

### Task 1: Lock Backend Job Start Semantics

**Files:**
- Modify: `tests/test_app_service.py`
- Modify: `desktop_backend/app_service.py`

- [ ] **Step 1: Write the failing test**

Add tests asserting that a running mutating job blocks starting another mutating job.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-desktop/bin/python -m unittest tests.test_app_service.AppServiceTest.test_launch_one_click_rejects_when_mutating_job_running -q`

- [ ] **Step 3: Write minimal implementation**

Add a server-side guard in `AppService` and use it from job-launch methods.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv-desktop/bin/python -m unittest tests.test_app_service.AppServiceTest.test_launch_one_click_rejects_when_mutating_job_running -q`

- [ ] **Step 5: Re-run related backend tests**

Run: `.venv-desktop/bin/python -m unittest tests.test_app_service tests.test_app_backend -q`

### Task 2: Remove Legacy CLI Wrapper Surface

**Files:**
- Delete: `bin/daily_pipeline.py`
- Delete: `bin/download.py`
- Delete: `bin/download_oneclick.py`
- Delete: `bin/parse.py`
- Delete: `bin/parser_regression.py`
- Delete: `bin/peap.py`
- Delete: `bin/public_resource_deals.py`
- Modify: `tests/test_naming_cleanup.py`
- Modify: `tests/test_cli_config_injection.py`
- Modify: `README.md`
- Modify: `docs/submission_guide.md`
- Modify: `peap_postprocess/ppe_business_user_guide.md`
- Modify: `peap_postprocess/postprocess_engine_plan.md`

- [ ] **Step 1: Write the failing test**

Adjust repository tests so they encode the new contract: the removed wrappers should no longer be expected.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-desktop/bin/python -m unittest tests.test_naming_cleanup -q`

- [ ] **Step 3: Write minimal implementation**

Delete wrapper files and update docs/tests to the new single-product-path contract.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv-desktop/bin/python -m unittest tests.test_naming_cleanup -q`

- [ ] **Step 5: Re-run targeted repository tests**

Run: `.venv-desktop/bin/python -m unittest tests.test_app_service tests.test_naming_cleanup -q`

### Task 3: Final Verification

**Files:**
- Modify: none expected unless verification exposes a regression

- [ ] **Step 1: Run desktop Node tests**

Run: `npm test`

- [ ] **Step 2: Run targeted Python tests**

Run: `.venv-desktop/bin/python -m unittest tests.test_app_service tests.test_app_backend tests.test_runtime_dependencies tests.test_streaming_daily_pipeline tests.test_streaming_store -q`

- [ ] **Step 3: Run a broader Python regression sweep**

Run: `.venv-desktop/bin/python -m unittest discover -s tests -q`

- [ ] **Step 4: Fix any regressions exposed by deletion boundary changes**

Keep fixes scoped to the desktop-supported product path or to repo references that still mention removed wrappers.
