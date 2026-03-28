# Desktop Frontend Framework Replacement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the imperative desktop renderer with a React + TypeScript + Vite renderer that keeps the Electron shell and existing desktop backend semantics intact.

**Architecture:** Keep `main.js`, `preload.js`, backend lifecycle, and backend HTTP endpoints as the stable shell. Introduce a built React renderer under `desktop_app/src/` with a thin typed desktop adapter, page-level feature modules, and stable smoke selectors. Execute in two phases: local controller lands the scaffold and shared contracts, then disjoint page and smoke slices are implemented in parallel and integrated back into the shell.

**Tech Stack:** Electron, React, TypeScript, Vite, Refine, Ant Design, Vitest, Testing Library, Node test, existing desktop backend APIs

---

### Task 1: Bootstrap the React/Vite renderer shell

**Files:**
- Modify: `desktop_app/package.json`
- Modify: `desktop_app/index.html`
- Modify: `desktop_app/main.js`
- Modify: `desktop_app/electron-builder.yml`
- Create: `desktop_app/vite.config.ts`
- Create: `desktop_app/tsconfig.json`
- Create: `desktop_app/tsconfig.node.json`
- Create: `desktop_app/src/main.tsx`
- Create: `desktop_app/src/App.tsx`
- Create: `desktop_app/src/app-shell.tsx`
- Create: `desktop_app/src/styles/app.css`
- Create: `desktop_app/src/testing/selectors.ts`
- Modify: `desktop_app/main.test.js`
- Modify: `desktop_app/package_desktop.test.js`
- Modify: `desktop_app/packaging_manifest.test.js`

- [ ] **Step 1: Write failing Electron/package tests that expect the window to load the built renderer entry and packaging to include Vite assets instead of `renderer.js` as the sole UI asset**
- [ ] **Step 2: Run `node --test ./main.test.js ./package_desktop.test.js ./packaging_manifest.test.js` and verify red**
- [ ] **Step 3: Add Vite/React/TypeScript config, renderer entry files, and a minimal application shell with stable `data-testid` selectors**
- [ ] **Step 4: Update Electron startup and packaging rules so dev mode loads the Vite build output and packaged mode ships the built renderer assets**
- [ ] **Step 5: Re-run `node --test ./main.test.js ./package_desktop.test.js ./packaging_manifest.test.js` and verify green**

### Task 2: Build the shared desktop adapter and frontend contract layer

**Files:**
- Create: `desktop_app/src/desktop/config.ts`
- Create: `desktop_app/src/desktop/http.ts`
- Create: `desktop_app/src/desktop/contracts.ts`
- Create: `desktop_app/src/desktop/queries.ts`
- Create: `desktop_app/src/desktop/commands.ts`
- Create: `desktop_app/src/desktop/errors.ts`
- Create: `desktop_app/src/desktop/provider.tsx`
- Create: `desktop_app/src/lib/formatters.ts`
- Create: `desktop_app/src/lib/polling.ts`
- Create: `desktop_app/src/types/window.d.ts`
- Create: `desktop_app/src/desktop/contracts.test.ts`
- Create: `desktop_app/src/desktop/http.test.ts`

- [ ] **Step 1: Write failing adapter tests for backend config loading, token/header injection, normalized error mapping, pagination query building, and command wrappers for overview/jobs/records/mappings/exports**
- [ ] **Step 2: Run `npx vitest run src/desktop/contracts.test.ts src/desktop/http.test.ts` and verify red**
- [ ] **Step 3: Implement the typed backend config loader, HTTP wrapper, normalized contracts, and shared provider hooks without page logic**
- [ ] **Step 4: Re-run `npx vitest run src/desktop/contracts.test.ts src/desktop/http.test.ts` and verify green**

### Task 3: Implement `OverviewPage` and `TasksPage`

**Files:**
- Create: `desktop_app/src/pages/OverviewPage.tsx`
- Create: `desktop_app/src/pages/TasksPage.tsx`
- Create: `desktop_app/src/features/overview/actions.ts`
- Create: `desktop_app/src/features/overview/components.tsx`
- Create: `desktop_app/src/features/tasks/components.tsx`
- Create: `desktop_app/src/pages/OverviewPage.test.tsx`
- Create: `desktop_app/src/pages/TasksPage.test.tsx`

- [ ] **Step 1: Write failing React tests for overview action cards, runtime status, progress snapshot rendering, job list rendering, and event-stream selection behavior**
- [ ] **Step 2: Run `npx vitest run src/pages/OverviewPage.test.tsx src/pages/TasksPage.test.tsx` and verify red**
- [ ] **Step 3: Implement the overview/tasks pages against the shared adapter and shared shell selectors only**
- [ ] **Step 4: Re-run `npx vitest run src/pages/OverviewPage.test.tsx src/pages/TasksPage.test.tsx` and verify green**

