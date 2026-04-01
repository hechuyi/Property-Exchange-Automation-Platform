# Full Parser Subsystem Rearchitecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current single-chain parser flow with a full parser subsystem that separates snapshot capture, document decoding, source classification, page parsing, cross-page assembly, normalization, policy execution, and sink projections.

**Architecture:** Introduce strong shared contracts in `peap_core`, move page-level runtime composition into `peap_parsers`, add an explicit record assembler and canonical normalizer in `peap`, and keep `peap/parsing.py` only as a compatibility facade during migration. The new subsystem must treat snapshots as first-class inputs, produce evidence-backed typed results, and make replay/cache invalidation depend on explicit runtime versions instead of hidden behavior.

**Tech Stack:** Python 3.11, pytest, SQLite, BeautifulSoup, existing `peap` / `peap_core` / `peap_parsers` / `peap_postprocess` packages, no new framework-level dependency unless fixture or storage pressure proves it necessary.

---

## Assumptions

- Existing exchange-specific parser classes remain the field extraction nucleus during migration; the plan changes runtime composition and contracts before it rewrites every extractor.
- This aggressive plan intentionally spans crawler/runtime/parser/store/export boundaries because the target architecture is a single parser subsystem, not a narrow parser cleanup.
- Compatibility entrypoints may exist temporarily, but they must become thin adapters only; no new business logic may be added to compatibility shims.
- Cross-page assembly is in scope for this plan. If execution must be staged more conservatively, stop after Task 4 and run the system in “page result only” mode behind a compatibility facade.
- Provenance is required from day one, but only as lightweight evidence refs by default; full DOM subtree archival is explicitly out of scope for first implementation.

## Boundary Rules

- `peap_core` may not import `peap`, `peap_parsers`, `desktop_backend`, or downloader modules.
- `peap_parsers` may depend on `peap_core` contracts, but may not depend on `desktop_backend`.
- `peap/parsing.py` becomes a compatibility facade; it may orchestrate the new subsystem but may not own source maps, fallback chains, or parser-specific special cases.
- Crawler/frontier code may emit snapshot envelopes and outgoing refs, but it may not parse page fields.
- Page parser families may extract facts and outgoing refs, but they may not perform record assembly, persistence, or export projection.
- Record assembly may not read DOM directly; it consumes `PageParseResult` only.
- Normalization may not peek into raw DOM or parser-private fields; it consumes assembled business objects only.
- Policy execution may patch canonical records only through typed patch contracts with diagnostics; silent merge semantics are forbidden.
- Export and UI code may consume canonical records or explicit compat projections only, never unrestricted parser payload passthrough.

## File Structure

**Shared parser-subsystem contracts**
- Create: `peap_core/snapshot_contracts.py`
- Create: `peap_core/page_parse_contracts.py`
- Create: `peap_core/record_contracts.py`
- Modify: `peap_core/__init__.py`
- Modify: `tests/test_environment_tooling.py`
- Create: `tests/test_snapshot_contracts.py`
- Create: `tests/test_page_parse_contracts.py`
- Create: `tests/test_record_contracts.py`

**Decoder and classifier runtime**
- Create: `peap_parsers/snapshot_decoder.py`
- Create: `peap_parsers/source_classifier.py`
- Create: `peap_parsers/source_detection_rules.py`
- Modify: `peap_parsers/utils.py`
- Modify: `peap_parsers/public_resource.py`
- Create: `tests/test_snapshot_decoder.py`
- Create: `tests/test_source_classifier.py`

**Page parser family runtime**
- Create: `peap_parsers/parser_registry.py`
- Create: `peap_parsers/family_runtime.py`
- Create: `peap_parsers/builtin_registry.py`
- Modify: `peap_parsers/base.py`
- Modify: `peap_parsers/__init__.py`
- Modify: `peap_parsers/beijing.py`
- Modify: `peap_parsers/shanghai.py`
- Modify: `peap_parsers/shenzhen.py`
- Modify: `peap_parsers/tianjin.py`
- Modify: `peap_parsers/chongqing.py`
- Modify: `peap_parsers/shandong.py`
- Modify: `peap_parsers/guangzhou.py`
- Create: `tests/test_parser_registry.py`
- Modify: `tests/test_parsing_contract.py`

