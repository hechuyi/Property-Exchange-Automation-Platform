# Non-Parser Runtime Boundary Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the non-parser boundary cleanups from the combined normalization plan so runtime contracts, shared source metadata, export compatibility, and legacy maintenance can be implemented independently of the upcoming parser-layer redesign.

**Architecture:** Move shared runtime contracts that should not belong to `desktop_backend` into `peap_core`, create one canonical source catalog for product/download/backend consumers, seal the downstream compat/export boundary without changing parser orchestration, and move store repair out of read endpoints into an explicit maintenance runner. This plan intentionally excludes parser routing, parser registry work, parser variants, and parse-cache invalidation so the parser-layer refactor can proceed under a separate design.

**Tech Stack:** Python 3.11, pytest, SQLite, existing `peap` / `peap_core` / `desktop_backend` packages, no new framework-level dependencies.

---

## Assumptions

- Parser extraction internals and parser orchestration remain unchanged in this plan.
- Existing SQLite data must remain readable throughout the transition.
- `desktop_backend` can keep thin compatibility wrappers temporarily, but core runtime modules should stop depending on it for shared contracts.
- Exported Excel rows must continue to satisfy the current writer schema, but unsanctioned raw parser fields must no longer pass through by accident.
- User-visible exchange labels and downloader choices should remain stable unless the current behavior is clearly a duplicate-table defect.

## Out Of Scope

- `peap/parsing.py` orchestration refactors
- `peap_parsers/*` parser-family changes
- parser registry / parser guard / parser enricher registration
- parse-cache signature redesign
- parser-template routing redesign for Beijing / Shanghai / public-resource pages

## Boundary Rules

- `peap_core` may not import `peap`, `desktop_backend`, or parser packages.
- `peap/streaming_store.py` and other core runtime modules may not import shared identity helpers from `desktop_backend`.
- Canonical source aliases and labels must come from one shared catalog, not duplicated dicts or tuples scattered across runtime consumers.
- Downloader implementation classes remain in `peap`; only immutable source metadata crosses into `peap_core`.
- Store read APIs (`overview` dependencies, `list_*`, `get_*`, `count_*`) must be side-effect free.
- Export compatibility must be produced by an explicit allowlisted projection, not by merging arbitrary raw payload fields into writer rows.

## File Structure

**Shared runtime contracts**
- Create: `peap_core/record_identity.py`
- Create: `peap_core/source_catalog.py`
- Modify: `peap/source_registry.py`
- Modify: `desktop_backend/record_identity.py`
- Modify: `peap/streaming_store.py`
- Modify: `tests/test_record_identity.py`
- Modify: `tests/test_source_registry.py`
- Modify: `tests/test_environment_tooling.py`

**Runtime consumer unification**
- Modify: `peap/product_profile.py`
- Modify: `peap/download_tasks.py`
- Modify: `peap/download_runner.py`
- Modify: `desktop_backend/app_service.py`
- Modify: `tests/test_product_profile.py`
- Modify: `tests/test_runner_request_adapters.py`
- Modify: `tests/test_download_runner.py`
- Modify: `tests/test_app_service.py`

**Compat/export boundary cleanup**
- Create: `peap/compat_payload.py`
- Modify: `peap/standard_model.py`
- Modify: `peap/output_mapping.py`
- Modify: `peap/checks.py`
- Modify: `peap/streaming_export.py`
- Create: `tests/test_compat_payload.py`
- Modify: `tests/test_streaming_export.py`

**Legacy maintenance isolation**
- Create: `peap/streaming_store_maintenance.py`
- Modify: `peap/streaming_daily_pipeline.py`
- Modify: `desktop_backend/app_service.py`
- Modify: `tests/test_streaming_store.py`
- Create: `tests/test_streaming_store_maintenance.py`
- Modify: `tests/test_streaming_daily_pipeline.py`
- Modify: `tests/test_app_service.py`

