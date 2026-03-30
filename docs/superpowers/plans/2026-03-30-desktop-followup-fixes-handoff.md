# Desktop Follow-up Fixes Handoff Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` or `superpowers:subagent-driven-development` together with `superpowers:test-driven-development`. Execute strictly in task order. Each task must follow: failing test -> targeted run confirming failure -> minimal implementation -> targeted run confirming pass -> commit.

**Goal:** Close the five regressions surfaced during desktop manual verification without reopening product decisions already settled by `2026-03-30-desktop-frontend-restructure.md`.

**Architecture:** Keep the workflow-first React/Electron shell as the target surface. Frontend fixes should stay centered in `desktop_app/src/` unless the issue explicitly requires Electron IPC, Python backend contract, or exchange parser/downloader changes. Do not reintroduce `tasks` as a primary page, do not revert the current `projectType` enum contract, and do not expand scope beyond the five issues listed here.

**Tech Stack:** Electron, React, TypeScript, Ant Design, Vitest, Node test runner, Python backend (`desktop_backend`), streaming ingest/postprocess pipeline (`peap/`), exchange parsers (`peap_parsers/`).

## Status Update (2026-03-30)

This handoff has already been advanced through the frontend-only portion on branch/worktree `codex/desktop-frontend-restructure` at:

```bash
/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/codex-desktop-frontend-restructure
```

Completed in this pass:

- Task 1: `TaskActivityPanel` now uses bounded internal scroll regions for recent jobs and event detail.
- Task 2: desktop file actions now validate missing paths in `main.js` and surface explicit renderer feedback in `RecordsPage` and `SettingsPage`.
- Task 3: batch mapping save summary now distinguishes saved-rule count from covered pending-item count while preserving intentional deduplication.

Fresh verification already run successfully after the frontend pass:

- `cd .../.worktrees/codex-desktop-frontend-restructure/desktop_app && npm test`
- `cd .../.worktrees/codex-desktop-frontend-restructure/desktop_app && npx vitest run`
- `cd .../.worktrees/codex-desktop-frontend-restructure/desktop_app && npm run build`
- `cd .../.worktrees/codex-desktop-frontend-restructure && uv run python scripts/check_release_gate.py`

Remaining work is now backend/parser-only: Task 4 and Task 5.

---

## Scope Constraints

- This is a follow-up fix pack, not a redesign pass.
- Preserve the shell direction already implemented: `workbench / records / mappings` are the primary destinations; `settings` remains low-frequency.
- For path-related UX, prefer native system behaviors over freeform string input.
- For records UI, continue exposing user-decision states rather than backend technical states.
- For mappings UI, keep the queue and current editor visible together on first paint.
- Do not use `/private/tmp` probe leftovers as evidence.
- Do not revert the known `projectType` contract fix: `equity_transfer / physical_asset / capital_increase / pre_disclosure`.

## Recommended Continuation Context

- Preferred continuation branch/worktree if it still exists: `.worktrees/codex-desktop-frontend-restructure`
- Preferred repo root for commands in this handoff:

```bash
/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/codex-desktop-frontend-restructure
```

If that worktree no longer exists, recreate or resume equivalent branch state before implementation.

---

## Verified Findings

### 1. Workbench task activity panel has no internal scroll containment [resolved in current frontend pass]

The current task activity panel is functionally correct but layout-incomplete. In `desktop_app/src/features/tasks/TaskActivityPanel.tsx`, the recent task list and current task detail both render directly inside cards without a bounded scrolling container. Once task history grows, the entire `workbench` page stretches vertically instead of keeping the activity area locally scrollable.

This was resolved in the current frontend pass by adding bounded scroll containers inside `desktop_app/src/features/tasks/TaskActivityPanel.tsx` together with regression coverage in `desktop_app/src/features/tasks/TaskActivityPanel.test.tsx`. No backend contract change was required.

### 2. “打开文件” and “在文件夹中显示” are wired, but failure is silent [resolved in current frontend pass]

The buttons are not fake. The bridge path exists end to end:

- `desktop_app/preload.js` exposes `openPath` and `showItemInFolder`
- `desktop_app/main.js` handles `peap:open-path` and `peap:show-item-in-folder`
- `desktop_app/src/pages/RecordsPage.tsx` calls the bridge from row actions

