# Desktop Frontend Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reframe the desktop app from a backend-object console into a workflow-first desktop product, with top-level navigation, settings, records, and mappings organized around user goals rather than internal job types.

**Architecture:** Replace the current five-peer-page shell with a three-goal shell (`workbench`, `records`, `mappings`) plus low-frequency settings access, then progressively refactor each page into a workflow surface that keeps the next action and current context visible. Preserve existing backend business logic where possible, but extend the Electron bridge and settings API where the current frontend cannot support platform-native file and directory selection.

**Tech Stack:** Electron, React, TypeScript, Ant Design, Refine shell, Vitest, Node test runner, Python backend (`desktop_backend`), Electron preload/main IPC bridge.

---

## Assumptions

- This plan targets the React/Electron desktop shell under `desktop_app/`, not the legacy DOM renderer in `desktop_app/renderer.js`.
- The redesign keeps the current backend job and record model intact unless a frontend workflow requires a small contract extension.
- The design intent is already decided: `Tasks` leaves top-level navigation, `Settings` becomes low-frequency, `Mappings` becomes a remediation workspace, and path editing moves to system pickers.
- `/private/tmp` probe reports are not durable evidence. Treat repository tests plus fresh verification as the source of truth.

## File Structure

**Shell and navigation**
- Create: `desktop_app/src/features/shell/navigation.ts`
- Modify: `desktop_app/src/App.tsx`
- Modify: `desktop_app/src/app-shell.tsx`
- Modify: `desktop_app/src/desktop/contracts.ts`
- Modify: `desktop_app/src/testing/selectors.ts`
- Test: `desktop_app/src/desktop/contracts.test.ts`
- Test: `desktop_app/src/testing/smoke-selectors.test.ts`
- Test: `desktop_app/layout_contract.test.js`

**Workbench**
- Create: `desktop_app/src/pages/WorkbenchPage.tsx`
- Create: `desktop_app/src/pages/WorkbenchPage.test.tsx`
- Create: `desktop_app/src/features/workbench/useWorkbench.ts`
- Create: `desktop_app/src/features/tasks/TaskActivityPanel.tsx`
- Create: `desktop_app/src/features/tasks/TaskActivityPanel.test.tsx`
- Modify: `desktop_app/src/pages/OverviewPage.tsx`
- Modify: `desktop_app/src/pages/OverviewPage.test.tsx`
- Modify: `desktop_app/src/pages/TasksPage.tsx`
- Modify: `desktop_app/src/pages/TasksPage.test.tsx`
- Modify: `desktop_app/src/features/tasks/formatters.ts`

**Settings and file-system interactions**
- Create: `desktop_app/src/features/settings/PathSettingField.tsx`
- Create: `desktop_app/src/features/settings/PathSettingField.test.tsx`
- Modify: `desktop_app/src/pages/SettingsPage.tsx`
- Modify: `desktop_app/src/pages/SettingsPage.test.tsx`
- Modify: `desktop_app/src/features/settings/api.ts`
- Modify: `desktop_app/preload.js`
- Modify: `desktop_app/main.js`
- Modify: `desktop_app/main.test.js`
- Modify: `desktop_app/src/types/window.d.ts`
- Modify: `desktop_backend/app_service.py`
- Modify: `desktop_backend/app_backend.py`
- Test: `tests/test_app_service.py`

**Records**
- Create: `desktop_app/src/features/records/RecordStatusTag.tsx`
- Create: `desktop_app/src/features/records/RecordDetailPanel.tsx`
- Create: `desktop_app/src/features/records/RecordDetailPanel.test.tsx`
- Modify: `desktop_app/src/pages/RecordsPage.tsx`
- Modify: `desktop_app/src/pages/RecordsPage.test.tsx`
- Modify: `desktop_app/src/features/records/table.ts`
- Modify: `desktop_app/src/features/records/summary.ts`
- Modify: `desktop_app/src/features/records/scope.ts`