**Documentation and regression**
- Modify: `docs/development_plan.md`
- Modify: `docs/project_layout.md`
- Modify: `README.md` only if the active architecture description changes materially

### Task 1: Move Shared Record Identity Contracts Into `peap_core`

**Files:**
- Create: `peap_core/record_identity.py`
- Modify: `desktop_backend/record_identity.py`
- Modify: `peap/streaming_store.py`
- Modify: `tests/test_record_identity.py`
- Modify: `tests/test_environment_tooling.py`

- [ ] **Step 1: Write failing boundary tests for shared identity ownership**

Add coverage asserting:
- `peap/streaming_store.py` imports `FAILED_RECORD_STATES`, `build_identity_anchor`, and `build_source_identity_payload` from `peap_core.record_identity`
- `desktop_backend/record_identity.py` remains either a thin compatibility wrapper or a pure re-export layer
- record identity tests import the canonical contract from `peap_core`, not `desktop_backend`

- [ ] **Step 2: Run the shared-identity tests and confirm failure**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform
uv run pytest tests/test_record_identity.py tests/test_environment_tooling.py -q
```

Expected: failures indicating the shared identity contract still lives under `desktop_backend`.

- [ ] **Step 3: Implement the shared identity module and migrate core imports**

Create `peap_core/record_identity.py` with:
- failed-state constants
- source identity payload helpers
- identity-anchor builder
- reprocess evidence selection helpers only if they remain backend-agnostic

Then:
- update `peap/streaming_store.py` to import from `peap_core.record_identity`
- convert `desktop_backend/record_identity.py` into a thin compatibility wrapper for any backend-only imports that still exist

- [ ] **Step 4: Re-run the shared-identity tests**

Run the same command from Step 2.

Expected: tests pass and core runtime no longer depends on `desktop_backend` for identity logic.

- [ ] **Step 5: Commit**

```bash
git add peap_core/record_identity.py desktop_backend/record_identity.py peap/streaming_store.py tests/test_record_identity.py tests/test_environment_tooling.py
git commit -m "refactor: move shared record identity contracts into peap_core"
```

### Task 2: Create A Canonical Source Catalog For Runtime Consumers

**Files:**
- Create: `peap_core/source_catalog.py`
- Modify: `peap/source_registry.py`
- Modify: `peap/product_profile.py`
- Modify: `peap/download_tasks.py`
- Modify: `peap/download_runner.py`
- Modify: `desktop_backend/app_service.py`
- Modify: `tests/test_source_registry.py`
- Modify: `tests/test_product_profile.py`
- Modify: `tests/test_runner_request_adapters.py`
- Modify: `tests/test_download_runner.py`
- Modify: `tests/test_app_service.py`

- [ ] **Step 1: Write failing tests for catalog-derived runtime metadata**

Add or update tests asserting:
- `ProductProfile.source_ids` are derived from a shared catalog selection, not a duplicated hardcoded tuple
- downloader display names and exchange choices are built from canonical source metadata, while downloader classes remain owned by `peap/download_tasks.py`
- `task_progress_label()` uses the same canonical exchange labels as the backend
- backend exchange normalization resolves aliases/codes through the shared catalog instead of private `EXCHANGE_LABELS` / `EXCHANGE_CODES` tables
- `peap/source_registry.py` becomes a compatibility facade over the shared catalog rather than a mutable singleton used as the canonical source of truth

- [ ] **Step 2: Run the runtime-consumer tests and confirm failure**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform
uv run pytest tests/test_source_registry.py tests/test_product_profile.py tests/test_runner_request_adapters.py tests/test_download_runner.py tests/test_app_service.py -q
```

Expected: failures showing source metadata is still duplicated across product profile, downloader labels, and backend alias tables.

- [ ] **Step 3: Implement the shared source catalog and migrate consumers**

Create `peap_core/source_catalog.py` with immutable source descriptors and alias helpers covering:
- canonical source code
- canonical human-readable label
- accepted aliases / synonyms
- optional runtime flags needed by product/download/backend consumers