**Cross-page assembly and canonical normalization**
- Create: `peap/record_assembler.py`
- Create: `peap/record_normalizer.py`
- Create: `peap/record_projection.py`
- Modify: `peap/standard_model.py`
- Modify: `peap/compat_payload.py`
- Create: `tests/test_record_assembler.py`
- Create: `tests/test_record_normalizer.py`
- Create: `tests/test_record_projection.py`

**Policy engine migration**
- Create: `peap/policy_engine.py`
- Create: `peap/policy_registry.py`
- Modify: `peap/streaming_postprocess.py`
- Modify: `peap_postprocess/postprocess_engine/contracts.py`
- Modify: `peap_postprocess/postprocess_engine/rules/builtin.py`
- Create: `tests/test_policy_engine.py`
- Modify: `tests/test_streaming_postprocess.py`

**Compatibility facade, ingest/store/export integration**
- Create: `peap/parser_subsystem.py`
- Modify: `peap/parsing.py`
- Modify: `peap/streaming_ingest.py`
- Modify: `peap/streaming_store.py`
- Modify: `peap/streaming_export.py`
- Modify: `peap/streaming_models.py`
- Modify: `tests/test_streaming_ingest.py`
- Modify: `tests/test_streaming_store.py`
- Modify: `tests/test_streaming_export.py`
- Create: `tests/test_parser_subsystem_e2e.py`

**Crawler/frontier and replay/cache wiring**
- Modify: `peap/download_tasks.py`
- Modify: `peap/download_runner.py`
- Modify: `peap/download_oneclick.py`
- Modify: `peap/streaming_daily_pipeline.py`
- Modify: `peap/parse_cache.py`
- Create: `tests/test_download_runner.py`
- Modify: `tests/test_streaming_daily_pipeline.py`
- Modify: `tests/test_parse_cache.py`

**Documentation and regression**
- Modify: `README.md`
- Modify: `docs/project_layout.md`
- Modify: `docs/parser_rule_risk_report.md`
- Create: `docs/superpowers/specs/2026-03-30-full-parser-subsystem-rearchitecture-design.md`

### Task 1: Freeze Shared Parser-Subsystem Contracts

**Files:**
- Create: `peap_core/snapshot_contracts.py`
- Create: `peap_core/page_parse_contracts.py`
- Create: `peap_core/record_contracts.py`
- Modify: `peap_core/__init__.py`
- Create: `tests/test_snapshot_contracts.py`
- Create: `tests/test_page_parse_contracts.py`
- Create: `tests/test_record_contracts.py`
- Modify: `tests/test_environment_tooling.py`

- [ ] **Step 1: Write failing contract tests for snapshot, page-parse, and assembled-record data shapes**

Add coverage asserting:
- snapshot envelopes serialize `snapshot_id`, digest, capture metadata, and storage path
- page parse contracts encode typed diagnostics, evidence refs, source matches, page identity, outgoing refs, and recoverability
- assembled record contracts represent `partial`, `sufficient`, `conflicted`, and `blocked` completion states

- [ ] **Step 2: Run the contract tests to verify they fail**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform
uv run pytest tests/test_snapshot_contracts.py tests/test_page_parse_contracts.py tests/test_record_contracts.py tests/test_environment_tooling.py -q
```

Expected: failures because the shared contracts do not exist yet.

- [ ] **Step 3: Implement minimal shared contracts in `peap_core`**

Create immutable dataclasses and any required helper methods for:
- `SnapshotEnvelope`
- `DecodedDocument`
- `SourceMatch`
- `Diagnostic`
- `EvidenceRef`
- `PageParseResult`
- `AssembledRecordCandidate`
- `CanonicalRecord`
- typed patch / policy audit support if needed by downstream tasks

- [ ] **Step 4: Re-run the contract tests**

Run the same command from Step 2.

Expected: tests pass and no contract module imports runtime-specific packages.

- [ ] **Step 5: Commit**

```bash
git add peap_core/snapshot_contracts.py peap_core/page_parse_contracts.py peap_core/record_contracts.py peap_core/__init__.py tests/test_snapshot_contracts.py tests/test_page_parse_contracts.py tests/test_record_contracts.py tests/test_environment_tooling.py
git commit -m "refactor: add parser subsystem core contracts"
```

### Task 2: Introduce Decoder And Source Classifier Layers

**Files:**
- Create: `peap_parsers/snapshot_decoder.py`
- Create: `peap_parsers/source_classifier.py`
- Create: `peap_parsers/source_detection_rules.py`
- Modify: `peap_parsers/utils.py`
- Modify: `peap_parsers/public_resource.py`
- Create: `tests/test_snapshot_decoder.py`
- Create: `tests/test_source_classifier.py`

- [ ] **Step 1: Write failing decoder and classifier tests**

Cover these behaviors:
- HTML snapshots decode into DOM + text + metadata
- MHTML snapshots decode into HTML parts without requiring parser-family-private logic
- classifier returns `matched`, `ambiguous`, or `unknown` instead of a naked source string
- public-resource MHTML pages are recognized by classifier rules instead of parser-side heuristics only

- [ ] **Step 2: Run decoder and classifier tests to verify failure**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform
uv run pytest tests/test_snapshot_decoder.py tests/test_source_classifier.py -q
```