**Mappings**
- Create: `desktop_app/src/features/mappings/PendingMappingsPane.tsx`
- Create: `desktop_app/src/features/mappings/RuleEditorPane.tsx`
- Create: `desktop_app/src/features/mappings/SavedRulesPane.tsx`
- Create: `desktop_app/src/features/mappings/PendingMappingsPane.test.tsx`
- Create: `desktop_app/src/features/mappings/RuleEditorPane.test.tsx`
- Modify: `desktop_app/src/pages/MappingsPage.tsx`
- Modify: `desktop_app/src/pages/MappingsPage.test.tsx`
- Modify: `desktop_app/src/features/mappings/model.ts`
- Modify: `desktop_app/src/features/mappings/flows.ts`

**Visual system and verification**
- Modify: `desktop_app/src/styles/app.css`
- Modify: `desktop_app/smoke_driver.js`
- Modify: `desktop_app/smoke_driver.test.js`
- Modify: `scripts/check_release_gate.py` only if the required smoke contract must be updated to the new shell

### Task 1: Freeze Shell Information Architecture

**Files:**
- Create: `desktop_app/src/features/shell/navigation.ts`
- Modify: `desktop_app/src/App.tsx`
- Modify: `desktop_app/src/app-shell.tsx`
- Modify: `desktop_app/src/desktop/contracts.ts`
- Modify: `desktop_app/src/testing/selectors.ts`
- Test: `desktop_app/src/desktop/contracts.test.ts`
- Test: `desktop_app/src/testing/smoke-selectors.test.ts`
- Test: `desktop_app/layout_contract.test.js`

- [ ] **Step 1: Write failing tests for the new top-level IA**

Add tests asserting:
- top-level destinations are `workbench`, `records`, `mappings`
- settings is present but not modeled as a peer primary destination
- smoke selector contract still exposes stable selectors for the three primary workflow views

- [ ] **Step 2: Run the IA tests to verify they fail against the current five-item shell**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/desktop_app
npx vitest run src/desktop/contracts.test.ts src/testing/smoke-selectors.test.ts
node --test ../desktop_app/layout_contract.test.js
```

Expected: failures mentioning `tasks`/`overview` still being primary shell destinations.

- [ ] **Step 3: Implement the shell contract refactor**

Add a shared navigation model in `src/features/shell/navigation.ts`, update `App.tsx` and `app-shell.tsx` to render the new destination set, and update selector contracts without yet deleting old page modules.

- [ ] **Step 4: Re-run the targeted IA tests**

Run the same commands from Step 2.

Expected: all targeted IA tests pass.

- [ ] **Step 5: Commit**

```bash
git add desktop_app/src/features/shell/navigation.ts desktop_app/src/App.tsx desktop_app/src/app-shell.tsx desktop_app/src/desktop/contracts.ts desktop_app/src/testing/selectors.ts desktop_app/src/desktop/contracts.test.ts desktop_app/src/testing/smoke-selectors.test.ts desktop_app/layout_contract.test.js
git commit -m "refactor: simplify desktop shell navigation"
```

### Task 2: Build a Workflow-First Workbench Page

**Files:**
- Create: `desktop_app/src/pages/WorkbenchPage.tsx`
- Create: `desktop_app/src/pages/WorkbenchPage.test.tsx`
- Create: `desktop_app/src/features/workbench/useWorkbench.ts`
- Create: `desktop_app/src/features/tasks/TaskActivityPanel.tsx`
- Create: `desktop_app/src/features/tasks/TaskActivityPanel.test.tsx`
- Modify: `desktop_app/src/App.tsx`
- Modify: `desktop_app/src/pages/OverviewPage.tsx`
- Modify: `desktop_app/src/pages/OverviewPage.test.tsx`
- Modify: `desktop_app/src/pages/TasksPage.tsx`
- Modify: `desktop_app/src/pages/TasksPage.test.tsx`
- Modify: `desktop_app/src/features/tasks/formatters.ts`

- [ ] **Step 1: Write failing workbench tests**

Cover these behaviors:
- primary actions stay on the workbench
- current task/activity appears inline on the same page
- `TasksPage` is no longer required for routine monitoring
- task history remains reachable as a secondary surface, not a primary destination

- [ ] **Step 2: Run the workbench tests and confirm failure**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/desktop_app
npx vitest run src/pages/WorkbenchPage.test.tsx src/pages/OverviewPage.test.tsx src/pages/TasksPage.test.tsx src/features/tasks/TaskActivityPanel.test.tsx
```

Expected: `WorkbenchPage` missing and existing overview/tasks assertions mismatched.

- [ ] **Step 3: Implement `WorkbenchPage` and move activity into it**