Then:
- update `peap/product_profile.py` to derive shipped source membership from the catalog
- update `peap/download_tasks.py` to build display names from catalog metadata while keeping downloader-class binding local
- update `peap/download_runner.py` to derive task-progress exchange labels from the same source metadata
- update `desktop_backend/app_service.py` to normalize exchange labels/codes through catalog lookups
- keep `peap/source_registry.py` only as a compatibility shim if existing imports still need it

- [ ] **Step 4: Re-run the runtime-consumer tests**

Run the same command from Step 2.

Expected: product profile, downloader task metadata, progress labels, and backend exchange normalization all resolve through one source catalog.

- [ ] **Step 5: Commit**

```bash
git add peap_core/source_catalog.py peap/source_registry.py peap/product_profile.py peap/download_tasks.py peap/download_runner.py desktop_backend/app_service.py tests/test_source_registry.py tests/test_product_profile.py tests/test_runner_request_adapters.py tests/test_download_runner.py tests/test_app_service.py
git commit -m "refactor: unify runtime source metadata on shared catalog"
```

### Task 3: Seal The Downstream Compat / Export Boundary

**Files:**
- Create: `peap/compat_payload.py`
- Modify: `peap/standard_model.py`
- Modify: `peap/output_mapping.py`
- Modify: `peap/checks.py`
- Modify: `peap/streaming_export.py`
- Create: `tests/test_compat_payload.py`
- Modify: `tests/test_streaming_export.py`

- [ ] **Step 1: Write failing tests for explicit export compatibility projection**

Add coverage asserting:
- export payload generation depends on an explicit compat projection plus mapped standard fields, not an unrestricted raw payload merge
- `record_to_export_payload()` does not copy arbitrary parser/postprocess extras into export rows just because they are present in stored payloads
- writer-contract validation no longer treats the whole legacy payload universe as an implicit free-pass for downstream columns
- public-resource exports still preserve the allowlisted compatibility fields they actually require

- [ ] **Step 2: Run compat/export tests and confirm failure**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform
uv run pytest tests/test_compat_payload.py tests/test_streaming_export.py -q
```

Expected: failures showing export rows still admit unrestricted raw-field passthrough.

- [ ] **Step 3: Implement an explicit downstream compat projection**

Create `peap/compat_payload.py` that:
- builds a bounded legacy-compatible payload from standard fields
- optionally accepts a small allowlisted extras set for downstream compatibility cases that are still required
- makes the allowed extras visible and reviewable in one place

Then update:
- `peap/standard_model.py` so legacy projection is explicit rather than raw-merge-driven
- `peap/output_mapping.py` so mapping starts from explicit compat payloads
- `peap/checks.py` so writer validation reasons about the bounded compat contract
- `peap/streaming_export.py` so export rows are assembled from explicit compat + mapped fields, not arbitrary merged raw payloads

- [ ] **Step 4: Re-run the compat/export tests**

Run the same command from Step 2.

Expected: export artifacts remain correct for supported columns, but non-contract raw fields no longer bleed into writer payloads.

- [ ] **Step 5: Commit**

```bash
git add peap/compat_payload.py peap/standard_model.py peap/output_mapping.py peap/checks.py peap/streaming_export.py tests/test_compat_payload.py tests/test_streaming_export.py
git commit -m "refactor: seal downstream compat and export payload boundary"
```

### Task 4: Move Legacy Store Repair Into An Explicit Maintenance Runner

**Files:**
- Create: `peap/streaming_store_maintenance.py`
- Modify: `peap/streaming_daily_pipeline.py`
- Modify: `desktop_backend/app_service.py`
- Modify: `tests/test_streaming_store.py`
- Create: `tests/test_streaming_store_maintenance.py`
- Modify: `tests/test_streaming_daily_pipeline.py`
- Modify: `tests/test_app_service.py`

- [ ] **Step 1: Rewrite tests around side-effect-free reads and explicit maintenance**

Add or update tests asserting:
- `overview()`, `list_records()`, `list_pending_mappings()`, `get_job()`, and related read flows do not mutate record state as a hidden side effect
- legacy skip-state normalization, listing-date normalization, and required-mapping normalization run through one explicit maintenance runner
- daily pipeline bootstrap and app startup call the maintenance runner from mutation-owning entrypoints
- archive repair remains a separate maintenance action and is not folded back into ordinary read paths

- [ ] **Step 2: Run maintenance-focused tests and confirm failure**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform
uv run pytest tests/test_streaming_store.py tests/test_streaming_store_maintenance.py tests/test_streaming_daily_pipeline.py tests/test_app_service.py -q
```