Expected: failures because decoder/classifier modules do not exist and current source detection only returns a bare string.

- [ ] **Step 3: Implement snapshot decoder and source classifier**

Requirements:
- move generic MHTML decomposition out of `PublicResourceParser`
- make source detection emit `SourceMatch`
- keep existing exchange heuristics initially, but package them as ordered classifier rules with reasons and confidence

- [ ] **Step 4: Re-run decoder and classifier tests**

Run the same command from Step 2.

Expected: decoder and classifier tests pass, and `detect_exchange()` becomes either a compatibility wrapper or a deprecated shim over classifier output.

- [ ] **Step 5: Commit**

```bash
git add peap_parsers/snapshot_decoder.py peap_parsers/source_classifier.py peap_parsers/source_detection_rules.py peap_parsers/utils.py peap_parsers/public_resource.py tests/test_snapshot_decoder.py tests/test_source_classifier.py
git commit -m "refactor: add decoder and source classifier runtime"
```

### Task 3: Replace Hardcoded Parser Dispatch With Page Parser Family Runtime

**Files:**
- Create: `peap_parsers/parser_registry.py`
- Create: `peap_parsers/family_runtime.py`
- Create: `peap_parsers/builtin_registry.py`
- Modify: `peap_parsers/base.py`
- Modify: `peap_parsers/__init__.py`
- Modify: `peap_parsers/beijing.py`
- Modify: `peap_parsers/shanghai.py`
- Modify: `peap_parsers/shenzhen.py`
- Modify: `peap_parsers/tianjin.py`
- Modify: `peap_parsers/chongqing.py`
- Modify: `peap_parsers/shandong.py`
- Modify: `peap_parsers/guangzhou.py`
- Create: `tests/test_parser_registry.py`
- Modify: `tests/test_parsing_contract.py`

- [ ] **Step 1: Write failing tests for registry-driven page parser execution**

Cover these behaviors:
- parser runtime resolves a family from classifier output instead of `PARSER_MAP`
- Beijing and Shanghai variant selection stays local to family runtime
- page parsers output `PageParseResult` instead of raw dict-only results
- parser runtime emits typed diagnostics for unrecoverable and partial parse states

- [ ] **Step 2: Run parser runtime tests to verify failure**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform
uv run pytest tests/test_parser_registry.py tests/test_parsing_contract.py -q
```

Expected: failures referencing `PARSER_MAP`, dict payload assumptions, or missing page parse result contracts.

- [ ] **Step 3: Implement family runtime and migrate base parser interfaces**

Requirements:
- `WebPageParser` must accept decoded documents and emit `PageParseResult`
- keep compatibility helpers during migration, but isolate them behind adapter methods
- move family/variant metadata into registry bindings instead of hardcoding them only in orchestrator

- [ ] **Step 4: Re-run parser runtime tests**

Run the same command from Step 2.

Expected: registry-driven parser execution passes, and current site families are wrapped by the new runtime.

- [ ] **Step 5: Commit**

```bash
git add peap_parsers/parser_registry.py peap_parsers/family_runtime.py peap_parsers/builtin_registry.py peap_parsers/base.py peap_parsers/__init__.py peap_parsers/beijing.py peap_parsers/shanghai.py peap_parsers/shenzhen.py peap_parsers/tianjin.py peap_parsers/chongqing.py peap_parsers/shandong.py peap_parsers/guangzhou.py tests/test_parser_registry.py tests/test_parsing_contract.py
git commit -m "refactor: register page parser families and page parse results"
```

### Task 4: Add Compatibility Facade Over The New Parser Subsystem

**Files:**
- Create: `peap/parser_subsystem.py`
- Modify: `peap/parsing.py`
- Modify: `peap/standard_model.py`
- Modify: `peap/compat_payload.py`
- Create: `tests/test_parser_subsystem_e2e.py`
- Modify: `tests/test_parsing_contract.py`

- [ ] **Step 1: Write failing compatibility-facade tests**

Cover these behaviors:
- `parse_file()` delegates to the new subsystem
- old callers still receive a `ParsedProject`-compatible object
- compat payloads are explicit projections from canonical records, not raw parser passthrough

- [ ] **Step 2: Run facade tests to verify failure**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform
uv run pytest tests/test_parsing_contract.py tests/test_parser_subsystem_e2e.py -q
```