Compose the current overview primary actions, runtime readiness, pending mapping count, and an inline activity panel into one page. Keep tasks history available via drawer, section, or inline expandable list, but remove the assumption that it is a first-class destination.

- [ ] **Step 4: Re-run the workbench test group**

Expected: all new and adapted page tests pass.

- [ ] **Step 5: Commit**

```bash
git add desktop_app/src/pages/WorkbenchPage.tsx desktop_app/src/pages/WorkbenchPage.test.tsx desktop_app/src/features/workbench/useWorkbench.ts desktop_app/src/features/tasks/TaskActivityPanel.tsx desktop_app/src/features/tasks/TaskActivityPanel.test.tsx desktop_app/src/App.tsx desktop_app/src/pages/OverviewPage.tsx desktop_app/src/pages/OverviewPage.test.tsx desktop_app/src/pages/TasksPage.tsx desktop_app/src/pages/TasksPage.test.tsx desktop_app/src/features/tasks/formatters.ts
git commit -m "feat: add workflow-first workbench"
```

### Task 3: Rebuild Settings Around Native Pickers and Low-Frequency Preferences

**Files:**
- Create: `desktop_app/src/features/settings/PathSettingField.tsx`
- Create: `desktop_app/src/features/settings/PathSettingField.test.tsx`
- Modify: `desktop_app/src/pages/SettingsPage.tsx`
- Modify: `desktop_app/src/pages/SettingsPage.test.tsx`
- Modify: `desktop_app/src/features/settings/api.ts`
- Modify: `desktop_app/preload.js`
- Modify: `desktop_app/main.js`
- Modify: `desktop_app/main.test.js`
- Modify: `desktop_app/src/types/window.d.ts`
- Modify: `desktop_backend/app_service.py`
- Modify: `desktop_backend/app_backend.py`
- Test: `tests/test_app_service.py`

- [ ] **Step 1: Write failing tests for grouped settings and picker-driven editing**

Cover:
- settings sections are grouped into defaults, locations, runtime, and maintenance
- editable path rows expose a native `选择…` action plus a contextual `在系统中显示`
- `postprocess_config` uses file selection rather than raw text entry
- backend save endpoints accept the new editable settings fields that should truly be user-configurable

- [ ] **Step 2: Run the settings frontend and backend tests to verify failure**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/desktop_app
npx vitest run src/pages/SettingsPage.test.tsx src/features/settings/api.test.ts src/features/settings/form.test.ts src/features/settings/PathSettingField.test.tsx
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform
uv run pytest tests/test_app_service.py -q -k settings
```

Expected: failures because only `save_json` and `postprocess_config` are writable and no file picker exists.

- [ ] **Step 3: Implement picker-capable settings rows and minimal backend contract expansion**

Add a reusable path field component, wire `pickDirectory` and new `pickFile` IPC handlers through preload/main, and expand backend settings persistence only for fields that are intended to be user-editable. Keep immutable derived paths visibly read-only.

- [ ] **Step 4: Re-run settings tests**

Expected: frontend and backend settings tests pass.

- [ ] **Step 5: Commit**

```bash
git add desktop_app/src/features/settings/PathSettingField.tsx desktop_app/src/features/settings/PathSettingField.test.tsx desktop_app/src/pages/SettingsPage.tsx desktop_app/src/pages/SettingsPage.test.tsx desktop_app/src/features/settings/api.ts desktop_app/preload.js desktop_app/main.js desktop_app/main.test.js desktop_app/src/types/window.d.ts desktop_backend/app_service.py desktop_backend/app_backend.py tests/test_app_service.py
git commit -m "refactor: rebuild settings around native pickers"
```

### Task 4: Turn Records into a List-Detail Workspace

**Files:**
- Create: `desktop_app/src/features/records/RecordStatusTag.tsx`
- Create: `desktop_app/src/features/records/RecordDetailPanel.tsx`
- Create: `desktop_app/src/features/records/RecordDetailPanel.test.tsx`
- Modify: `desktop_app/src/pages/RecordsPage.tsx`
- Modify: `desktop_app/src/pages/RecordsPage.test.tsx`
- Modify: `desktop_app/src/features/records/table.ts`
- Modify: `desktop_app/src/features/records/summary.ts`
- Modify: `desktop_app/src/features/records/scope.ts`

- [ ] **Step 1: Write failing tests for the new records interaction model**

Cover:
- user-facing statuses collapse into a smaller decision-oriented vocabulary
- row selection drives a detail panel
- file actions are labeled with platform-native semantics such as `打开文件` and `在文件夹中显示`
- filters remain functional for keyword, state, project type, date range, and pagination

- [ ] **Step 2: Run the records tests to confirm failure**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/desktop_app
npx vitest run src/pages/RecordsPage.test.tsx src/features/records/RecordDetailPanel.test.tsx
```

