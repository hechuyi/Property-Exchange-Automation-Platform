# Downloader 3.0 Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the downloader 3.0 architecture so that errors are explicit and source-specific page differences are modeled as configuration/capabilities rather than scattered hard-coded behavior.

**Architecture:** Keep the current 3.0 split (`download_runner` orchestration, `download_runtime` runtime adapter, `download_oneclick_presenters` formatter layer, source-specific downloaders) but tighten the boundaries with explicit process models, typed downloader errors, and capability/manifest-driven source configuration. The key rule is that workflow layers consume structured contracts, not ad-hoc strings and not source-specific field knowledge.

**Tech Stack:** Python 3.11, dataclasses, pytest, ruff, existing `uv` workflow, current downloader modules under `peap/`.

---

## File Structure

### Create
- `peap/download_capabilities.py` — downloader capability and manifest dataclasses; explicit declaration of what a source supports.
- `peap/download_errors.py` — structured downloader error types and serialization helpers.
- `tests/test_download_capabilities.py` — capability/manifest tests.
- `tests/test_download_errors.py` — typed error contract tests.

### Modify
- `peap/download_tasks.py` — replace pure class-binding registry semantics with manifest/capability-backed task specs while keeping current callers working.
- `peap/download_models.py` — add explicit process models for collect/materialize execution boundaries.
- `peap/download_runtime.py` — consume declared capabilities instead of relying on implicit constructor/signature conventions only.
- `peap/download_runner.py` — consume structured process results and typed errors.
- `peap/download_task_flow.py` — propagate explicit collect/materialize results and typed errors rather than assembling opaque strings.
- `peap/download_execution.py` — emit typed execution failures.
- `peap/download_oneclick.py` — stop inventing domain error meaning in the presenter path; consume typed errors.
- `peap/download_oneclick_presenters.py` — formatter only; map typed errors to UI payloads without defining domain semantics.
- `peap/downloaders/common.py` — keep shared downloader contract pieces only; do not grow this into a miscellaneous dumping ground.
- `peap/downloaders/*.py` — progressively consume manifest/capability/config objects where source differences are currently embedded as hard-coded behavior.
- `tests/test_download_runner.py` — regression tests for explicit models and typed errors.
- `tests/test_download_oneclick.py` — regression tests for formatter-only presenter behavior.
- `tests/test_download_split_modules.py` — regression tests for explicit collect/split process objects.
- `tests/test_streaming_daily_pipeline.py` — verify downstream consumers still receive stable structured events.

## Design Constraints To Preserve

1. **Errors must be explicit.**
   - Downloader failures must move toward typed, structured errors (`error_code`, `stage`, `source`, `failure_kind`, `details`) rather than opaque strings.
   - Presentation code may format errors for UI, but may not invent domain categories by parsing raw strings.

2. **Source differences must be configurable where possible.**
   - Exchange/page variation should be expressed through manifests, capabilities, selectors, and source-specific configuration objects.
   - Hard-coded behavior should be reserved for truly source-unique logic, not for toggles that could be declarative.
   - Page redesigns are expected; the architecture should minimize the blast radius of selector/route/list-shape changes.

3. **Capabilities must drive orchestration.**
   - The runner/workflow layer should ask what a source supports instead of branching on source ids or guessing from implementation details.

4. **Intermediate process objects must be explicit.**
   - Request normalization, collect results, candidate batches, materialize results, and typed execution errors should be first-class models.

5. **`common.py` must not become a junk drawer.**
   - Shared contracts and pure helpers may live there temporarily, but new cross-cutting models belong in purpose-specific modules.

---

### Task 1: Add downloader capability/manifest models

**Files:**
- Create: `peap/download_capabilities.py`
- Modify: `peap/download_tasks.py`
- Test: `tests/test_download_capabilities.py`

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

import unittest

from peap.download_capabilities import DownloadDriverCapabilities, DownloadTaskManifest
from peap.download_tasks import DownloadTaskSpec, build_task_registry