Expected: failures because compatibility still depends on old parser orchestration and raw payload semantics.

- [ ] **Step 3: Implement parser subsystem facade**

Requirements:
- `peap/parser_subsystem.py` orchestrates decode -> classify -> parse -> normalize facade flow
- `peap/parsing.py` becomes a thin adapter over subsystem results
- `ParsedProject` may remain temporarily, but its data must derive from new canonical/compat projections

- [ ] **Step 4: Re-run facade tests**

Run the same command from Step 2.

Expected: old entrypoints continue to work while being powered by the new runtime.

- [ ] **Step 5: Commit**

```bash
git add peap/parser_subsystem.py peap/parsing.py peap/standard_model.py peap/compat_payload.py tests/test_parser_subsystem_e2e.py tests/test_parsing_contract.py
git commit -m "refactor: route legacy parsing facade through parser subsystem"
```

### Task 5: Implement Cross-Page Record Assembly

**Files:**
- Create: `peap/record_assembler.py`
- Modify: `peap/streaming_models.py`
- Create: `tests/test_record_assembler.py`
- Modify: `tests/test_parser_subsystem_e2e.py`

- [ ] **Step 1: Write failing assembler contract tests**

Cover these behaviors:
- list + detail pages can assemble into one business object
- detail + announcement pages can remain `partial` until required facts appear
- conflicting candidate identities become `conflicted`, not silently merged
- outgoing refs and candidate tokens drive correlation instead of direct DOM inspection

- [ ] **Step 2: Run assembler tests to verify failure**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform
uv run pytest tests/test_record_assembler.py tests/test_parser_subsystem_e2e.py -q
```

Expected: failures because no assembler exists and current runtime remains single-page only.

- [ ] **Step 3: Implement record assembler and correlation state model**

Requirements:
- accept multiple `PageParseResult` objects
- expose completion states `partial`, `sufficient`, `conflicted`, `blocked`
- produce stable `assembly_id` / entity keys
- preserve source page lineage for downstream provenance

- [ ] **Step 4: Re-run assembler tests**

Run the same command from Step 2.

Expected: assembler tests pass and page lineage remains visible to downstream consumers.

- [ ] **Step 5: Commit**

```bash
git add peap/record_assembler.py peap/streaming_models.py tests/test_record_assembler.py tests/test_parser_subsystem_e2e.py
git commit -m "feat: add cross-page record assembler"
```

### Task 6: Introduce Canonical Normalizer And Explicit Record Projection

**Files:**
- Create: `peap/record_normalizer.py`
- Create: `peap/record_projection.py`
- Modify: `peap/standard_model.py`
- Modify: `peap/compat_payload.py`
- Modify: `peap/streaming_export.py`
- Create: `tests/test_record_normalizer.py`
- Create: `tests/test_record_projection.py`
- Modify: `tests/test_streaming_export.py`

- [ ] **Step 1: Write failing normalizer and projection tests**

Cover these behaviors:
- assembled records normalize into one stable canonical schema
- date, amount, project-type, and status invariants are enforced in one layer
- export payload generation depends on explicit projection from canonical records only
- arbitrary parser extras do not leak into exports

- [ ] **Step 2: Run normalizer and projection tests to verify failure**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform
uv run pytest tests/test_record_normalizer.py tests/test_record_projection.py tests/test_streaming_export.py -q
```

Expected: failures because canonical normalization and downstream projection are still entangled with parser payloads.

- [ ] **Step 3: Implement normalizer and explicit projection layer**

Requirements:
- canonical schema becomes the only internal record shape for downstream consumers
- `build_compat_payload()` accepts canonical input or an explicit projected view, not raw parser dicts
- exports consume canonical/projection data only

- [ ] **Step 4: Re-run normalizer and projection tests**

