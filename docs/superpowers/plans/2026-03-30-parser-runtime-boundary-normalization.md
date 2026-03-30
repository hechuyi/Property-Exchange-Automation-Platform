# Parser And Runtime Boundary Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace scattered parser/source/legacy special cases with explicit shared contracts, registered parser policies, and isolated maintenance flows so the project can absorb new exceptions without continuing to grow hidden coupling.

**Architecture:** Move pure shared metadata and identity contracts into `peap_core`, then make `peap_parsers` own a narrow registry for source detection, parser variant selection, parser guards, and post-parse enrichers. Keep `peap` orchestration thin, derive task/profile/UI exchange metadata from one shared source catalog, seal the standard-to-compat/export boundary, and move legacy store repair from read paths into explicit startup/maintenance execution.

**Tech Stack:** Python 3.11, pytest, SQLite, existing `peap` / `peap_core` / `peap_parsers` packages, no new framework-level dependencies.

---

## Assumptions

- Existing exchange-specific parser classes remain the field extraction unit; this plan changes composition and boundaries, not every parser's extraction internals.
- "Registered" here means Python-level registration through explicit contracts and ordered bindings, not pushing HTML heuristics into opaque config files.
- The parser registry must stay smaller in scope than PPE. It handles routing, guards, and enrichers only; it must not become a second general-purpose rule engine.
- Legacy data already in SQLite must remain readable. Normalization may be idempotent and explicit, but it must no longer be triggered by ordinary read endpoints.
- User-visible exchange names, job types, and export semantics should stay stable unless the current behavior is already an identified structural defect.

## Boundary Rules

- `peap_core` may not import `peap`, `peap_parsers`, or `desktop_backend`.
- `peap_parsers` may depend on `peap_core` contracts, but not on `desktop_backend` or downloader modules.
- `peap/parsing.py` becomes orchestration-only: no hardcoded source map, no direct fallback chain, no source-specific `if/else` beyond registry invocation.
- `desktop_backend` may consume shared contracts, but must not own contracts required by `peap`.
- Store read APIs (`list_*`, `get_*`) must be side-effect free. Any migration, normalization, or repair must run through an explicit maintenance entrypoint.
- The export writer contract may depend on explicit compat projections only, never on unrestricted raw parser passthrough.

## File Structure

**Shared source and identity contracts**
- Create: `peap_core/source_catalog.py`
- Create: `peap_core/record_identity.py`
- Modify: `peap/source_registry.py`
- Modify: `desktop_backend/record_identity.py`
- Modify: `peap/streaming_store.py`
- Modify: `tests/test_source_registry.py`
- Modify: `tests/test_record_identity.py`
- Modify: `tests/test_environment_tooling.py`

**Parser routing and parser-side exception registration**
- Create: `peap_parsers/parser_registry.py`
- Create: `peap_parsers/builtin_registry.py`
- Modify: `peap_parsers/__init__.py`
- Modify: `peap_parsers/utils.py`
- Modify: `peap_parsers/beijing.py`
- Modify: `peap_parsers/shanghai.py`
- Modify: `peap/parsing.py`
- Modify: `peap/parse_cache.py`
- Create: `tests/test_parser_registry.py`
- Modify: `tests/test_parsing_contract.py`
- Modify: `tests/test_parse_cache.py`

**Source/task/profile consumer unification**
- Modify: `peap/product_profile.py`
- Modify: `peap/download_tasks.py`
- Modify: `peap/download_runner.py`
- Modify: `desktop_backend/app_service.py`
- Modify: `tests/test_product_profile.py`
- Modify: `tests/test_runner_request_adapters.py`
- Modify: `tests/test_app_service.py`

**Compat/export boundary cleanup**
- Create: `peap/compat_payload.py`
- Modify: `peap/standard_model.py`
- Modify: `peap/output_mapping.py`
- Modify: `peap/checks.py`
- Modify: `peap/streaming_ingest.py`
- Modify: `peap/streaming_export.py`
- Create: `tests/test_compat_payload.py`
- Modify: `tests/test_streaming_export.py`
- Modify: `tests/test_parsing_contract.py`

**Legacy maintenance isolation**
- Create: `peap/streaming_store_maintenance.py`
- Modify: `peap/streaming_store.py`
- Modify: `peap/streaming_daily_pipeline.py`
- Modify: `desktop_backend/app_service.py`
- Create: `tests/test_streaming_store_maintenance.py`
- Modify: `tests/test_streaming_daily_pipeline.py`
- Modify: `tests/test_app_service.py`