### Task 4: Implement `RecordsPage`, `MappingsPage`, and `SettingsPage`

**Files:**
- Create: `desktop_app/src/pages/RecordsPage.tsx`
- Create: `desktop_app/src/pages/MappingsPage.tsx`
- Create: `desktop_app/src/pages/SettingsPage.tsx`
- Create: `desktop_app/src/features/records/components.tsx`
- Create: `desktop_app/src/features/mappings/components.tsx`
- Create: `desktop_app/src/features/settings/components.tsx`
- Create: `desktop_app/src/pages/RecordsPage.test.tsx`
- Create: `desktop_app/src/pages/MappingsPage.test.tsx`
- Create: `desktop_app/src/pages/SettingsPage.test.tsx`

- [ ] **Step 1: Write failing React tests for records filters/pagination, mappings draft import-save-reprocess flows, and settings runtime/file actions**
- [ ] **Step 2: Run `npx vitest run src/pages/RecordsPage.test.tsx src/pages/MappingsPage.test.tsx src/pages/SettingsPage.test.tsx` and verify red**
- [ ] **Step 3: Implement the three pages using only the shared adapter, product-level error copy, and stable selectors**
- [ ] **Step 4: Re-run `npx vitest run src/pages/RecordsPage.test.tsx src/pages/MappingsPage.test.tsx src/pages/SettingsPage.test.tsx` and verify green**

### Task 5: Migrate smoke hooks and renderer-facing integration tests

**Files:**
- Modify: `desktop_app/smoke_driver.js`
- Modify: `desktop_app/smoke_driver.test.js`
- Modify: `desktop_app/layout_contract.test.js`
- Create: `desktop_app/src/testing/smoke-bridge.ts`
- Create: `desktop_app/src/testing/smoke-selectors.test.ts`

- [ ] **Step 1: Write failing smoke-facing tests that use the new `data-testid` hooks instead of legacy DOM ids while preserving the same smoke path**
- [ ] **Step 2: Run `node --test ./smoke_driver.test.js ./layout_contract.test.js` and `npx vitest run src/testing/smoke-selectors.test.ts` and verify red**
- [ ] **Step 3: Update smoke orchestration and the renderer bootstrap bridge so Electron smoke can drive the React UI without relying on implementation DOM structure**
- [ ] **Step 4: Re-run `node --test ./smoke_driver.test.js ./layout_contract.test.js` and `npx vitest run src/testing/smoke-selectors.test.ts` and verify green**

### Task 6: Integrate all pages into the Electron shell and verify the cutover

**Files:**
- Modify: `desktop_app/src/App.tsx`
- Modify: `desktop_app/src/app-shell.tsx`
- Modify: `desktop_app/package.json`
- Modify: `desktop_app/main.js`
- Modify: `desktop_app/electron-builder.yml`

- [ ] **Step 1: Wire the five pages into the shell navigation, polling lifecycle, and preload-backed side effects**
- [ ] **Step 2: Run `npm test` and `npx vitest run` to verify both Node-side and React-side suites stay green**
- [ ] **Step 3: Run `npm run build` and verify the Vite renderer output is emitted and packaged assets resolve**
- [ ] **Step 4: Run the desktop smoke flow on the new renderer and confirm the report closes the same `manual-import -> mappings -> export -> interrupt/restart` path**

## Parallelization Notes

- The controller implements Task 1 and Task 2 first because every later task depends on the scaffold and adapter contracts.
- After Task 2 lands, dispatch three parallel workers with disjoint write sets:
  - Worker A owns only Task 3 files.
  - Worker B owns only Task 4 files.
  - Worker C owns only Task 5 files.
- The controller alone owns Task 6 integration files and conflict resolution.
- Review and merge workers one task at a time after their targeted tests pass in their forked workspaces.

## Verification Sequence

- Scaffold gate: `node --test ./main.test.js ./package_desktop.test.js ./packaging_manifest.test.js`
- Adapter gate: `npx vitest run src/desktop/contracts.test.ts src/desktop/http.test.ts`
- Page gates: `npx vitest run src/pages/OverviewPage.test.tsx src/pages/TasksPage.test.tsx src/pages/RecordsPage.test.tsx src/pages/MappingsPage.test.tsx src/pages/SettingsPage.test.tsx`
- Smoke gate: `node --test ./smoke_driver.test.js ./layout_contract.test.js && npx vitest run src/testing/smoke-selectors.test.ts`
- Final gate: `npm test && npx vitest run && npm run build`