Expected: failures because the current page is a flat table with raw pipeline status labels.

- [ ] **Step 3: Implement the list-detail records workspace**

Keep the existing query model, but reorganize layout so filters are compact, the table is the browsing surface, and a detail panel owns secondary metadata and actions. Add a compact visual status component for quick recognition.

- [ ] **Step 4: Re-run records tests**

Expected: all targeted records tests pass.

- [ ] **Step 5: Commit**

```bash
git add desktop_app/src/features/records/RecordStatusTag.tsx desktop_app/src/features/records/RecordDetailPanel.tsx desktop_app/src/features/records/RecordDetailPanel.test.tsx desktop_app/src/pages/RecordsPage.tsx desktop_app/src/pages/RecordsPage.test.tsx desktop_app/src/features/records/table.ts desktop_app/src/features/records/summary.ts desktop_app/src/features/records/scope.ts
git commit -m "refactor: turn records into a list-detail workspace"
```

### Task 5: Turn Mappings into a Remediation Workspace

**Files:**
- Create: `desktop_app/src/features/mappings/PendingMappingsPane.tsx`
- Create: `desktop_app/src/features/mappings/RuleEditorPane.tsx`
- Create: `desktop_app/src/features/mappings/SavedRulesPane.tsx`
- Create: `desktop_app/src/features/mappings/PendingMappingsPane.test.tsx`
- Create: `desktop_app/src/features/mappings/RuleEditorPane.test.tsx`
- Modify: `desktop_app/src/pages/MappingsPage.tsx`
- Modify: `desktop_app/src/pages/MappingsPage.test.tsx`
- Modify: `desktop_app/src/features/mappings/model.ts`
- Modify: `desktop_app/src/features/mappings/flows.ts`

- [ ] **Step 1: Write failing tests for the remediation-first layout**

Cover:
- pending queue and active editor are simultaneously visible above the fold
- importing a pending item keeps editing context in view
- saved rules are demoted to a secondary region and do not push active remediation out of the first viewport
- conflict confirmation still works

- [ ] **Step 2: Run mappings tests and verify failure**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/desktop_app
npx vitest run src/pages/MappingsPage.test.tsx src/features/mappings/PendingMappingsPane.test.tsx src/features/mappings/RuleEditorPane.test.tsx
```

Expected: failures because the current page is a long vertical stack.

- [ ] **Step 3: Implement the remediation workspace layout**

Split pending queue, active editor, and saved rules into distinct panes. Keep preview and batch-result messaging inline with the action area instead of occupying a dedicated standalone card.

- [ ] **Step 4: Re-run mappings tests**

Expected: mappings tests pass.

- [ ] **Step 5: Commit**

```bash
git add desktop_app/src/features/mappings/PendingMappingsPane.tsx desktop_app/src/features/mappings/RuleEditorPane.tsx desktop_app/src/features/mappings/SavedRulesPane.tsx desktop_app/src/features/mappings/PendingMappingsPane.test.tsx desktop_app/src/features/mappings/RuleEditorPane.test.tsx desktop_app/src/pages/MappingsPage.tsx desktop_app/src/pages/MappingsPage.test.tsx desktop_app/src/features/mappings/model.ts desktop_app/src/features/mappings/flows.ts
git commit -m "refactor: make mappings a remediation workspace"
```

### Task 6: Unify Status, Copy, and Wait Feedback

**Files:**
- Modify: `desktop_app/src/features/tasks/formatters.ts`
- Modify: `desktop_app/src/features/records/summary.ts`
- Modify: `desktop_app/src/features/records/table.ts`
- Modify: `desktop_app/src/pages/WorkbenchPage.tsx`
- Modify: `desktop_app/src/pages/RecordsPage.tsx`
- Modify: `desktop_app/src/pages/MappingsPage.tsx`
- Modify: `desktop_app/src/pages/OverviewPage.test.tsx`
- Modify: `desktop_app/src/pages/RecordsPage.test.tsx`
- Modify: `desktop_app/src/pages/MappingsPage.test.tsx`

- [ ] **Step 1: Write failing tests for vocabulary and wait-state consistency**

Cover:
- progress labels are short, action-tied, and consistent
- completed, in-progress, blocked, and failed states use the same vocabulary across workbench, records, and mappings
- raw technical backend states do not leak into the first line of UI copy

- [ ] **Step 2: Run the affected page tests**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/desktop_app
npx vitest run src/pages/WorkbenchPage.test.tsx src/pages/OverviewPage.test.tsx src/pages/RecordsPage.test.tsx src/pages/MappingsPage.test.tsx
```

