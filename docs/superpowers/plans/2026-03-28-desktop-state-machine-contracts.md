# Desktop State Machine Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Formalize frontend-consumable public state machines for records, jobs, phases/stages, and mapping work items, then converge the desktop backend so `/api/overview`, `/api/jobs`, `/api/jobs/{id}`, `/api/jobs/{id}/events`, `/api/records`, and `/api/mappings` expose stable enums, required fields, and failure/blocker codes without frontend-side inference.

**Architecture:** Keep the current store schema and raw ingest semantics as the internal source of raw events, but add a single backend state-contract module that projects raw store/pipeline data into public record/job/progress/event/work-item contracts. Roll the change out additively: new canonical fields are introduced alongside legacy fields first, then all service and HTTP responses are routed through shared projection helpers, and cross-job failure summaries are normalized onto one stable shape.

**Tech Stack:** Python, SQLite, desktop backend, unittest, Markdown docs

---

### Task 1: Freeze the public state contract in docs and pure tests

**Files:**
- Create: `docs/desktop_state_contract.md`
- Create: `tests/test_state_contract.py`
- Modify: `tests/test_progress_contract.py`

- [ ] **Step 1: Write the contract doc section that separates raw internal states from public frontend states**
- [ ] **Step 2: Write failing pure tests for canonical public enums covering `record_state`, `job_status`, `phase_code`, `stage_code`, `work_item_state`, `failure_code`, and `blocking_reason_code`**
- [ ] **Step 3: Write failing pure tests that lock `conflict` as an internal-only raw record state which must project to `record_state=ready` plus `warning_codes=[\"archive_name_conflict\"]`**
- [ ] **Step 4: Write failing pure tests that mark `parsed`, `postprocessed`, and `refresh_history` as reserved stage codes rather than stable public event stages**
- [ ] **Step 5: Run the focused tests and verify red**

Run: `python3 -m unittest tests.test_state_contract tests.test_progress_contract -v`

Expected: FAIL because the canonical state-contract module and the new projections do not exist yet.

### Task 2: Implement the centralized public state-contract module

**Files:**
- Create: `desktop_backend/state_contract.py`
- Modify: `desktop_backend/progress_contract.py`

- [ ] **Step 1: Implement canonical enum sets, labels, and taxonomy constants for public record/job/phase/stage/work-item contracts**
- [ ] **Step 2: Implement pure projection helpers for public record state, mapping work-item state, job terminal summary, and job-event normalization**
- [ ] **Step 3: Add explicit helpers for `raw_record_state -> public_record_state`, including `conflict -> ready + archive_name_conflict warning`**
- [ ] **Step 4: Make `progress_contract.py` delegate terminal-status and progress-shape decisions to the new shared module instead of owning a separate partial contract**
- [ ] **Step 5: Re-run the focused contract tests and verify green**

Run: `python3 -m unittest tests.test_state_contract tests.test_progress_contract -v`

Expected: PASS

### Task 3: Converge `/api/records` and `/api/mappings` on public record and work-item states

**Files:**
- Modify: `desktop_backend/app_service.py`
- Modify: `tests/test_app_service.py`

- [ ] **Step 1: Write failing service tests that require record rows to expose canonical public fields such as `record_state`, `is_exportable`, `warning_codes`, `failure_code`, `failure_stage`, and `failure_message` alongside legacy `state/status_label/status_detail`**
- [ ] **Step 2: Write failing service tests that require mapping pending items to expose `work_item_state`, `primary_gap_code`, stable `gap_codes`, and stable `blocking_reason_code` without the frontend inspecting raw findings**
- [ ] **Step 3: Implement read-side record and mapping projections through the shared state-contract helpers without changing the underlying store schema**
- [ ] **Step 4: Ensure `recommended_rule` becomes required for `missing_group` and `missing_type`, and `candidate_resolutions` becomes required for `mapping_conflict`**
- [ ] **Step 5: Run the focused service tests and verify green**

Run: `python3 -m unittest tests.test_app_service -v`

Expected: PASS

### Task 4: Unify job progress outputs across `/api/overview`, `/api/jobs`, and `/api/jobs/{id}`

**Files:**
- Modify: `desktop_backend/app_service.py`
- Modify: `desktop_backend/app_backend.py`
- Modify: `tests/test_app_service.py`
- Modify: `tests/test_app_backend.py`