class DownloadCapabilitiesTest(unittest.TestCase):
    def test_task_spec_exposes_manifest_and_capabilities(self) -> None:
        registry = build_task_registry()
        spec = registry["sse:physical_asset"]

        self.assertIsInstance(spec.manifest, DownloadTaskManifest)
        self.assertIsInstance(spec.capabilities, DownloadDriverCapabilities)
        self.assertTrue(spec.capabilities.supports_list_only)
        self.assertTrue(spec.capabilities.supports_prefetched_candidates)
        self.assertEqual(spec.manifest.task_id, "sse:physical_asset")
        self.assertEqual(spec.manifest.source_id, "sse")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_download_capabilities.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'peap.download_capabilities'`

- [ ] **Step 3: Write minimal implementation**

```python
# peap/download_capabilities.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DownloadDriverCapabilities:
    supports_list_only: bool = True
    supports_prefetched_candidates: bool = True
    supports_split_planning: bool = True
    supports_resume: bool = True
    supports_asset_snapshot: bool = True


@dataclass(frozen=True)
class DownloadTaskManifest:
    task_id: str
    source_id: str
    project_type: str
    display_name: str
```

```python
# peap/download_tasks.py (conceptual change)
@dataclass(frozen=True)
class DownloadTaskSpec:
    exchange_code: str
    project_type: str
    display_name: str
    downloader_cls: Type
    default_page_size: int
    manifest: DownloadTaskManifest
    capabilities: DownloadDriverCapabilities
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_download_capabilities.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add peap/download_capabilities.py peap/download_tasks.py tests/test_download_capabilities.py
git commit -m "refactor: add downloader task capabilities"
```

### Task 2: Add typed downloader errors

**Files:**
- Create: `peap/download_errors.py`
- Modify: `peap/download_oneclick_presenters.py`
- Modify: `peap/download_oneclick.py`
- Test: `tests/test_download_errors.py`
- Test: `tests/test_download_oneclick.py`

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

import unittest

from peap.download_errors import DownloadError, collect_failed_error


class DownloadErrorsTest(unittest.TestCase):
    def test_collect_failed_error_is_structured(self) -> None:
        error = collect_failed_error(
            source_id="tpre",
            task_id="tpre:physical_asset",
            raw_reason="upstream 500",
        )

        self.assertIsInstance(error, DownloadError)
        self.assertEqual(error.error_code, "tpre_collect_failed")
        self.assertEqual(error.stage, "prepare_tasks")
        self.assertEqual(error.failure_kind, "collect")
        self.assertEqual(error.task_id, "tpre:physical_asset")
        self.assertEqual(error.raw_reason, "upstream 500")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_download_errors.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'peap.download_errors'`

- [ ] **Step 3: Write minimal implementation**

```python
# peap/download_errors.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DownloadError:
    error_code: str
    stage: str
    failure_kind: str
    source_id: str
    task_id: str = ""
    raw_reason: str = ""


def collect_failed_error(*, source_id: str, task_id: str, raw_reason: str) -> DownloadError:
    return DownloadError(
        error_code=f"{source_id}_collect_failed",
        stage="prepare_tasks",
        failure_kind="collect",
        source_id=source_id,
        task_id=task_id,
        raw_reason=raw_reason,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_download_errors.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add peap/download_errors.py tests/test_download_errors.py peap/download_oneclick.py peap/download_oneclick_presenters.py
git commit -m "refactor: add typed downloader errors"
```

### Task 3: Add explicit collect/materialize process models

**Files:**
- Modify: `peap/download_models.py`
- Modify: `peap/download_task_flow.py`
- Modify: `peap/download_execution.py`
- Test: `tests/test_download_split_modules.py`

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

import unittest

from peap.download_models import DownloadCollectResult, DownloadMaterializeResult