Expected: copy assertions fail before the vocabulary cleanup.

- [ ] **Step 3: Implement the shared vocabulary cleanup**

Update formatter and page copy so the UI communicates user intent and next action instead of backend internals.

- [ ] **Step 4: Re-run the page tests**

Expected: all targeted tests pass.

- [ ] **Step 5: Commit**

```bash
git add desktop_app/src/features/tasks/formatters.ts desktop_app/src/features/records/summary.ts desktop_app/src/features/records/table.ts desktop_app/src/pages/WorkbenchPage.tsx desktop_app/src/pages/RecordsPage.tsx desktop_app/src/pages/MappingsPage.tsx desktop_app/src/pages/OverviewPage.test.tsx desktop_app/src/pages/RecordsPage.test.tsx desktop_app/src/pages/MappingsPage.test.tsx
git commit -m "refactor: unify desktop status and wait feedback"
```

### Task 7: Refresh Smoke Coverage for the New Workflow

**Files:**
- Modify: `desktop_app/smoke_driver.js`
- Modify: `desktop_app/smoke_driver.test.js`
- Modify: `desktop_app/src/testing/selectors.ts`
- Modify: `desktop_app/src/testing/smoke-bridge.ts`
- Modify: `scripts/check_release_gate.py` only if selector or route assumptions must change

- [ ] **Step 1: Write failing smoke-driver tests for the new shell and workflow**

Cover:
- workbench is the new primary destination
- tasks are accessed as a secondary workflow surface
- mappings and records still support the existing real-smoke steps under the new layout

- [ ] **Step 2: Run the smoke-driver tests and confirm failure**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/desktop_app
node --test ./smoke_driver.test.js ./layout_contract.test.js
```

Expected: selector and flow assumptions fail.

- [ ] **Step 3: Update smoke selectors and driver flow**

Keep the end-to-end business chain the same, but teach the smoke driver the new page entry points and action locations.

- [ ] **Step 4: Re-run smoke-driver tests**

Expected: smoke-driver unit tests pass.

- [ ] **Step 5: Commit**

```bash
git add desktop_app/smoke_driver.js desktop_app/smoke_driver.test.js desktop_app/src/testing/selectors.ts desktop_app/src/testing/smoke-bridge.ts scripts/check_release_gate.py
git commit -m "test: align smoke coverage with workflow-first shell"
```

### Task 8: Full Verification and Real Desktop Validation

**Files:**
- Verify only; no new source files unless a regression is found

- [ ] **Step 1: Run the full desktop frontend automated suite**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/desktop_app
npm test
npx vitest run
npm run build
```

Expected: all commands exit `0`.

- [ ] **Step 2: Run backend tests for any settings/API contract changes**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform
uv run pytest tests/test_app_service.py -q
```

Expected: pass with no regressions.

- [ ] **Step 3: Run release gate**

Run:

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform
uv run python scripts/check_release_gate.py
```

Expected: `Overall: PASS`.

- [ ] **Step 4: Run one fresh real Electron workflow smoke**

Use a clean `PEAP_APP_HOME` and verify:
- workbench actions
- mappings remediation
- records export and file actions
- settings runtime path actions

Expected: clean end-to-end completion under the new shell.

- [ ] **Step 5: Commit verification fixes only if needed**

```bash
git add -A
git commit -m "fix: close workflow-first shell regressions"
```

Only commit if verification exposed and fixed an actual regression.