- [ ] **Step 1: Write failing service and backend tests that require `/api/jobs` and `/api/jobs/{id}` to expose the same normalized `progress` shape as the current overview progress contract**
- [ ] **Step 2: Write failing tests that require terminal jobs to carry structured failure fields and warning summaries instead of making the frontend infer terminal meaning from free-form summary payloads**
- [ ] **Step 3: Implement a shared `build_job_progress_snapshot()` path and route overview, job list, and job detail through it**
- [ ] **Step 4: Keep legacy top-level fields for compatibility, but make the new normalized `progress` object the canonical long-term surface**
- [ ] **Step 5: Run the focused service and backend tests and verify green**

Run: `python3 -m unittest tests.test_app_service tests.test_app_backend -v`

Expected: PASS

### Task 5: Normalize job events and unify failure summaries across `one_click`, `manual_import`, `mapping_refresh`, and `export_excel`

**Files:**
- Modify: `peap/streaming_daily_pipeline.py`
- Modify: `desktop_backend/app_service.py`
- Modify: `desktop_backend/http_contract.py`
- Modify: `tests/test_streaming_daily_pipeline.py`
- Modify: `tests/test_app_service.py`
- Modify: `tests/test_app_backend.py`

- [ ] **Step 1: Write failing tests that require every failed or interrupted job type to expose the same terminal summary shape: `failure_code`, `failure_stage`, and `failure_message`**
- [ ] **Step 2: Write failing tests that require `/api/jobs/{id}/events` to expose normalized `stage_code`, `stage_kind`, `event_outcome`, optional `record_state`, and normalized failure fields alongside legacy `stage/status/error_type/error_message`**
- [ ] **Step 3: Implement a shared failure-summary builder reused by streaming one-click jobs, manual import jobs, mapping refresh jobs, and export jobs**
- [ ] **Step 4: Implement event projection rules that stop overloading raw `status` as both stage outcome and record result**
- [ ] **Step 5: Re-run the focused event and failure tests and verify green**

Run: `python3 -m unittest tests.test_streaming_daily_pipeline tests.test_app_service tests.test_app_backend -v`

Expected: PASS

### Task 6: Lock backward compatibility, migration notes, and regression coverage

**Files:**
- Modify: `docs/desktop_state_contract.md`
- Modify: `tests/test_app_service.py`
- Modify: `tests/test_app_backend.py`
- Modify: `tests/test_streaming_store.py`

- [ ] **Step 1: Add migration notes documenting which legacy fields remain, which new canonical fields replace them, and which fields are display-only versus control-only versus debug-only**
- [ ] **Step 2: Write regression tests that ensure legacy fields still exist while the new canonical fields carry the formal state-machine contract**
- [ ] **Step 3: Write regression tests that ensure raw store states and event rows can still be read even after public contract projection becomes stricter**
- [ ] **Step 4: Run the targeted backend regression suite**
- [ ] **Step 5: Run the broader Python test suite before merge**

Run: `python3 -m unittest tests.test_state_contract tests.test_progress_contract tests.test_streaming_daily_pipeline tests.test_streaming_store tests.test_app_service tests.test_app_backend -v`

Run: `python3 -m unittest discover -s tests -q`

Expected: PASS

## Implementation Notes

- Prefer additive compatibility over schema churn: introduce new canonical fields first, then deprecate frontend branching on legacy free-text fields later.
- Do not treat `status_label`, `status_detail`, `failure_message`, `findings`, `payload`, or `latest_stage_summary` as control-plane inputs for UI branching.
- Keep the raw store contract intact unless a failing test proves the raw schema itself is blocking the public contract.
- Do not expose `parsed`, `postprocessed`, or `refresh_history` as stable frontend-visible stage codes until the pipeline actually emits and tests them.
- Keep all new logic in shared helpers; avoid duplicating `if/else` state derivation across `app_service.py`, `progress_contract.py`, and handler code.

## Verification Sequence

- First pass: `python3 -m unittest tests.test_state_contract tests.test_progress_contract -v`
- Second pass: `python3 -m unittest tests.test_app_service tests.test_app_backend tests.test_streaming_daily_pipeline -v`
- Third pass: `python3 -m unittest tests.test_streaming_store -v`
- Final pass: `python3 -m unittest discover -s tests -q`