The problem was that the failure path was silently swallowed. `shell.openPath()` returns an error string on failure, but the renderer ignored the return value. `shell.showItemInFolder()` was also invoked without existence validation and returned an empty string regardless of whether the path was valid. The result was a user-visible no-op.

This was resolved in the current frontend pass with a small Electron IPC contract refinement in `desktop_app/main.js` plus explicit renderer feedback in `desktop_app/src/pages/RecordsPage.tsx` and `desktop_app/src/pages/SettingsPage.tsx`. `preload.js` and `window.d.ts` did not need changes because the normalized string-return contract stayed compatible.

### 3. The current Shanghai/SUAEE physical asset downloader path is stale

This was verified against the current official site on **2026-03-30**.

The repository downloader still targets the legacy endpoint in `peap/downloaders/sse_physical.py`:

```python
LIST_API_URL = "https://www.suaee.com/manageprojectweb/foreign/project/queryAllNew"
```

That legacy endpoint now returns `404`. The current official site has moved to the `/si` API namespace and `home.html#/` / `xmdt.html#/xmList` / `xmzx.html#/zczrDetail` route family. Live traffic shows the relevant APIs are now:

- `POST /si/prjs/realright/list`
- `POST /si/prjs/realright/options`
- `POST /si/prjs/realright/detail_zspl`

The parser side is stale as well. `peap_parsers/shanghai_standard.py` still depends on legacy DOM classes such as `.project_code`, `.project_xmmc`, `.project_content`, and `.project_contact`; those selectors are absent in the current detail page. The page still contains the data semantically, but not in the old DOM structure.

This issue is therefore not a one-line URL swap. Both downloader entrypoints and detail-page parsing assumptions need migration.

### 4. “保存已填规则一次只能保存一个” is most likely a batch semantics and feedback problem, not a simple loop break [resolved in current frontend pass]

The current batch save path is:

- `desktop_app/src/pages/MappingsPage.tsx` -> `saveDraftMappings()`
- `desktop_app/src/features/mappings/flows.ts` -> `runBatchMappingUpsertFlow()`

`runBatchMappingUpsertFlow()` intentionally deduplicates pending drafts by:

- `match_field`
- `target_field`
- `source_name`
- `target_value`

If two pending items resolve to the exact same rule, only one mapping rule is written, but all grouped `recordId`s are treated as handled. This is internally coherent, but the current success feedback is phrased as “saved N rules”, which is easy to misread as “only one pending item was saved” when multiple rows collapsed into one rule.

There was not yet evidence that two genuinely distinct rules also collapsed incorrectly. The implementation therefore stayed focused on test coverage and feedback semantics rather than removing deduplication. The current frontend pass added coverage for both distinct-rule and identical-rule batches, and updated the batch summary to report both saved-rule count and covered pending-item count.

### 5. Scrap/disposal filtering happens too late in the streaming postprocess pipeline

This is a real ordering bug.

In `peap/streaming_postprocess.py`, `run_record_postprocess()` currently applies mapping entries before optional postprocess rules. The scrap/disposal rule lives in the optional rules registry (`R010FilterScrapPhysicalAssetRule`), so scrap rows can already enter mapping analysis and even become `pending_mapping` before the filter rule runs.

There is a second defect: when `_apply_optional_rule_registry()` receives a `filter_out_row` patch, it only appends a filtering finding and does not convert the record into a `skipped` state for the streaming pipeline. That means the filter does not actually short-circuit the ingest lifecycle the way the product expectation requires.

This issue should be fixed by making rule-driven filtering a first-class pre-mapping phase inside postprocess, and by treating `filter_out_row` as a terminal `skipped` outcome for the streaming ingest path. Do not solve it by adding a scrap-only special case in the main pipeline.

---

## Task Order

The original execution order has been partially completed. Do not reopen Tasks 1-3 unless Task 4 or Task 5 exposes a concrete regression that requires it.

1. Completed on 2026-03-30: Task 1: Add internal scroll containment to task activity
2. Completed on 2026-03-30: Task 2: Make file-system actions succeed visibly or fail visibly
3. Completed on 2026-03-30: Task 3: Clarify batch mapping save semantics and coverage
4. Remaining: Task 4: Make rule filtering terminal before mapping, with mapping as the last postprocess phase
5. Remaining: Task 5: Migrate Shanghai/SUAEE downloader and parser to the current site

---

## Task 1: Contain Task Activity With Internal Scroll [completed 2026-03-30]