**Documentation and final verification**
- Modify: `docs/parser_rule_risk_report.md`
- Modify: `docs/development_plan.md`
- Modify: `docs/project_layout.md`
- Modify: `README.md` only if the new shared-contract/module boundaries change the active architecture description

### Task 1: Extract Shared Source And Identity Contracts

**Files:**
- Create: `peap_core/source_catalog.py`
- Create: `peap_core/record_identity.py`
- Modify: `peap/source_registry.py`
- Modify: `desktop_backend/record_identity.py`
- Modify: `peap/streaming_store.py`
- Modify: `tests/test_source_registry.py`
- Modify: `tests/test_record_identity.py`
- Modify: `tests/test_environment_tooling.py`

- [ ] **Step 1: Write failing boundary tests for shared contracts**

Add tests that assert:
- `peap/streaming_store.py` imports record identity from `peap_core`, not `desktop_backend`
- source definitions are available from a shared catalog module, not a test-only registry singleton
- `desktop_backend/record_identity.py` is either a thin compatibility wrapper or no longer required by core runtime code

- [ ] **Step 2: Run the shared-contract test group and confirm failure**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform
uv run pytest tests/test_source_registry.py tests/test_record_identity.py tests/test_environment_tooling.py -q
```

Expected: failures mentioning the old `desktop_backend.record_identity` ownership and the absence of a shared source catalog contract.

- [ ] **Step 3: Implement pure shared contract modules**

Create `peap_core/source_catalog.py` for immutable source metadata and alias lookup, move pure record identity helpers into `peap_core/record_identity.py`, then update `peap/streaming_store.py` and any remaining core imports to consume those modules. Keep `desktop_backend/record_identity.py` only as a thin wrapper if a temporary compatibility layer is still needed.

- [ ] **Step 4: Re-run the shared-contract tests**

Run the same command from Step 2.

Expected: shared-contract tests pass and no core runtime module depends on `desktop_backend` for shared identity logic.

- [ ] **Step 5: Commit**

```bash
git add peap_core/source_catalog.py peap_core/record_identity.py peap/source_registry.py desktop_backend/record_identity.py peap/streaming_store.py tests/test_source_registry.py tests/test_record_identity.py tests/test_environment_tooling.py
git commit -m "refactor: extract shared source and identity contracts"
```

### Task 2: Replace Hardcoded Parser Dispatch With A Registered Parser Pipeline

**Files:**
- Create: `peap_parsers/parser_registry.py`
- Create: `peap_parsers/builtin_registry.py`
- Modify: `peap_parsers/__init__.py`
- Modify: `peap_parsers/utils.py`
- Modify: `peap_parsers/beijing.py`
- Modify: `peap_parsers/shanghai.py`
- Modify: `peap/parsing.py`
- Modify: `peap/parse_cache.py`
- Create: `tests/test_parser_registry.py`
- Modify: `tests/test_parsing_contract.py`
- Modify: `tests/test_parse_cache.py`

- [ ] **Step 1: Write failing tests for registry-driven parser composition**

Cover these behaviors:
- parser orchestration resolves source parsing through a registry instead of `PARSER_MAP`
- Beijing / Shanghai special-template routing is expressed as registered variants, not router-local `if/else` only
- parser guards handle CBEX OTC recoverability through registered policy hooks
- parser enrichers are registered in an ordered chain, and `public_resource` exclusion is policy-driven rather than a direct orchestrator branch
- parse-cache signatures change when files under `peap_parsers/` change

- [ ] **Step 2: Run parser contract tests to verify failure**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform
uv run pytest tests/test_parsing_contract.py tests/test_parse_cache.py tests/test_parser_registry.py -q
```

Expected: failures referencing `PARSER_MAP`, direct fallback patch points, or cache signatures that ignore parser-module edits.

- [ ] **Step 3: Implement a narrow parser registry**

Add a registry contract that owns:
- ordered source detectors
- per-source parser bindings
- optional parser variants selected by template predicates
- pre-parse guards and post-parse enrichers

Then update `peap/parsing.py` to delegate source resolution, parser selection, guards, and enrichers through that registry. Keep the registry intentionally narrow; do not import PPE concepts or generic rule planning into parser orchestration.

- [ ] **Step 4: Re-run parser and cache tests**

Run the same command from Step 2.

Expected: parser contract tests pass, cache signatures include parser-module changes, and parser composition is exercised through registry tests instead of `PARSER_MAP` patching.