Expected: failures showing read APIs still trigger hidden normalization or that no explicit maintenance runner exists.

- [ ] **Step 3: Implement explicit store maintenance orchestration**

Create `peap/streaming_store_maintenance.py` to coordinate:
- legacy skip-state normalization
- listing-date normalization
- required-mapping-state normalization
- audit entry emission for maintenance actions

Then:
- remove `_normalize_legacy_views()` calls from read endpoints in `desktop_backend/app_service.py`
- invoke the maintenance runner from `AppService` startup/bootstrap points that already own mutation responsibility
- invoke the maintenance runner from `peap/streaming_daily_pipeline.py` before ingest work begins
- keep archive repair separate from the normalization runner

- [ ] **Step 4: Re-run the maintenance tests**

Run the same command from Step 2.

Expected: reads become side-effect free, normalization remains idempotent, and startup/bootstrap still repairs legacy state exactly once.

- [ ] **Step 5: Commit**

```bash
git add peap/streaming_store_maintenance.py peap/streaming_daily_pipeline.py desktop_backend/app_service.py tests/test_streaming_store.py tests/test_streaming_store_maintenance.py tests/test_streaming_daily_pipeline.py tests/test_app_service.py
git commit -m "refactor: isolate store maintenance from read paths"
```

### Task 5: Update Active Docs And Run Regression

**Files:**
- Modify: `docs/development_plan.md`
- Modify: `docs/project_layout.md`
- Modify: `README.md` only if architecture wording changed materially

- [ ] **Step 1: Update docs for the non-parser boundary changes**

Document:
- shared identity and source metadata contracts now live in `peap_core`
- runtime source metadata is centralized for product/download/backend consumers
- export compatibility is bounded by an explicit downstream compat projection
- store normalization is an explicit maintenance concern rather than a read-path side effect
- parser-layer redesign is intentionally out of scope for this document set and tracked separately

- [ ] **Step 2: Run the focused regression suite**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform
uv run pytest tests/test_record_identity.py tests/test_environment_tooling.py tests/test_source_registry.py tests/test_product_profile.py tests/test_runner_request_adapters.py tests/test_download_runner.py tests/test_compat_payload.py tests/test_streaming_export.py tests/test_streaming_store.py tests/test_streaming_store_maintenance.py tests/test_streaming_daily_pipeline.py tests/test_app_service.py -q
uv run ruff check peap peap_core desktop_backend tests
```

Expected: targeted regression tests pass and lint is clean without requiring parser-layer changes.

- [ ] **Step 3: Commit**

```bash
git add docs/development_plan.md docs/project_layout.md README.md
git commit -m "docs: align non-parser runtime boundary architecture"
```

## Execution Notes

- Do not pull parser routing or parse-cache work back into this plan for convenience; those concerns are intentionally deferred.
- Prefer temporary compatibility wrappers only where they reduce migration risk for one phase; remove them once imports have been migrated.
- Treat the source catalog as immutable metadata, not a new service locator. Downloader classes, parser classes, and backend formatting logic still belong to their owning layers.
- When rewriting maintenance tests, change the contract first: current tests that rely on `overview()` or other reads to repair state must be inverted before implementation changes begin.
- Keep the compat projection small and auditable. If a field is needed for exports, add it explicitly rather than reintroducing raw passthrough.