Completed in `codex/desktop-frontend-restructure`.

- Actual files changed: `desktop_app/src/features/tasks/TaskActivityPanel.tsx`, `desktop_app/src/features/tasks/TaskActivityPanel.test.tsx`
- Not needed: `desktop_app/src/styles/app.css`

**Files**

- Modify: `desktop_app/src/features/tasks/TaskActivityPanel.tsx`
- Modify: `desktop_app/src/features/tasks/TaskActivityPanel.test.tsx`
- Modify: `desktop_app/src/styles/app.css` only if layout tokens or reusable scroll styles are needed

### Intent

Keep `workbench` height stable when task history grows by giving the activity area its own scroll container instead of letting the full page elongate.

### TDD Steps

- [x] Add failing tests that assert the recent-task area and/or task-detail area render within a bounded scrolling region.
- [x] Run the targeted test file and confirm failure against the current unbounded layout.
- [x] Implement the smallest layout change that introduces internal scroll without changing the information hierarchy.
- [x] Re-run the targeted test file and confirm pass.
- [x] Commit scope was kept to the files above, except `app.css` was not needed.

### Verification

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/codex-desktop-frontend-restructure/desktop_app
npx vitest run src/features/tasks/TaskActivityPanel.test.tsx
```

### Commit

```bash
git add desktop_app/src/features/tasks/TaskActivityPanel.tsx desktop_app/src/features/tasks/TaskActivityPanel.test.tsx desktop_app/src/styles/app.css
git commit -m "fix: contain workbench task activity overflow"
```

---

## Task 2: Make File Open/Reveal Actions Observable [completed 2026-03-30]

Completed in `codex/desktop-frontend-restructure`.

- Actual files changed: `desktop_app/main.js`, `desktop_app/main.test.js`, `desktop_app/src/pages/RecordsPage.tsx`, `desktop_app/src/pages/RecordsPage.test.tsx`, `desktop_app/src/pages/SettingsPage.tsx`, `desktop_app/src/pages/SettingsPage.test.tsx`
- Not needed: `desktop_app/preload.js`, `desktop_app/src/types/window.d.ts`

**Files**

- Modify: `desktop_app/main.js`
- Modify: `desktop_app/preload.js`
- Modify: `desktop_app/main.test.js` if main-process coverage already exists for these handlers
- Modify: `desktop_app/src/pages/RecordsPage.tsx`
- Modify: `desktop_app/src/pages/RecordsPage.test.tsx`
- Modify: `desktop_app/src/pages/SettingsPage.tsx` only if the same actions are surfaced there
- Modify: `desktop_app/src/pages/SettingsPage.test.tsx` only if the same actions are surfaced there
- Modify: `desktop_app/src/types/window.d.ts` if bridge return types change

### Intent

A valid path should open or reveal successfully. An invalid or missing path should raise user-visible feedback rather than silently doing nothing.

### TDD Steps

- [x] Add failing renderer tests that cover a failed `openPath` or `showItemInFolder` invocation and assert a visible error message.
- [x] Add failing Electron-side tests if needed to pin the IPC return contract for invalid paths.
- [x] Run the targeted tests and confirm failure.
- [x] Implement the minimal contract change:
  - validate the incoming path in the main process
  - return structured success/error information or a normalized error string
  - surface that result in the renderer with explicit feedback
- [x] Re-run the targeted tests and confirm pass.
- [x] Commit scope stayed within the affected Electron and page files; preload/type files were not needed because the string-return contract remained compatible.

### Verification

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/codex-desktop-frontend-restructure/desktop_app
npx vitest run src/pages/RecordsPage.test.tsx src/pages/SettingsPage.test.tsx
node --test main.test.js
```

### Commit

```bash
git add desktop_app/main.js desktop_app/preload.js desktop_app/main.test.js desktop_app/src/pages/RecordsPage.tsx desktop_app/src/pages/RecordsPage.test.tsx desktop_app/src/pages/SettingsPage.tsx desktop_app/src/pages/SettingsPage.test.tsx desktop_app/src/types/window.d.ts
git commit -m "fix: surface desktop file action failures"
```

---

## Task 3: Fix Batch Mapping Save Semantics and Messaging [completed 2026-03-30]

Completed in `codex/desktop-frontend-restructure`.