class DownloadProcessModelsTest(unittest.TestCase):
    def test_collect_and_materialize_results_are_explicit_models(self) -> None:
        collect = DownloadCollectResult(candidate_entries=[{"project_code": "XM001"}], errors=[])
        materialize = DownloadMaterializeResult(saved_count=1, errors=[])

        self.assertEqual(len(collect.candidate_entries), 1)
        self.assertEqual(materialize.saved_count, 1)
        self.assertEqual(materialize.errors, [])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_download_split_modules.py -q -k explicit_models`
Expected: FAIL with `ImportError` or missing attribute error for `DownloadCollectResult`

- [ ] **Step 3: Write minimal implementation**

```python
# peap/download_models.py
@dataclass(frozen=True)
class DownloadCollectResult:
    candidate_entries: list[dict[str, object]]
    errors: list[str]


@dataclass(frozen=True)
class DownloadMaterializeResult:
    saved_count: int
    errors: list[str]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_download_split_modules.py -q -k explicit_models`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add peap/download_models.py peap/download_task_flow.py peap/download_execution.py tests/test_download_split_modules.py
git commit -m "refactor: add downloader process result models"
```

### Task 4: Make presenter layer formatter-only

**Files:**
- Modify: `peap/download_oneclick_presenters.py`
- Modify: `peap/download_oneclick.py`
- Test: `tests/test_download_oneclick_presenters.py`
- Test: `tests/test_download_oneclick.py`

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

import unittest

from peap.download_errors import DownloadError
from peap.download_oneclick_presenters import format_download_error


class DownloadOneClickPresenterFormattingTest(unittest.TestCase):
    def test_presenter_formats_typed_error_without_parsing_raw_strings(self) -> None:
        error = DownloadError(
            error_code="cbex_list_failed",
            stage="prepare_tasks",
            failure_kind="list",
            source_id="cbex",
            task_id="cbex:equity_transfer",
            raw_reason="api-http-521",
        )

        payload = format_download_error(error)

        self.assertEqual(payload["error_code"], "cbex_list_failed")
        self.assertEqual(payload["error_details"]["stage"], "prepare_tasks")
        self.assertEqual(payload["error_details"]["failure_kind"], "list")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_download_oneclick_presenters.py -q -k typed_error`
Expected: FAIL because `format_download_error` does not exist yet

- [ ] **Step 3: Write minimal implementation**

```python
# peap/download_oneclick_presenters.py
from peap.download_errors import DownloadError


