# Semantic Invariant Screening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace page-by-page product sweeps with invariant-driven screening that can repeatedly verify task semantics, scope semantics, and object identity across store -> service -> API -> frontend, and close the release-critical trust gaps already exposed in the real-operation report.

**Architecture:** Keep the report anchored on three main lines: task semantics, scope semantics, and object identity. Each screening wave targets one invariant with one minimal sample and prefers real `AppService + StreamingStore + current frontend pure logic`; introduce HTTP or browser replay only when the invariant itself lives at that layer. Convert stable findings into focused regression coverage so later screening can rerun contracts instead of rediscovering symptoms.

**Tech Stack:** Python, unittest, SQLite-backed `StreamingStore`, desktop backend HTTP handler, vanilla JS renderer modules, Node test runner, Markdown reports

**Execution Note:** The run commands below assume a project Python interpreter that can import the repo. If `.venv-desktop/bin/python` is absent in the current workspace, use the active interpreter or venv that resolves the same imports. In sandboxed Codex runs, HTTP handler tests that bind `127.0.0.1` may be blocked by loopback restrictions; record that as an execution constraint instead of misclassifying it as a product finding.

**Progress Rule:** Any agent taking over this plan MUST update this document at the end of every task round. That includes read-only investigation rounds, partial audits, blocked work, and implementation rounds. Update both the execution snapshot below and the relevant task section before handing off.

**Execution Snapshot (2026-03-25):**

| Task | Checked | Fixed | Current status |
| --- | --- | --- | --- |
| Task 1 | Yes | Yes | Completed. The invariant charter was merged into the existing methodology/report docs instead of creating a parallel report, and discoverability anchors were verified. |
| Task 2 | Yes (read-only) | No | Task-semantics line audited. `5.1`, `5.15`, `5.17`, `5.21`, `5.32`, `5.37`, `5.41`, `5.42` all still appear open in current code; no direct service/API/frontend contract tests were added yet. |
| Task 3 | Yes (read-only) | No | Scope-semantics line audited. `5.18`, `5.30`, `7.49`, `7.53`, `11.17`, `5.7`, `5.11`, `5.23`, `5.35` all still appear open; export date scope mismatch should be treated as part of `5.30`, not a new line. |
| Task 4 | Yes (read-only) | No | Object-identity line audited. `5.36`, `5.38`, `5.39`, `5.40`, `5.43` all still appear open; failed-object boundary still lacks direct regression coverage. |
| Task 5 | Partial | No | A minimal handoff smoke test was executed on `/api/jobs/:id/events`. `5.23` and `5.35` still appear open (`get_job().events` capped at 100, `get_job_events()` / `GET /api/jobs/:id/events` capped at 200, missing job still collapses to `200 + []`); a direct `python3 -m unittest tests.test_app_backend -v` run also confirmed the documented sandbox loopback bind restriction. The broader fallback/cap wave is not yet complete. |
| Task 6 | No | No | Not started. |

---

### Task 1: Freeze the invariant charter and report routing table

**Files:**
- Create: `docs/superpowers/specs/2026-03-25-semantic-invariant-screening-design.md`
- Modify: `docs/project_audit_methodology_2026-03-23.md`
- Modify: `docs/real_operation_test_report_2026-03-23.md`

**Current audit status (2026-03-25):** Completed and reviewed. This task was executed by merging the new invariant framing into the existing docs/report set, not by starting a new parallel report.

- [x] **Step 1: Add the new screening charter to the spec and methodology docs**
- [x] **Step 2: Insert a short routing table near the top of the real-operation report that maps existing high-risk findings onto the three main lines and six invariant classes**
- [x] **Step 3: Add the “only open new 5.x when all three conditions hold” rule and the release-gate completion criteria to the report methodology section**
- [x] **Step 4: Verify the new anchors and routing labels are discoverable**

Run: `rg -n "任务语义主线|范围语义主线|对象身份主线|只在三条件同时满足时新开 5.x|完成标准" docs/superpowers/specs/2026-03-25-semantic-invariant-screening-design.md docs/project_audit_methodology_2026-03-23.md docs/real_operation_test_report_2026-03-23.md`

Expected: matches appear in all three documents so later waves can link findings back to a single invariant vocabulary.

### Task 2: Lock the task-semantics screening wave