- [ ] **Step 5: Commit**

```bash
git add peap_parsers/parser_registry.py peap_parsers/builtin_registry.py peap_parsers/__init__.py peap_parsers/utils.py peap_parsers/beijing.py peap_parsers/shanghai.py peap/parsing.py peap/parse_cache.py tests/test_parser_registry.py tests/test_parsing_contract.py tests/test_parse_cache.py
git commit -m "refactor: register parser routing and enrichers"
```

### Task 3: Make Tasks, Profiles, And UI Exchange Metadata Derive From One Source Catalog

**Files:**
- Modify: `peap/product_profile.py`
- Modify: `peap/download_tasks.py`
- Modify: `peap/download_runner.py`
- Modify: `desktop_backend/app_service.py`
- Modify: `tests/test_product_profile.py`
- Modify: `tests/test_runner_request_adapters.py`
- Modify: `tests/test_app_service.py`

- [ ] **Step 1: Write failing tests for source-catalog-derived consumers**

Add or update tests asserting:
- the shipped product profile derives `source_ids` from the shared source catalog rather than a duplicated tuple
- download task labels and exchange choices come from shared source metadata, while downloader-class binding remains local to `peap/download_tasks.py`
- backend exchange label/code normalization uses the same canonical aliases as the shared catalog instead of a hand-maintained duplicate table

- [ ] **Step 2: Run the source-consumer tests and confirm failure**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform
uv run pytest tests/test_product_profile.py tests/test_runner_request_adapters.py tests/test_app_service.py -q
```

Expected: failures showing duplicated exchange/profile metadata still lives in multiple modules.

- [ ] **Step 3: Refactor consumers to derive from shared source metadata**

Update:
- `peap/product_profile.py` to compute shipped source membership from the shared catalog
- `peap/download_tasks.py` to use shared source labels/ids while keeping downloader-class bindings local
- `desktop_backend/app_service.py` to resolve exchange labels/codes from shared aliases instead of a duplicated constant table

Do not move downloader implementation classes into `peap_core`; only shared metadata crosses that boundary.

- [ ] **Step 4: Re-run the source-consumer tests**

Run the same command from Step 2.

Expected: product profile, task registry, and backend metadata tests pass against one canonical source catalog.

- [ ] **Step 5: Commit**

```bash
git add peap/product_profile.py peap/download_tasks.py peap/download_runner.py desktop_backend/app_service.py tests/test_product_profile.py tests/test_runner_request_adapters.py tests/test_app_service.py
git commit -m "refactor: unify runtime consumers on shared source catalog"
```

### Task 4: Seal The Standard / Compat / Export Boundary

**Files:**
- Create: `peap/compat_payload.py`
- Modify: `peap/standard_model.py`
- Modify: `peap/output_mapping.py`
- Modify: `peap/checks.py`
- Modify: `peap/streaming_ingest.py`
- Modify: `peap/streaming_export.py`
- Create: `tests/test_compat_payload.py`
- Modify: `tests/test_streaming_export.py`
- Modify: `tests/test_parsing_contract.py`

- [ ] **Step 1: Write failing tests for explicit compat projection**

Cover these behaviors:
- export payload generation depends on explicit compat projection plus standard fields, not unrestricted raw payload passthrough
- writer contract validation no longer treats the full `LEGACY_PAYLOAD_KEYS` universe as a free compatibility escape hatch
- parser raw extras that are not part of the compat contract do not silently satisfy export schema requirements

- [ ] **Step 2: Run compat/export tests and confirm failure**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform
uv run pytest tests/test_compat_payload.py tests/test_streaming_export.py tests/test_parsing_contract.py -q
```

Expected: failures showing `include_raw=True` still leaks parser-side fields into export/writer compatibility.

- [ ] **Step 3: Implement an explicit compat projection layer**

Create `peap/compat_payload.py` to build a bounded legacy-compatible payload from standard fields and an allowlisted set of extras. Update `standard_model.py`, `output_mapping.py`, and `checks.py` so export code depends on that explicit contract. Keep any raw-payload merge only where a dedicated backward-compatibility path is still required, and make that path impossible to confuse with the export contract.

- [ ] **Step 4: Re-run compat/export tests**

Run the same command from Step 2.

Expected: export rows remain correct, but unsanctioned parser keys no longer bleed across the writer boundary.

- [ ] **Step 5: Commit**