Run the same command from Step 2.

Expected: tests pass and raw parser fields no longer bleed into export rows.

- [ ] **Step 5: Commit**

```bash
git add peap/record_normalizer.py peap/record_projection.py peap/standard_model.py peap/compat_payload.py peap/streaming_export.py tests/test_record_normalizer.py tests/test_record_projection.py tests/test_streaming_export.py
git commit -m "refactor: normalize assembled records and project explicit downstream payloads"
```

### Task 7: Migrate Postprocess Logic Into A Canonical Policy Engine

**Files:**
- Create: `peap/policy_engine.py`
- Create: `peap/policy_registry.py`
- Modify: `peap/streaming_postprocess.py`
- Modify: `peap_postprocess/postprocess_engine/contracts.py`
- Modify: `peap_postprocess/postprocess_engine/rules/builtin.py`
- Create: `tests/test_policy_engine.py`
- Modify: `tests/test_streaming_postprocess.py`

- [ ] **Step 1: Write failing policy-engine tests**

Cover these behaviors:
- mapping/group/type rules execute against canonical records
- policies emit typed patches and diagnostics
- default overwrite semantics refuse to silently replace high-confidence fields
- existing PPE rule bindings still work through adapters or explicitly migrated wrappers

- [ ] **Step 2: Run policy-engine tests to verify failure**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform
uv run pytest tests/test_policy_engine.py tests/test_streaming_postprocess.py -q
```

Expected: failures because current record postprocess still depends on raw payload merge semantics.

- [ ] **Step 3: Implement policy engine and migrate streaming postprocess entrypoints**

Requirements:
- preserve existing mapping/rulepack business behavior where possible
- make every mutation auditable via typed patches
- retain compatibility wrappers only as thin adapters

- [ ] **Step 4: Re-run policy-engine tests**

Run the same command from Step 2.

Expected: policy tests pass and `streaming_postprocess` becomes a compatibility surface over canonical policy execution.

- [ ] **Step 5: Commit**

```bash
git add peap/policy_engine.py peap/policy_registry.py peap/streaming_postprocess.py peap_postprocess/postprocess_engine/contracts.py peap_postprocess/postprocess_engine/rules/builtin.py tests/test_policy_engine.py tests/test_streaming_postprocess.py
git commit -m "refactor: migrate record postprocess into canonical policy engine"
```

### Task 8: Rewire Ingest, Store, And Export Around The New Subsystem

**Files:**
- Modify: `peap/streaming_ingest.py`
- Modify: `peap/streaming_store.py`
- Modify: `peap/streaming_export.py`
- Modify: `peap/streaming_models.py`
- Modify: `tests/test_streaming_ingest.py`
- Modify: `tests/test_streaming_store.py`
- Modify: `tests/test_streaming_export.py`

- [ ] **Step 1: Write failing integration tests for canonical ingest/store/export flow**

Cover these behaviors:
- ingest stores snapshot identity, page lineage, assembly state, canonical record, and policy findings
- failed pages preserve typed failure taxonomy instead of opaque parse exceptions
- export reads canonical revisions rather than parser/postprocess payload merges

- [ ] **Step 2: Run ingest/store/export tests to verify failure**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform
uv run pytest tests/test_streaming_ingest.py tests/test_streaming_store.py tests/test_streaming_export.py -q
```

Expected: failures because store schema and ingest runner still assume single-page parser payloads.

- [ ] **Step 3: Implement canonical ingest/store/export integration**

Requirements:
- store snapshot lineage and assembly lineage explicitly
- keep revision history on canonical records and policy patches
- adapt legacy callers through compatibility projection where still required

- [ ] **Step 4: Re-run ingest/store/export tests**

Run the same command from Step 2.

Expected: integration tests pass and the streaming path no longer depends on raw parser payload semantics as the system of record.

- [ ] **Step 5: Commit**

```bash
git add peap/streaming_ingest.py peap/streaming_store.py peap/streaming_export.py peap/streaming_models.py tests/test_streaming_ingest.py tests/test_streaming_store.py tests/test_streaming_export.py
git commit -m "refactor: wire streaming ingest store and export to parser subsystem"
```

### Task 9: Upgrade Crawler, Frontier, Replay, And Parse Cache