**Files:**
- Modify: `tests/test_app_service.py`
- Modify: `tests/test_app_backend.py`
- Modify: `desktop_app/renderer.js`
- Create: `desktop_app/renderer/tasks.mjs`
- Create: `desktop_app/renderer/tasks.test.js`
- Modify: `docs/real_operation_test_report_2026-03-23.md`

**Current audit status (2026-03-25):** Read-only investigation completed; implementation not started. Confirmed still-open issue group: `5.1`, `5.15`, `5.17`, `5.21`, `5.32`, `5.37`, `5.41`, `5.42`.

- [ ] **Step 1: Write failing service tests for the highest-risk task invariants: task creation must precede success return, all-failed manual import cannot resolve as `success_with_warnings`, zero-fix `mapping_refresh` cannot masquerade as successful repair**
- [ ] **Step 2: Write failing service tests for terminal projection semantics so `failed` and `interrupted` preserve one consistent end-state interpretation in `latest_progress`**
- [ ] **Step 3: Extract pure renderer helpers for task titles / hints / terminal copy into `desktop_app/renderer/tasks.mjs`, then write failing Node tests covering `export_excel`, `manual_import`, `mapping_refresh`, `failed`, and `interrupted` copy**
- [ ] **Step 4: Add HTTP-level tests only for invariants that actually live at the API layer, such as fake success or wrong status-code semantics; keep the rest at service level**
- [ ] **Step 5: Run the focused Python and Node suites and capture the exact minimal repros into the report sections for `5.1`, `5.15`, `5.32`, `5.37`, and `5.41`, opening only `7.x / 11.x` boundaries unless a genuinely new task-semantic crack appears**

Run: `.venv-desktop/bin/python -m unittest tests.test_app_service tests.test_app_backend -v`

Run: `node --test desktop_app/renderer/tasks.test.js`

Expected: the new task-semantic checks fail before fixes and pass only when store, service, API, and renderer all describe task existence and terminal states with the same meaning.

### Task 3: Lock the scope-semantics screening wave

**Files:**
- Modify: `tests/test_app_service.py`
- Modify: `desktop_app/renderer/records.mjs`
- Modify: `desktop_app/renderer/records.test.js`
- Modify: `desktop_app/renderer.js`
- Create: `desktop_app/renderer/exports.mjs`
- Create: `desktop_app/renderer/exports.test.js`
- Modify: `docs/real_operation_test_report_2026-03-23.md`

**Current audit status (2026-03-25):** Read-only investigation completed; implementation not started. Confirmed still-open issue group: `5.18`, `5.30`, `7.49`, `7.53`, `11.17`, `5.7`, `5.11`, `5.23`, `5.35`. The export-date-window mismatch should be merged into `5.30`, not split into a new line.

- [ ] **Step 1: Write failing Node tests that prove export payload construction must inherit the current record-view scope instead of silently widening to date-only export**
- [ ] **Step 2: Extract pure frontend helpers for export request construction and empty-export blocker text so scope semantics can be checked without browser replay**
- [ ] **Step 3: Write failing service tests that compare `list_records()` and `run_export()` on the same minimal samples: one `ready`, one `pending_mapping`, one cross-type same-date pair, and one keyword-narrowed pair**
- [ ] **Step 4: Extend record summary tests so current-view counts, total counts, blocker counts, and empty-state explanations all come from the same filter scope**
- [ ] **Step 5: Run the focused suites and fold the results into `5.30`, `7.45`, `7.49`, `7.53`, and `11.17`, resisting the temptation to split the same scope crack into fresh `5.x` items**

Run: `.venv-desktop/bin/python -m unittest tests.test_app_service -v`

Run: `node --test desktop_app/renderer/records.test.js desktop_app/renderer/exports.test.js`

Expected: FAIL before scope propagation is aligned; PASS only when record-table scope, export scope, and empty-state explanation scope are identical for the same sample.

### Task 4: Lock the object-identity screening wave

**Files:**
- Modify: `tests/test_app_service.py`
- Modify: `tests/test_streaming_store.py`
- Modify: `peap/streaming_store.py`
- Modify: `desktop_backend/app_service.py`
- Modify: `docs/real_operation_test_report_2026-03-23.md`

**Current audit status (2026-03-25):** Read-only investigation completed; implementation not started. Confirmed still-open issue group: `5.36`, `5.38`, `5.39`, `5.40`, `5.43`. Direct regression tests for failed-object identity have not been added yet.