def format_download_error(error: DownloadError) -> dict[str, object]:
    return {
        "error_code": error.error_code,
        "error_message": error.raw_reason,
        "error_details": {
            "stage": error.stage,
            "failure_kind": error.failure_kind,
            "exchange": error.source_id,
            "task_id": error.task_id,
            "raw_reason": error.raw_reason,
        },
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_download_oneclick_presenters.py -q -k typed_error`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add peap/download_oneclick_presenters.py peap/download_oneclick.py tests/test_download_oneclick_presenters.py tests/test_download_oneclick.py
git commit -m "refactor: make oneclick presenter formatter-only"
```

### Task 5: Move source variability toward manifest/config models

**Files:**
- Modify: `peap/download_capabilities.py`
- Modify: `peap/download_tasks.py`
- Modify: `peap/downloaders/sse_physical.py`
- Modify: `peap/downloaders/cbex_physical.py`
- Modify: `peap/downloaders/cquae.py`
- Modify: `peap/downloaders/tpre.py`
- Test: `tests/test_exchange_downloader_fixes.py`
- Test: `tests/test_download_capabilities.py`

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

import unittest

from peap.download_tasks import build_task_registry


class DownloadSourceConfigTest(unittest.TestCase):
    def test_task_manifest_exposes_configurable_source_variation(self) -> None:
        registry = build_task_registry()
        spec = registry["sse:physical_asset"]

        self.assertIsInstance(spec.manifest.list_endpoint, str)
        self.assertIsInstance(spec.manifest.detail_route, str)
        self.assertGreater(len(spec.manifest.date_field_candidates), 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_download_capabilities.py -q -k source_variation`
Expected: FAIL because manifest fields like `list_endpoint` do not exist yet

- [ ] **Step 3: Write minimal implementation**

```python
# peap/download_capabilities.py
@dataclass(frozen=True)
class DownloadTaskManifest:
    task_id: str
    source_id: str
    project_type: str
    display_name: str
    list_endpoint: str = ""
    detail_route: str = ""
    date_field_candidates: tuple[str, ...] = ()
```

```python
# peap/download_tasks.py (example row creation)
manifest = DownloadTaskManifest(
    task_id=task_id,
    source_id=exchange_code,
    project_type=project_type,
    display_name=_task_display_name(exchange_code, project_type),
    list_endpoint="https://www.suaee.com/manageprojectweb/foreign/project/queryAllNew",
    detail_route="jymhzichan",
    date_field_candidates=("plksrq", "gpksrq", "list_disclosure_start"),
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_download_capabilities.py -q -k source_variation`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add peap/download_capabilities.py peap/download_tasks.py peap/downloaders/sse_physical.py peap/downloaders/cbex_physical.py peap/downloaders/cquae.py peap/downloaders/tpre.py tests/test_download_capabilities.py tests/test_exchange_downloader_fixes.py
git commit -m "refactor: move downloader source differences into manifests"
```

### Task 6: Add cross-source protocol conformance tests

**Files:**
- Modify: `tests/test_downloaders_module_contracts.py`
- Modify: `tests/test_exchange_downloader_fixes.py`

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

import unittest

from peap.download_tasks import build_task_registry


class DownloaderConformanceTest(unittest.TestCase):
    def test_all_registered_sources_declare_required_capabilities(self) -> None:
        registry = build_task_registry()
        for spec in registry.values():
            self.assertTrue(spec.capabilities.supports_list_only)
            self.assertTrue(spec.capabilities.supports_prefetched_candidates)
            self.assertNotEqual(spec.manifest.source_id, "")
            self.assertNotEqual(spec.manifest.task_id, "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_downloaders_module_contracts.py -q -k capabilities`
Expected: FAIL until all specs declare capabilities and manifests

- [ ] **Step 3: Write minimal implementation**

```python
# Keep registry creation complete for all current task specs.
registry[task_id] = DownloadTaskSpec(
    exchange_code=exchange_code,
    project_type=project_type,
    display_name=display_name,
    downloader_cls=downloader_cls,
    default_page_size=page_size[task_id],
    manifest=manifest,
    capabilities=DownloadDriverCapabilities(),
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_downloaders_module_contracts.py -q -k capabilities`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_downloaders_module_contracts.py tests/test_exchange_downloader_fixes.py peap/download_tasks.py peap/download_capabilities.py
git commit -m "test: add downloader protocol conformance coverage"
```

## Self-Review Notes

### Spec coverage
- Explicit error principle: covered by Task 2 and Task 4.
- Configurable, not purely hard-coded source variation: covered by Task 1, Task 5, and Task 6.
- Harder orchestration boundaries and process objects: covered by Task 3 and then consumed by Task 4/5.

### Placeholder scan
- No `TODO`/`TBD` placeholders left in the task steps.
- Each task contains exact file paths and exact verification commands.

### Type consistency
- Capability model names are consistent: `DownloadDriverCapabilities`, `DownloadTaskManifest`.
- Process object names are consistent: `DownloadCollectResult`, `DownloadMaterializeResult`.
- Typed error model is consistent: `DownloadError`.

## Immediate conclusion from current code

1. **Explicit errors are only partially present today.**
   - Some structured UI-facing error payloads exist in `peap/download_oneclick_presenters.py`, but most downloader execution still propagates string errors (`errors: list[str]`) in `peap/download_models.py`, `peap/download_task_flow.py`, and runner/task flow layers.
   - So the current architecture does **not** yet fully enforce explicit typed errors end to end.

2. **Source differences are not yet configurable enough.**
   - `peap/download_tasks.py` still uses `_TASK_BINDINGS` with direct `downloader_cls` hard-coding.
   - The source modules still encode many page-shape assumptions directly inside source files.
   - So the current architecture does **not** yet make source variation primarily declarative/configurable.

The next work should focus on these two hardening tracks before further structural expansion.

---

Plan complete and saved to `docs/superpowers/plans/2026-04-01-downloader-3.0-hardening.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