**Files:**
- Modify: `peap/download_tasks.py`
- Modify: `peap/download_runner.py`
- Modify: `peap/download_oneclick.py`
- Modify: `peap/streaming_daily_pipeline.py`
- Modify: `peap/parse_cache.py`
- Modify: `tests/test_download_runner.py`
- Modify: `tests/test_streaming_daily_pipeline.py`
- Modify: `tests/test_parse_cache.py`

- [ ] **Step 1: Write failing tests for snapshot-envelope propagation and runtime-aware cache invalidation**

Cover these behaviors:
- download/save callbacks emit snapshot envelopes with URL and digest metadata
- replay can rerun decode/classify/parse/assemble/normalize deterministically from stored snapshots
- parse cache keys include decoder/classifier/family/variant/assembler/normalizer/policy versions

- [ ] **Step 2: Run frontier and cache tests to verify failure**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform
uv run pytest tests/test_download_runner.py tests/test_streaming_daily_pipeline.py tests/test_parse_cache.py -q
```

Expected: failures because current callbacks and cache signatures only encode old parser assumptions.

- [ ] **Step 3: Implement snapshot-envelope propagation and upgraded cache keys**

Requirements:
- downloader pipeline emits stable snapshot metadata
- replay runner can rebuild canonical records from snapshots
- cache invalidation is explicit and versioned, not based only on mtimes or a narrow parser signature

- [ ] **Step 4: Re-run frontier and cache tests**

Run the same command from Step 2.

Expected: tests pass and replay/cache behavior is stable under subsystem version changes.

- [ ] **Step 5: Commit**

```bash
git add peap/download_tasks.py peap/download_runner.py peap/download_oneclick.py peap/streaming_daily_pipeline.py peap/parse_cache.py tests/test_download_runner.py tests/test_streaming_daily_pipeline.py tests/test_parse_cache.py
git commit -m "refactor: propagate snapshot envelopes and versioned parser cache"
```

### Task 10: Final Documentation And Full Regression Pass

**Files:**
- Modify: `README.md`
- Modify: `docs/project_layout.md`
- Modify: `docs/parser_rule_risk_report.md`
- Modify: `docs/superpowers/specs/2026-03-30-full-parser-subsystem-rearchitecture-design.md`
- Modify: `docs/superpowers/plans/2026-03-30-full-parser-subsystem-rearchitecture.md`

- [x] **Step 1: Update architecture documentation and migration notes**

Document:
- subsystem topology
- compatibility facades still present
- replay and provenance expectations
- operational risks during migration

- [x] **Step 2: Run targeted regression suites**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform
uv run pytest \
  tests/test_snapshot_contracts.py \
  tests/test_page_parse_contracts.py \
  tests/test_record_contracts.py \
  tests/test_snapshot_decoder.py \
  tests/test_source_classifier.py \
  tests/test_parser_registry.py \
  tests/test_parsing_contract.py \
  tests/test_parser_subsystem_e2e.py \
  tests/test_record_assembler.py \
  tests/test_record_normalizer.py \
  tests/test_record_projection.py \
  tests/test_policy_engine.py \
  tests/test_streaming_postprocess.py \
  tests/test_streaming_ingest.py \
  tests/test_streaming_store.py \
  tests/test_streaming_export.py \
  tests/test_download_runner.py \
  tests/test_streaming_daily_pipeline.py \
  tests/test_parse_cache.py -q
```

Expected: all parser-subsystem contract and integration suites pass.

- [x] **Step 3: Run a replay smoke test against a fixed snapshot corpus**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform
uv run python -m peap.streaming_daily_pipeline --dry-run
```

Expected: dry run completes without schema errors, and replay output includes typed diagnostics instead of opaque parser exceptions.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/project_layout.md docs/parser_rule_risk_report.md docs/superpowers/specs/2026-03-30-full-parser-subsystem-rearchitecture-design.md docs/superpowers/plans/2026-03-30-full-parser-subsystem-rearchitecture.md
git commit -m "docs: capture full parser subsystem rearchitecture"
```

## Execution Notes

- Do not start Task 5 before Task 4 passes; cross-page assembly without a stabilized page result contract will create churn.
- Do not migrate `streaming_store` to canonical-first persistence until Task 7 passes; otherwise the store schema will freeze around temporary payload shapes.
- Keep fixture corpora small and purposeful. Prefer one list/detail pair and one conflict pair per source over giant opaque corpora.
- If rollout risk becomes too high, release behind a parser-subsystem feature flag after Task 4, then continue Tasks 5-9 behind the flag.