- [ ] **Step 1: Write failing service tests for the core identity invariant: a read path must not rewrite the identity anchor of a `parse_failed` object**
- [ ] **Step 2: Write failing service tests proving `reprocess_record()` must keep targeting the original failed object even after overview / list reads**
- [ ] **Step 3: Write failing store-level tests that track candidate tokens, `source_file`, `archive_path`, and repeated manual imports for a single failed HTML through repair, reprocess, and re-import paths**
- [ ] **Step 4: Implement only the minimum store / service changes needed to preserve one stable identity anchor across records, events, and recovery paths**
- [ ] **Step 5: Run the focused suites and update `5.36`, `5.38`, `5.40`, and `5.43`, adding new `7.x / 11.x` edges only when the finding is still the same identity crack viewed from another path**

Run: `.venv-desktop/bin/python -m unittest tests.test_app_service tests.test_streaming_store -v`

Expected: FAIL while read-side repair still rewrites identity or recovery inputs; PASS only when the same failed object remains traceable and reusable across read, repair, retry, and reprocess flows.

### Task 5: Close the cross-cutting fallback and cap invariants

**Files:**
- Modify: `tests/test_app_service.py`
- Modify: `tests/test_streaming_store.py`
- Modify: `desktop_app/renderer/records.test.js`
- Modify: `docs/real_operation_test_report_2026-03-23.md`

**Current audit status (2026-03-25):** Partially executed. A minimal handoff smoke test was completed for `/api/jobs/:id/events`, confirming that `get_job().events` is still hard-capped at 100, `get_job_events()` / `GET /api/jobs/:id/events` is still hard-capped at 200, and nonexistent tasks still collapse to `200 + []`; these results should remain folded into `5.23` and `5.35`, not split into a new line. A direct `python3 -m unittest tests.test_app_backend -v` run in the current Codex sandbox also hit the documented `127.0.0.1` bind restriction (`PermissionError` in `ThreadingHTTPServer`), so that environment constraint is now explicitly validated rather than hypothetical. The rest of the fallback/cap wave has not been independently rerun yet.

- [ ] **Step 1: Add focused tests for hidden limits and truncation semantics around pending mappings, batch refresh, recent jobs, and job-event retrieval so every cap is either user-visible or explicitly surfaced; include the current `get_job().events = 100` and `get_job_events()` / `GET /api/jobs/:id/events = 200` split as a fixed regression target**
- [ ] **Step 2: Add focused tests for default values and fallback projections, especially homepage defaults, export `latest_progress`, and any “smart” fallback that can silently change business meaning**
- [ ] **Step 3: Keep each repro on one invariant plus one minimal sample, and classify the result as a new `5.x` only when it clears the three-condition threshold from the spec**
- [ ] **Step 4: Re-run the focused suites and update only the user-visible impact text in the report; do not log “design elegance” or “test debt” as findings**

Run: `.venv-desktop/bin/python -m unittest tests.test_app_service tests.test_streaming_store -v`

Run: `node --test desktop_app/renderer/records.test.js`

Expected: PASS once defaults, fallbacks, and hard caps are either aligned with the business contract or explicitly exposed to the user.

### Task 6: Re-run the release gate and tighten the report closeout

**Files:**
- Modify: `docs/real_operation_test_report_2026-03-23.md`
- Modify: `todo.md`

**Current audit status (2026-03-25):** Not started.

- [ ] **Step 1: Re-run the task, scope, and identity suites together and confirm the regression surface covers all three release-gate conditions**
- [ ] **Step 2: Re-read the report and ensure every newly added section states a stable repro, a code cause, a main-line classification, and a user-visible consequence**
- [ ] **Step 3: Reduce `todo.md` to the true release blockers that remain against the three completion criteria; drop page-level noise that no longer blocks product trust**
- [ ] **Step 4: Save a short execution note in the report indicating which waves are closed, which are still open, and why**

Run: `.venv-desktop/bin/python -m unittest tests.test_app_service tests.test_app_backend tests.test_streaming_store -v`

Run: `node --test desktop_app/layout_contract.test.js desktop_app/renderer/records.test.js desktop_app/renderer/tasks.test.js desktop_app/renderer/exports.test.js`

Expected: PASS on reproducible screening coverage, with any still-open blockers explicitly mapped to task semantics, scope semantics, or object identity rather than scattered by page or module.