- Actual files changed: `desktop_app/src/features/mappings/flows.ts`, `desktop_app/src/pages/MappingsPage.test.tsx`
- Not needed: `desktop_app/src/pages/MappingsPage.tsx`, `desktop_app/src/features/mappings/model.ts`

**Files**

- Modify: `desktop_app/src/pages/MappingsPage.tsx`
- Modify: `desktop_app/src/pages/MappingsPage.test.tsx`
- Modify: `desktop_app/src/features/mappings/flows.ts`
- Modify: `desktop_app/src/features/mappings/model.ts` only if the result type needs extra summary fields

### Intent

Preserve valid deduplication if it is intentional, but make batch-save behavior explicit and verifiable:

- distinct rules must all persist
- identical rules may collapse into one saved rule
- the UI must report both “saved rule count” and “covered pending-item count”; the implemented summary now always reports both when coverage is non-zero

### TDD Steps

- [x] Add a failing test for multiple distinct drafts: all distinct rules must be persisted.
- [x] Add a failing test for multiple identical drafts: one rule may persist, but all corresponding pending items must be marked handled and the feedback must distinguish rule count from item count.
- [x] Run the targeted test file and confirm failure.
- [x] Implement the smallest change necessary:
  - keep or refine dedupe semantics only if tests justify it
  - return enough summary information from the flow for the page to communicate the outcome unambiguously
- [x] Re-run the targeted test file and confirm pass.
- [x] Commit scope stayed within the mapping flow/test files; the page component already consumed the updated summary without additional UI changes.

### Verification

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/codex-desktop-frontend-restructure/desktop_app
npx vitest run src/pages/MappingsPage.test.tsx
```

### Commit

```bash
git add desktop_app/src/pages/MappingsPage.tsx desktop_app/src/pages/MappingsPage.test.tsx desktop_app/src/features/mappings/flows.ts desktop_app/src/features/mappings/model.ts
git commit -m "fix: clarify batch mapping save results"
```

---

## Task 4: Make Rule Filtering Terminal Before Mapping

**Files**

- Modify: `peap/streaming_postprocess.py`
- Modify: `tests/test_streaming_ingest.py`
- Modify: `tests/test_app_service.py`
- Modify: `peap_postprocess/postprocess_engine/rules/builtin.py` only if the scrap predicate needs extraction into a shared helper
- Modify: `peap_postprocess/ppe_config/postprocess.yaml` only if configuration cleanup is strictly required after the pipeline change

### Intent

Rows that satisfy a rule-driven exclusion condition must never enter mapping analysis or the pending-mapping queue. In practical terms, the scrap/disposal rule is the current failing fixture, but the fix should be generic: postprocess rules run first, mapping runs last, and any `filter_out_row` result must terminate the ingest path as `skipped`.

### TDD Steps

- [ ] Add a failing ingest test that proves a rule-filtered scrap/disposal row does not produce `pending_mapping`.
- [ ] Add a failing app-service or state-machine test that proves the row lands in a skipped-style outcome rather than a generic filtered finding only.
- [ ] Run the targeted backend tests and confirm failure.
- [ ] Implement the smallest correct pipeline change:
  - reorder or split `run_record_postprocess()` so rule execution happens before mapping enrichment
  - make `filter_out_row` a terminal outcome that survives into ingest classification as `skipped`
  - keep the solution generic for any filter-style rule; scrap/disposal is the current regression fixture, not a one-off hardcoded branch
  - extract shared helpers only if the refactor truly needs them
- [ ] Re-run the targeted backend tests and confirm pass.
- [ ] Commit only the postprocess and test files touched by this task.

### Verification

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/codex-desktop-frontend-restructure
uv run pytest tests/test_streaming_ingest.py tests/test_app_service.py -q
```

### Commit

```bash
git add peap/streaming_postprocess.py tests/test_streaming_ingest.py tests/test_app_service.py peap_postprocess/postprocess_engine/rules/builtin.py peap_postprocess/ppe_config/postprocess.yaml
git commit -m "refactor: make rule filtering terminal before mapping"
```

---

## Task 5: Migrate Shanghai/SUAEE Downloader and Parser

**Files**

- Modify: `peap/downloaders/sse_physical.py`
- Modify: `peap_parsers/shanghai_standard.py`
- Modify: `tests/test_exchange_downloader_fixes.py`
- Modify: `tests/test_download_oneclick.py`
- Modify: `tests/test_streaming_daily_pipeline.py`
- Modify: `tests/test_parsing_contract.py`
- Modify: `tests/test_streaming_ingest.py` only if parsing fixtures or contracts surface there
- Add fixtures only if necessary and keep them tightly scoped to the new SUAEE contract