```bash
git add peap/compat_payload.py peap/standard_model.py peap/output_mapping.py peap/checks.py peap/streaming_ingest.py peap/streaming_export.py tests/test_compat_payload.py tests/test_streaming_export.py tests/test_parsing_contract.py
git commit -m "refactor: seal compat and export payload boundary"
```

### Task 5: Move Legacy Store Normalization Into Explicit Maintenance Flow

**Files:**
- Create: `peap/streaming_store_maintenance.py`
- Modify: `peap/streaming_store.py`
- Modify: `peap/streaming_daily_pipeline.py`
- Modify: `desktop_backend/app_service.py`
- Create: `tests/test_streaming_store_maintenance.py`
- Modify: `tests/test_streaming_daily_pipeline.py`
- Modify: `tests/test_app_service.py`

- [ ] **Step 1: Write failing tests for side-effect-free reads and explicit maintenance**

Add tests asserting:
- `list_jobs()`, `get_job()`, `list_pending_mappings()`, and similar read endpoints do not mutate store state
- legacy normalization is triggered through an explicit maintenance runner during startup / pipeline bootstrap, not during ordinary read access
- archive repair and legacy-state normalization are separate maintenance actions with clear audit entries

- [ ] **Step 2: Run maintenance-oriented tests and confirm failure**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform
uv run pytest tests/test_streaming_store_maintenance.py tests/test_streaming_daily_pipeline.py tests/test_app_service.py -q
```

Expected: failures showing read APIs still trigger legacy normalization or that no explicit maintenance runner exists.

- [ ] **Step 3: Implement explicit store maintenance orchestration**

Create a maintenance module that coordinates:
- legacy skip-state normalization
- listing-date normalization
- required-mapping-state normalization
- optional archive repair

Then remove `_normalize_legacy_views()` from read paths and call the maintenance runner explicitly from startup/bootstrap flows that already own mutation responsibility.

- [ ] **Step 4: Re-run maintenance tests**

Run the same command from Step 2.

Expected: reads become side-effect free, maintenance remains idempotent, and startup flows still repair old state exactly once.

- [ ] **Step 5: Commit**

```bash
git add peap/streaming_store_maintenance.py peap/streaming_store.py peap/streaming_daily_pipeline.py desktop_backend/app_service.py tests/test_streaming_store_maintenance.py tests/test_streaming_daily_pipeline.py tests/test_app_service.py
git commit -m "refactor: isolate legacy store maintenance flows"
```

### Task 6: Run Full Regression And Update Active Architecture Docs

**Files:**
- Modify: `docs/parser_rule_risk_report.md`
- Modify: `docs/development_plan.md`
- Modify: `docs/project_layout.md`
- Modify: `README.md` only if architecture wording changed materially

- [ ] **Step 1: Update active docs to reflect the new architecture**

Document:
- shared contracts now live under `peap_core`
- parser exceptions are registered through parser registry bindings, not scattered orchestrator branches
- legacy normalization is an explicit maintenance concern, not a read-path side effect
- export compatibility is bounded by a defined compat projection

- [ ] **Step 2: Run the focused regression suite**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform
uv run pytest tests/test_source_registry.py tests/test_record_identity.py tests/test_environment_tooling.py tests/test_parser_registry.py tests/test_parsing_contract.py tests/test_parse_cache.py tests/test_product_profile.py tests/test_runner_request_adapters.py tests/test_compat_payload.py tests/test_streaming_export.py tests/test_streaming_store_maintenance.py tests/test_streaming_daily_pipeline.py tests/test_app_service.py -q
uv run ruff check peap peap_core peap_parsers desktop_backend tests
```

Expected: all targeted regression tests pass and lint is clean.

- [ ] **Step 3: Commit**

```bash
git add docs/parser_rule_risk_report.md docs/development_plan.md docs/project_layout.md README.md
git commit -m "docs: align architecture docs with registry-based parser runtime"
```

## Execution Notes

- Prefer keeping compatibility wrappers only when they reduce transition risk for one phase; remove them as soon as all internal imports have been migrated.
- Avoid creating one giant "metadata god object". Shared source catalog should hold immutable source metadata only; downloader classes, parser classes, and UI formatting logic stay in their owning layers.
- Treat parse-cache signature coverage as a release gate item for parser work. If a parser file can change behavior, it must participate in signature invalidation.
- Do not merge Task 4 before Task 2 is stable. Parser registration must settle before tightening payload boundaries, or the plan will blur parsing defects with compat-cleanup defects.
- Do not merge Task 5 behind read endpoints "for convenience". That would recreate the exact hidden coupling this plan is trying to remove.