### Intent

Replace the legacy Shanghai physical-asset acquisition path with one that matches the current official SUAEE site contract, while keeping the layer boundary clean: the downloader owns live `/si` API acquisition and current detail URL construction, and the parser consumes the persisted local artifact produced by that acquisition. Do not turn the parser into a second network client.

### Required Evidence To Preserve In The Implementation Notes

- The old `queryAllNew` endpoint is no longer usable and returns `404`.
- The current site config sets `window.BASE_API_URL = '/si'`, so the active contract lives under the `/si` API namespace.
- Current list retrieval is under `realright/list`.
- Current public detail routing for physical assets is `/xmzx.html#/zczrDetail?XMID=...`.
- Current detail retrieval is under `realright/detail_zspl`.
- The list response now carries `XMID` and `XMBH`; detail acquisition must be keyed by `XMID`, not by the list row's internal `ID`.
- Current detail rendering no longer exposes the legacy `.project_*` CSS hooks relied on by `shanghai_standard.py`.

### TDD Steps

- [ ] Update or add failing downloader tests that encode the new list/detail contract instead of the old `404` fallback path:
  - `/si/prjs/realright/list` replaces `queryAllNew`
  - candidate construction preserves `XMID`
  - current physical-asset detail URLs resolve to `/xmzx.html#/zczrDetail?XMID=...`
- [ ] Update or add failing parser tests against a captured current rendered detail snapshot, or an equivalent tightly scoped fixture derived from the current contract, rather than blessing a new set of brittle `.project_*` replacements.
- [ ] Run the targeted downloader/parser tests and confirm failure.
- [ ] Implement the smallest migration that restores end-to-end correctness:
  - move list fetching to the current API
  - carry `XMID` through candidate generation and current detail resolution
  - keep the downloader responsible for live API knowledge and local snapshot persistence; do not make the parser fetch live detail JSON itself
  - update detail extraction so it no longer depends on vanished legacy classes and instead reads stable rendered content or embedded structured data preserved with the snapshot
- [ ] Re-run the targeted downloader/parser tests and confirm pass.
- [ ] Commit only the downloader/parser files and the tests or fixtures needed to support them.

### Verification

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/codex-desktop-frontend-restructure
uv run pytest tests/test_exchange_downloader_fixes.py tests/test_download_oneclick.py tests/test_streaming_daily_pipeline.py -q
uv run pytest tests/test_parsing_contract.py tests/test_streaming_ingest.py -q
```

### Commit

```bash
git add peap/downloaders/sse_physical.py peap_parsers/shanghai_standard.py tests/test_exchange_downloader_fixes.py tests/test_download_oneclick.py tests/test_streaming_daily_pipeline.py tests/test_parsing_contract.py tests/test_streaming_ingest.py
git commit -m "fix: migrate shanghai physical asset ingestion contract"
```

---

## Full Verification Gate Before Hand-off Closure

After all five tasks are complete, run the full required verification suite. Do not replace these runs with “should pass”.

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/codex-desktop-frontend-restructure/desktop_app
npm test
npx vitest run
npm run build

cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/codex-desktop-frontend-restructure
uv run python scripts/check_release_gate.py
```

If Task 5 materially changes live-download contract assumptions, it is also appropriate to re-run the exchange-focused backend tests used during implementation so the final closure does not depend solely on the release gate.

---

## Handoff Notes For The Next AI

- Do not reinterpret the five issues as a reason to reopen the desktop product direction.
- Task 3 should begin with test evidence before changing deduplication semantics; the bug report currently supports an ambiguity diagnosis more strongly than a confirmed persistence bug.
- Task 4 is the one place where product intent and pipeline mechanics are clearly misaligned; fix it by making rule filtering terminal and mapping the last postprocess phase, not by adding a scrap-only precheck.
- Task 5 must delete or rewrite tests that currently normalize the old Shanghai `404` behavior as expected output. Keeping those tests untouched will produce a false sense of correctness.
- Task 5 should be executed as an acquisition-contract migration plus parser adaptation, not as a selector-hunting patch. Keep `/si` API knowledge in the downloader layer and keep the parser as an offline consumer of persisted artifacts.
- Keep each task commit-scoped and independently reviewable.
