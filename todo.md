# PEAP Refactor TODO / Handoff

Last updated: 2026-03-23
Owner in this thread: Codex
Required working rule: after every non-trivial code change, update this file before ending the turn.

## 0. Current Handoff Snapshot

This file is older than the current product direction. Read this section first.

### 0.0 Minimal Background And Forced Correction Spec

Minimal background for a fresh window:

- active product: Electron desktop app, not the old CLI shell
- user-approved operator flow: choose date range -> `一键执行` writes archived pages + records into the desktop database -> operator manually triggers Excel export
- desktop should use one visible workspace root under the user's Documents directory; do not split business data across multiple roots
- the CLI is still the semantic reference for dedupe intent, parser coverage, type normalization, mapping logic, and export column contract
- for current work, trust this `0.0` section first; later "completed" bullets are historical and may describe attempts that did not fully land in real validation

This section overrides any earlier "completed" wording below. Real-world validation found several core logic mismatches. They must be fixed before further UI polish is treated as meaningful progress.

#### 0.0.1 Backend Must Be Corrected First

Do not continue adding frontend states or labels on top of the current backend behavior.

The current desktop product still has these wrong ownership boundaries:

- downloader output is still treated as a staging area in practice, and ingest is still responsible for canonical archive placement
- project/business type is still partially inferred by parser/path fallback instead of being owned by the download task itself
- duplicate detection is still weaker than the legacy CLI behavior, so repeated date-range runs can re-download or re-surface already-known projects
- mapping resolution, pending-mapping counts, and record readiness are still not driven from one canonical store-level truth
- task/progress projection is still partly derived from mixed job events instead of a single business ledger

Required backend-first corrections:

1. Direct-to-submission save, no second archive move
   - downloader stage must compute the final canonical `submission/YYYY年M月/...` target path before saving
   - saved HTML and sibling resource directory must land at the final path on first write
   - `peap/streaming_ingest.py` must stop materializing a second canonical copy for normal desktop downloads
   - if a file must be imported from outside the workspace, that is a one-time import materialization, not the normal download path

2. Business type ownership belongs to scan/download stage, not parser
   - `股权` / `实物` / `增资` / `预披露` and similar business classes must be known before detail-page save
   - downloader/listing task metadata must be the source of truth for `project_type`
   - parser may validate or enrich fields, but it must not decide the primary business type for desktop streaming ingest
   - if type cannot be decided at scan/download time, the item must be surfaced as an explicit blocked condition instead of silently entering the database as usable data

3. Duplicate filtering must match the legacy CLI intent
   - before a run executes downloads, it must consult existing database records and existing archived projects by stable project identity
   - repeated runs over the same date range must not re-download known projects unless the operator explicitly requests refresh semantics
   - store-level upsert rules must make repeated ingest idempotent for the same project snapshot
   - weekend / historical ranges must not appear as "新增" just because the downloader rescanned old pages

4. Pending-mapping and ready-state classification must be store-driven
   - records missing required mapped fields must not appear as `已录入`
   - homepage pending-mapping counts, records page status, mapping page pending list, and export eligibility must all come from the same normalized store view
   - saving a rule must trigger a background recalculation over all affected latest records, not just the currently focused row
   - old incorrect rows already in the database must be normalized when the desktop backend starts reading them

5. Manual import must be a first-class parser entrypoint
   - desktop must support recursively importing local `html` / `htm` / `mhtml` files that are not covered by current downloaders
   - manual import must use the same parser + normalization + storage path as downloaded pages
   - imported files must also obey the same final submission layout and dedupe logic

6. Progress must come from real business counters
   - scan stage must report what source is being scanned, such as `北交所 - 挂牌房屋土地`
   - scan, save, parse/store, pending-mapping, skipped, duplicate-skipped, and export counts must be derived from canonical runtime state
   - frontend progress must not lag behind terminal progress because it is reading stale or differently-aggregated counters
   - after completion, the state must freeze as completed instead of lingering in a fake running summary

7. Small-range scan policy should stay bounded by default
   - for non-large date ranges, default scan scope should remain limited, e.g. first 10 pages unless the operator overrides it
   - this policy must be source-aware and must not silently expand into full historical crawling

#### 0.0.2 Frontend Changes That Depend On The Backend Fixes

These UI corrections are required, but they should be implemented after the backend above is actually correct.

1. Task page must be a dedicated left-nav entry
   - do not duplicate task summaries inside `数据记录` or `映射补录`
   - do not keep a right-side floating task strip

2. Records page must behave like a real database viewer
   - it should expose useful filters, stable table layout, and clear status summaries
   - it must show business type, exchange, listing date, and export-relevant fields in a controlled schema
   - unresolved / blocked rows must be visibly blocked, not mixed into normal usable data

3. Mapping page must expose saved rules as a proper table, not a buried search box
   - saved rules should be visible without scrolling to the bottom of the page
   - operators must be able to filter and inspect rule entries directly
   - pending items and saved rules should not fight through nested scroll containers

4. Unknown type or blocked ingest must be surfaced explicitly
   - if a project lands in `未知` or any equivalent unresolved type, the UI must show that as a blocking condition
   - it must not silently pass as a normal record

5. One-click completion should explicitly tell the operator that export is a separate action
   - one-click means ingest + archive
   - export is still an operator-triggered step and should be messaged clearly after completion

#### 0.0.3 Acceptance Criteria Before Calling The Desktop Product "Directly Exportable"

Do not mark the product as ready until all of the following are true:

- downloading writes directly into the final submission structure and does not perform a second canonical archive move
- business type is assigned at scan/download time and does not depend on parser/path inference for correctness
- rerunning the same date range does not create fake "新增" or duplicate downloaded pages
- homepage pending-mapping counts match records page and mapping page counts
- records missing required mapped/business fields never appear as normal ready data
- manual import can ingest unsupported-site local HTML/MHTML into the same database/export path
- progress shown in Electron matches the real backend progress closely enough to be operationally trustworthy
- task information lives in a dedicated left-side page only
- exported Excel columns match the CLI contract and only include export-eligible records

#### 0.0.4 Execution Order

Follow this order strictly:

1. backend storage and ownership correction
2. duplicate / state normalization correction
3. manual import entrypoint alignment
4. progress projection correction
5. frontend page restructuring
6. Electron real-window validation

#### 0.0.5 Expected Change Ownership

Backend ownership:

- `peap/download_oneclick.py`
  - range collection policy, existing-project filtering, source-aware page-limit defaults
- `peap/streaming_daily_pipeline.py`
  - one-click orchestration, scan/download ownership, canonical progress counters
- `peap/streaming_ingest.py`
  - remove normal-download archive materialization, keep only parse/normalize/store responsibilities
- `peap/streaming_store.py`
  - canonical ready/pending/skipped/duplicate state rules and cross-page normalized counts
- `peap/streaming_postprocess.py`
  - reduce to normalization/enrichment rules that run inside ingest, not a separate user-visible stage
- `peap/parsing.py`
  - desktop path must stop depending on path/category inference for business-type correctness
- `desktop_backend/app_service.py`
  - project the normalized store truth into homepage, records, mappings, tasks, and export APIs
- `desktop_backend/app_backend.py`
  - keep API shape aligned with the corrected backend semantics

Frontend ownership:

- `desktop_app/index.html`
  - left-nav information architecture and page layout contracts
- `desktop_app/renderer.js`
  - consume only normalized backend views; stop compensating for backend inconsistencies in the browser
- `desktop_app/styles.css`
  - remove nested-scroll and buried-table layout failures

#### 0.0.6 Still Unresolved In Real Validation

Treat the following as not fixed until revalidated in the real Electron window:

- homepage `待补映射` count has still disagreed with records/mappings in live use
- records page is still not yet a strong database viewer in terms of filters, layout discipline, and status clarity
- saved-rules table visibility/layout has still regressed in real use
- some projects still surface with `项目类型=未知` even though the downloader/source class should have determined type before ingest
- unknown or blocked type is still not surfaced aggressively enough to the operator
- Electron progress has still lagged or disagreed with terminal/backend progress in real runs
- repeated date-range runs have still shown fake `新增`, implying dedupe/cache parity with the CLI is still incomplete
- task information has still leaked into records/mappings instead of living only in a dedicated task page
- browser-runtime detection has produced false negatives in real validation even when Chromium existed locally
- export behavior must still be checked against the exact CLI column contract, not only against "looks plausible" desktop fields

### 0.1 Product Direction

The active product is now the desktop app, not the legacy source-tree CLI.

- Desktop shell: `desktop_app/`
- Desktop product backend: `desktop_backend/`
- Engine/core still used by the app: `peap/`, `peap_parsers/`, `peap_postprocess/`

The desktop app currently depends on the engine/core. Do not delete engine modules just because the old CLI still references them.

### 0.2 What Was Completed In This Thread

Historical attempt log only. Do not treat this section as the current source of truth when it conflicts with `0.0`.

- corrected mapping refresh semantics so new rules are authoritative instead of fill-only:
  - `peap/streaming_postprocess.py` now allows matching `group -> source_type` and related rules to overwrite previously stored type values when the rule matches
  - this fixes the real operator case where `华润 -> 央企` must update other latest `华润` records instead of only blank rows
- added mapping preview / overwrite confirmation and batch pending refresh APIs:
  - `desktop_backend/app_service.py` now exposes `preview_mapping_upsert(...)` with `create/update/overwrite` mode detection, existing-rule lookup, and affected-record counts
  - `desktop_backend/app_service.py` also exposes `launch_pending_mapping_refresh(...)` for all current `pending_mapping` latest records
  - `desktop_backend/app_backend.py` now serves `POST /api/mappings/preview` and `POST /api/mappings/reprocess-pending`
- upgraded the mappings UI to match the approved operator flow:
  - `desktop_app/index.html` and `desktop_app/styles.css` now keep batch reprocess inside `映射补录`, not on the homepage
  - the mappings page now exposes a dedicated `一键重处理当前所有待补项` action with visible pending-count feedback
  - saving a rule now performs preview first and shows an explicit overwrite warning dialog before replacing an existing rule
  - bulk draft save reuses the same preview/confirm path and skips only the rules whose overwrite was not confirmed
  - bulk draft save now shows immediate in-progress feedback, disables the save button while running, and surfaces the first concrete failure reason instead of silently only logging to the console
- fixed duplicated pending-mapping rows at the store layer:
  - `peap/streaming_store.py` now makes `mark_mapping_pending(...)` idempotent for an open `record_id`
  - existing historical duplicate open rows are now deduped in both `list_pending_mappings(...)` and `count_pending_mappings()`, so left-panel lists and homepage counts stop inflating from legacy duplicate entries
- tightened mapping rule validation and write isolation:
  - `desktop_backend/app_service.py` now rejects blank `source_name`, blank `target_value`, and invalid `match_field` / `target_field` combinations before preview or save
  - `desktop_app/renderer.js` now locally blocks single-rule save when source/target is incomplete instead of sending an empty rule to the backend
  - mapping save, batch pending refresh, and single-record reprocess are now all covered by the backend mutating-job reservation model, so they no longer race with one-click/manual-import/export writes
- restored mapping-refresh execution through the public `reprocess_record(...)` path with thread-local reentry tracking:
  - batch refresh workers now mark the current thread as already holding `mapping_refresh`, so internal reprocess calls remain lock-safe without bypassing the public service boundary
  - this keeps testability and behavior aligned: single-record reprocess still goes through one public path, while batch refresh can reuse it without deadlocking itself
- tightened draft-rule batch UX semantics in the renderer:
  - `desktop_app/renderer/mappings.mjs` now resolves all duplicate draft rows sharing the same deduped rule key when one save succeeds, so repeated pending rows for the same rule do not linger in the draft list after a successful batch save
  - mapping interaction state is now centralized in `isMappingInteractionActive(...)`; while the overwrite dialog is open, mappings polling is treated as paused to avoid background list refreshes fighting with the user’s confirmation flow
- tightened homepage first-screen layout around the real high-frequency actions:
  - `desktop_app/index.html` now groups `一键执行`, `导出 Excel`, and `手动导入解析` inside one `homePrimaryActions` grid
  - `desktop_app/styles.css` now renders that area as a two-column first-screen layout on desktop and collapses cleanly on narrow widths
- added focused renderer-side regression coverage for the new behavior:
  - `desktop_app/renderer/mappings.mjs`
  - `desktop_app/renderer/mappings.test.js`
  - `desktop_app/layout_contract.test.js` now asserts that batch reprocess stays on the mappings page rather than drifting onto the homepage
- removed the legacy public CLI wrapper layer and its public packaging entrypoints so the desktop app is the only supported product surface
- added a desktop-only local API trust boundary:
  - Electron main process now generates a random API token
  - the token is passed into the backend sidecar and exposed to the renderer through preload
  - `desktop_backend/app_backend.py` now rejects non-ready requests without the matching token
- upgraded records from a silently truncated list to a paginated view contract:
  - `desktop_backend/app_service.py` now returns `page`, `page_size`, `total_count`, `page_count`, and `has_more`
  - the records page now exposes previous/next paging and page-size selection
  - summary text now distinguishes total matched rows from the current page size
- split part of the Electron renderer into focused modules:
  - `desktop_app/renderer/api.mjs`
  - `desktop_app/renderer/records.mjs`
  - `desktop_app/renderer/polling.mjs`
  - `desktop_app/renderer/state.mjs`
  - `desktop_app/renderer.js` remains the entrypoint/orchestrator instead of carrying all HTTP, polling, and records responsibilities inline
- added dedicated regression coverage for:
  - local desktop token enforcement
  - readiness probes forwarding token headers
  - records pagination metadata
  - renderer API header injection and records summary formatting
- implemented a streaming ingest path in `peap/`:
  - download/manual-import callback -> queue -> parse -> postprocess -> sqlite -> export
- added sqlite-backed streaming store and export path:
  - `peap/streaming_store.py`
  - `peap/streaming_ingest.py`
  - `peap/streaming_export.py`
  - `peap/streaming_daily_pipeline.py`
- added desktop product backend:
  - `desktop_backend/app_config.py`
  - `desktop_backend/app_service.py`
  - `desktop_backend/app_backend.py`
- added electron shell scaffold and interactive UI:
  - `desktop_app/main.js`
  - `desktop_app/preload.js`
  - `desktop_app/index.html`
  - `desktop_app/renderer.js`
  - `desktop_app/styles.css`
- split app-layer code out of `peap/` to reduce CLI/app confusion
- downgraded `bin/peap.py` and related README language to legacy/internal status
- added isolated desktop dev bootstrap files:
  - `.python-version`
  - `desktop_backend/requirements.lock.txt`
  - `scripts/bootstrap_desktop_env.sh`
- added storage layout doc:
  - `docs/desktop_storage_layout.md`
- added desktop release-build wiring:
  - `desktop_app/backend_launch.js`
  - `desktop_app/build_backend_sidecar.js`
  - `desktop_app/electron-builder.yml`
  - `desktop_backend/requirements.build.lock.txt`
- added native packaging workflow:
  - `.github/workflows/desktop-package.yml`
- refactored Electron main-process backend launch so dev/runtime path selection is explicit and testable
- aligned child-process Playwright env propagation so the backend config path and runtime browser cache path do not drift in desktop mode
- added app-level Playwright runtime management:
  - `desktop_backend/runtime_dependencies.py`
  - `/api/runtime/dependencies`
  - `/api/runtime/install-browser`
  - desktop Settings UI actions for Chromium detect/install
- added product-readiness gating for the desktop shell:
  - backend now exposes `product_readiness` in overview/runtime responses
  - startup overlay warns when Chromium is missing
  - download actions stay disabled until browser runtime is ready
- upgraded browser install flow from synchronous action to background task:
  - `/api/runtime/install-browser` now starts async install and returns task state
  - overview/runtime payloads now include `browser_install`
  - startup gate auto-attempts one Chromium install per app session when missing
- hardened desktop startup failure handling:
  - dev launch now preflights `.venv-desktop` Python existence before spawn
  - missing backend runtime now shows a clear startup error instead of raw `spawn ... ENOENT`
- updated backend sidecar packaging to include Playwright package data / driver binaries required by the bundled install path
- corrected ingest semantics so intentional parser skips are stored and displayed as `skipped` rather than `failed`
- added desktop progress-phase events for prepare / save / export so the UI can show long-running work without exposing downloader internals
- rebuilt the desktop shell toward a business-facing operator flow:
  - brand/title changed to `产权交易所自动录入`
  - overview now uses a single date-range `一键执行` card plus a separate `导出 Excel` card
  - one-click semantics are fixed to “按所选日期范围自动录入数据并保存网页”
  - added a database-backed records view rendered in the same field order as export payloads
  - tightened the mapping form layout and added direct database file access from the UI
- tightened the operator-facing data view and settings usability:
  - records page now supports business-type filtering so different output kinds no longer share one mixed column set by default
  - exchange values in record rows are normalized to user-facing labels like `北交所` / `上交所` / `天交所` / `重交所`
  - progress summary now distinguishes scan/save/archive/export phases and no longer keeps stale running states after a job finishes
  - download now has its own date-range controls; export form layout was reduced to a smaller responsive grid
  - basic settings default exchange / default project type now use selects instead of free-text inputs
  - archive/export paths can now be chosen through Electron directory pickers instead of manual path typing
- expanded streaming mapping support closer to the PPE rule model:
  - desktop mapping entries now support four rule kinds via metadata:
    - transferor -> group
    - transferor -> source_type
    - group -> group
    - group -> source_type
  - `apply_mapping_entries` now supports transferor-group fill, group-chain normalization, and transferor-type priority over group-type
  - mapping form now exposes rule kind + source + target instead of the old 3-field company/group/type shortcut
- repaired several desktop operator regressions found during live validation:
  - normalized stored `listing_date` values so `YYYY/MM/DD` records now match date-picker filters and export ranges
  - overview now exposes `record_state_counts` plus the latest substantive stage summary, so zero-result jobs no longer collapse into a misleading all-zero status line
  - manual export now explains when the selected range has only `待补映射` / `已跳过` rows instead of pretending export simply succeeded with nothing
  - app service now repairs missing archive files from raw snapshots once per runtime when DB rows point at archive paths that no longer exist on disk
  - mapping drafts no longer lose focus every 4 seconds because the mappings panel poll is paused while the user is actively typing
  - mapping page card heights / scroll behavior were made consistent so pending items and saved rules no longer fight with nested scrollbars
  - saved mapping rules now have an explicit visible section with count, keyword search, recent-first ordering, and note display instead of being buried below the edit form
  - record rows now expose user-facing status detail, and `conflict` is rendered as `归档重名` with the actual renamed archive file when available
  - recent job cards now use `已写入` instead of the misleading `已录入`, reducing confusion between ready rows and pending-mapping rows
- aligned ingest ownership more tightly to the approved desktop model:
  - one-click and desktop downloads now save directly into the canonical archive tree instead of writing raw auto snapshots and copying them later
  - saving a mapping rule now starts a background `mapping_refresh` job that reprocesses every affected latest record, not just the currently selected row
  - the desktop backend now exposes a real `manual_import` job that recursively discovers local `html` / `htm` / `mhtml` files and ingests them through the same parser/postprocess/store path
  - small date windows now default to scanning at most 10 list pages when the operator did not explicitly override page count
  - ingest now treats both empty and unrecognized `项目类型` as a blocking `project_type_unknown` condition instead of letting those rows enter `ready`
  - legacy rows whose latest payload normalizes to `project_type_unknown` are now reclassified into `pending_mapping` during backend normalization, keeping overview / records / pending lists aligned on the same store state
  - desktop ingest now prefers downloader-provided `project_type` metadata over parser/path fallback when the two disagree, matching the approved ownership rule that business type belongs to scan/download stage
  - one-click predownload filtering now accepts canonical candidate identity tokens beyond `project_code`, including stored `page_url` / `project_id` tokens from prior downloaded events, reducing repeat rescans that previously slipped through when list-stage rows lacked a stable code
  - `reprocess_record` no longer feeds the stored business type back into ingest as a top-priority override; it now passes that value only as a low-priority fallback so reparse can correct stale historical types when the page content is decisive
  - candidate-identity token reuse now respects the requested record-state filter instead of blindly reusing all historical `downloaded` events, so `parse_failed` / `postprocess_failed` rows no longer get silently hidden behind `ready`-state prefiltering
  - archive-repair normalization now rewires matching historical `downloaded` events when a record `source_file` is collapsed or copied into its archive path, and the one-time repair sweep now covers all latest records instead of silently stopping at the most recent 500 rows
  - both desktop launch and direct streaming pipeline entrypoints now normalize legacy skip/date/pending-mapping state before computing one-click dedupe/export context, so a first action of `一键执行` no longer depends on overview/list-records having been opened earlier in the session
  - ingest now persists downloader-provided `page_url` / `project_id` into the latest record context, and candidate-token assembly now reads those stable identities from latest records as well as `downloaded` events, reducing future dedupe dependence on historical event payload integrity
  - the streaming exchange downloaders now write directly into canonical `submission/YYYY年M月/项目编号-项目名称.html` targets instead of first creating `挂牌_股权转让` / `挂牌_实物资产` style staging subdirectories under submission; `cquae` detail-save and resume-path logic were aligned to the same canonical layout
  - mac desktop packaging now forces `electron-builder` to reuse the locally installed `node_modules/electron/dist` runtime for `pack` / `dist:mac`, avoiding the broken default `unpack-electron` path that produced an empty `Contents/MacOS/` and crashed while renaming `Electron` to the product executable
  - the sqlite-backed desktop store now reapplies its schema on every connection, so deleting `streaming_ingest.sqlite3` while the desktop backend is running no longer bricks overview / settings / one-click / export with `no such table: records` or `no such table: settings`; those flows now self-heal back to an empty initialized database
  - desktop packaging is now routed through a single `desktop_app/package_desktop.js` entrypoint with explicit native-host checks and local `node_modules/electron/dist` reuse, and the GitHub Actions desktop packaging workflow now calls that same entrypoint for both macOS and Windows artifacts instead of maintaining split script logic
  - desktop backend startup now exposes a lightweight `/api/ready` probe separate from the heavyweight runtime-inspecting `/api/health` payload, and Electron main now waits on that ready probe with a longer startup budget so packaged app boot no longer races Playwright runtime inspection
  - desktop packaging now also has a repo-level native entrypoint at `scripts/package_desktop.js`, and `electron-builder.yml` is covered by a manifest regression test that fails if `main.js` adds a new local runtime dependency that is not listed in the packaged `files:` set
  - packaged app startup now writes a small `desktop-app-main.log` under `<Documents>/PEAP/logs/`, so future native-app launch failures can be debugged from resolved backend command / spawn / exit facts instead of blind guessing
  - the packaged mac app no longer launches its bundled backend with `stdio: "inherit"`; packaged runs now pipe backend stdout/stderr into workspace logs instead, which fixed the reproducible `open ...app` flash-exit behavior where the GUI app died before the backend finished booting
  - desktop workspace defaults no longer recreate `data/raw/*`; manual import now lives at `<workspace>/manual`, downloader output stays at `<workspace>/submission`, and startup migration collapses legacy `data/raw/manual` / `data/raw/auto` content into those canonical roots
  - desktop backend startup now interrupts any stale `running` jobs left behind by a previous crash / forced close, marking them `interrupted` instead of letting overview resurrect fake `71%` / `89%` progress from dead sessions
  - one-click execute stage now skips tasks whose prefetched candidate set is empty and short-circuits entirely when collect-stage filtering leaves nothing to download, eliminating the misleading 53%-89% save-page march across empty tasks that live validation reproduced on same-day runs
  - Electron main no longer blocks first paint on backend readiness; the window opens immediately, renderer waits on `/api/ready`, macOS close now quits the app instead of leaving the backend alive headlessly, and the overview exposes a `强制停止` button that restarts the backend and terminates the current task path
  - overview progress now includes the current selected date range during scan/download phases, reducing ambiguity about what the same-day one-click run is actually processing

### 0.3 Intended Desktop Data Layout

Target layout after the current correction work:

- single visible workspace root: `~/Documents/PEAP` on macOS and the user's Documents directory equivalent on Windows
- database: `<workspace>/data/streaming_ingest.sqlite3`
- archived downloaded pages: `<workspace>/submission/`
- manual imported source material staged under `<workspace>/manual/` before ingest
- exports: `<workspace>/exports/`
- logs: `<workspace>/logs/`
- cache and browser runtime: `<workspace>/cache/`

Do not treat split-root layouts as the target model.

### 0.4 Current Constraints / Do Not Delete Yet

Do not delete these yet:

- `peap/streaming_*`
- `peap/download_*`
- `peap/parsing.py`
- `peap_postprocess/`
- `peap_parsers/`

Reason:

- the desktop backend still imports and executes those engine modules directly
- deleting them now would break the active desktop product path

Safe candidates for later deletion only after sidecar packaging and app-only verification:

- legacy CLI wrapper shells under `bin/`
- legacy CLI-oriented README sections
- old runtime-config-only operational docs that no longer serve the desktop path

### 0.5 Immediate Next Steps

Do these in order:

1. fix backend ownership first
   - direct-to-submission save
   - downloader-owned business type
   - CLI-grade dedupe before download and during store upsert
   - store-driven pending-mapping / ready normalization
2. fix manual import and mapping refresh against the same normalized ingest path
3. fix progress projection so Electron and terminal show the same business progress
4. only then restructure frontend pages
   - dedicated left-nav task page
   - records page as a real database viewer
   - mapping page saved-rules table visible without buried scrolling
5. run real Electron validation on the same existing workspace/database the operator has already been using
6. after behavior is trustworthy, rerun packaging and packaged-app validation on native macOS/Windows runners

### 0.6 Validation Status

Validated in this thread:

- `python3 -m unittest tests.test_app_config tests.test_app_service`
- `python3 -m compileall desktop_backend peap bin tests`
- `node --check desktop_app/main.js`
- `node --check desktop_app/preload.js`
- `node --check desktop_app/renderer.js`
- `bash -n scripts/bootstrap_desktop_env.sh`
- `node --check desktop_app/backend_launch.js`
- `node --check desktop_app/main.js`
- `node --check desktop_app/build_backend_sidecar.js`
- `node --check desktop_app/renderer.js`
- `node --test desktop_app/backend_launch.test.js`
- `python3 -m unittest tests.test_app_config tests.test_app_service tests.test_runtime_dependencies`
- `python3 -m compileall desktop_backend`
- `bash -n scripts/bootstrap_desktop_env.sh`
- `python3 -m unittest tests.test_streaming_ingest tests.test_streaming_daily_pipeline tests.test_app_service tests.test_runtime_dependencies`
- `node --check desktop_app/main.js`
- `node --check desktop_app/preload.js`
- `node --check desktop_app/renderer.js`
- `python3 -m unittest tests.test_app_config tests.test_app_service tests.test_runtime_dependencies tests.test_streaming_ingest tests.test_streaming_daily_pipeline tests.test_streaming_store tests.test_streaming_postprocess`
- `node --check desktop_app/main.js`
- `node --check desktop_app/preload.js`
- `node --check desktop_app/renderer.js`
- `python3 -m unittest tests.test_app_config tests.test_app_service tests.test_runtime_dependencies tests.test_streaming_ingest tests.test_streaming_daily_pipeline tests.test_streaming_store tests.test_streaming_export`
- `python3 -m compileall desktop_backend peap`
- `node --check desktop_app/preload.js`
- `node --check desktop_app/backend_launch.js`
- `node --test desktop_app/backend_launch.test.js`
- `python3 -m unittest tests.test_app_service tests.test_streaming_store tests.test_streaming_export tests.test_streaming_ingest`
- `node --check desktop_app/renderer.js`
- `node --check desktop_app/preload.js`
- `node --check desktop_app/backend_launch.js`
- `python3 -m unittest tests.test_app_service tests.test_streaming_store tests.test_streaming_ingest tests.test_download_oneclick tests.test_streaming_daily_pipeline tests.test_streaming_export tests.test_runtime_dependencies -v`
- `python3 -m compileall desktop_backend peap`
- `node --check desktop_app/main.js`
- `node --check desktop_app/preload.js`
- `node --check desktop_app/renderer.js`
- `node --test desktop_app/backend_launch.test.js`
- `python3 -m unittest tests.test_app_service tests.test_streaming_store tests.test_streaming_ingest -v`
- `python3 -m unittest tests.test_streaming_store tests.test_streaming_ingest tests.test_download_oneclick tests.test_streaming_daily_pipeline tests.test_app_service -v`
- `python3 -m unittest tests.test_streaming_store tests.test_streaming_ingest tests.test_download_oneclick tests.test_streaming_daily_pipeline tests.test_app_service tests.test_streaming_export tests.test_runtime_dependencies -v`
- `python3 -m compileall desktop_backend peap tests`
- `python3 -m unittest tests.test_streaming_store tests.test_streaming_ingest tests.test_download_oneclick tests.test_streaming_daily_pipeline tests.test_app_service tests.test_streaming_export tests.test_runtime_dependencies -v` (`78` tests, latest rerun after archive-repair / launch-normalization / record-token fixes)
- `python3 -m compileall desktop_backend peap tests` (latest rerun after the same fixes)
- `python3 -m unittest tests.test_exchange_downloader_fixes tests.test_streaming_store tests.test_streaming_ingest tests.test_download_oneclick tests.test_streaming_daily_pipeline tests.test_app_service tests.test_streaming_export tests.test_runtime_dependencies -v` (`89` tests, latest rerun after direct-to-submission downloader path fix)
- `python3 -m compileall desktop_backend peap tests` (latest rerun after the downloader path fix)
- `cd desktop_app && npx electron-builder --dir -c electron-builder.yml --config.electronDist=node_modules/electron/dist --config.directories.output=dist-local --publish never` (latest rerun after packaging fix; produced `dist-local/mac-arm64/产权交易所自动录入.app`)
- `cd desktop_app && npx electron-builder --mac pkg -c electron-builder.yml --config.electronDist=node_modules/electron/dist --config.directories.output=dist-pkg-local --publish never` (latest rerun after packaging fix; produced `dist-pkg-local/property-exchange-auto-entry-0.1.0-mac.pkg`)
- `cd desktop_app && npm run dist:mac` (latest rerun after wiring `dist:mac` to local `node_modules/electron/dist`; produced `dist/property-exchange-auto-entry-0.1.0-mac.pkg` and `dist/mac-arm64/产权交易所自动录入.app`)
- `cd desktop_app && npm run pack -- --config.directories.output=dist-pack-verify` (latest rerun after wiring `pack` to local `node_modules/electron/dist`; produced `dist-pack-verify/mac-arm64/产权交易所自动录入.app`)
- `python3 -m unittest tests.test_streaming_store tests.test_app_service -v` (`47` tests, latest rerun after sqlite schema self-heal for runtime db deletion)
- `cd desktop_app && node --test ./package_desktop.test.js ./backend_launch.test.js` (`9` tests, latest rerun after unifying desktop packaging entrypoint)
- `cd desktop_app && npm run dist:mac` (latest rerun through unified `package_desktop.js` entrypoint; produced `dist/property-exchange-auto-entry-0.1.0-mac.pkg`)
- `python3 -m unittest tests.test_app_backend tests.test_app_service tests.test_streaming_store -v` (`49` tests, latest rerun after adding the lightweight ready endpoint and startup self-heal coverage)
- `cd desktop_app && npm test` (`18` tests, latest rerun after adding the packaged-file manifest regression that keeps local main-process dependencies in `app.asar`)
- `node scripts/package_desktop.js --platform mac --layout release` (latest rerun through the repo-level native packaging entrypoint; produced `desktop_app/dist/property-exchange-auto-entry-0.1.0-mac.pkg`)
- packaged app smoke: `open -n desktop_app/dist/mac-arm64/产权交易所自动录入.app` followed by `GET http://127.0.0.1:42679/api/ready` returned `200`, confirming the packaged `.app` now spawns its bundled backend successfully after the ready-probe and manifest fixes
- packaged app stability rerun: a 30-second launch trace after `open -n desktop_app/dist/mac-arm64/产权交易所自动录入.app` kept both the main process and backend listener alive through `t=30s`, with `desktop-app-main.log` recording `backend_ready` and `desktop-backend.log` capturing the sidecar boot line after switching packaged sidecar stdio from `inherit` to piped log files
- `python3 -m unittest tests.test_app_backend tests.test_app_config tests.test_streaming_store tests.test_download_oneclick tests.test_streaming_daily_pipeline tests.test_app_service -v` (`72` tests, latest rerun after workspace-layout cleanup, stale-job interruption, empty-task one-click pruning, and force-stop/startup contract fixes)
- `cd desktop_app && npm test` (`20` tests, latest rerun after adding the force-stop / non-blocking-startup layout contracts)
- `python3 -m compileall desktop_backend peap tests` (latest rerun after the same desktop/runtime fixes)

Also verified earlier in this thread:

- streaming store / ingest / daily pipeline tests pass
- desktop backend can boot and answer `/api/health`

### 0.7 Environment Note

The temporary user-level Python packages installed earlier in this thread were removed.

Current expectation:

- do not use the macOS CommandLineTools Python as the desktop dev runtime
- move to `pyenv` + repo-local `.venv-desktop`

## 1. Goal

Bring this repository from "usable script collection" to "good engineering project" without breaking the current data pipeline.

Target qualities:
- single source of truth for runtime config and path resolution
- installable package + reproducible dev environment
- smaller modules with clear ownership
- command layer built on Python APIs instead of stdout/file coupling
- stable regression checks for downloader, parser, and PPE
- runtime output stays outside the repo by default

## 1.1 Closing Mode And Stop Condition

This refactor needs a stop line. The goal for the current thread is a stable, maintainable handoff point, not endless architectural cleanup.

Estimated remaining work to reach a reasonable stop point:
- `0` mandatory refactor batches; the repo is now at the intended closeout stop line for this thread

Must finish before stopping:
- `completed`: one more meaningful parser-contract batch so exchange-specific parser output is pushed further behind the `ParsedProject` boundary
- `completed`: one final repo-hygiene / smoke-validation batch so default output paths, ignore rules, and core smoke commands are rechecked at the end
- `completed`: one short integrated closeout / handoff validation sweep so the final stop line is explicit

Worth doing only if it naturally falls out of the must-finish work:
- `completed in the latest continuation`: one small config-coupling cleanup for high-value lazy default-config hotspots in wrapper entrypoints and import-time logger wiring

Safe to defer to backlog:
- further downloader CLI thinning once current behavior is already stable
- large-file decomposition done only for aesthetics/line count
- broad test expansion beyond the targeted regression coverage needed to protect the closeout path
- deeper dependency-injection cleanup whose main benefit is architectural neatness rather than risk reduction

## 2. Original Plan Re-evaluated

### 2.1 Engineering foundation
Status: mostly done

Done:
- Added package metadata in `pyproject.toml`.
- Added editable install / dev dependency flow.
- Added CI workflow under `.github/workflows/ci.yml`.
- Added shared runtime helpers under `peap_core/`.
- Added base tests under `tests/`.
- canonical package/script/doc/template naming is now normalized around `peap`, `peap_parsers`, `peap_postprocess`, and lower_snake_case entry/doc filenames

Still missing:
- CI currently covers the new base tooling, but coverage is still shallow.
- `bin/` bootstrap scripts still exist and are still real runtime entrypoints in practice.

### 2.2 Config convergence
Status: partially done

Done:
- `config.py` is now instance-based and reloadable.
- Common runtime/path/json helpers moved to `peap_core.runtime`.
- `prepare_submission` no longer hardcodes `E:/PEAP_DATA`.
- PPE default config now points to external template output under `PEAP_DATA`.

Still missing:
- Some config/utility logic is still duplicated across parser/downloader/PPE layers.
- Global module-level `config` is still widely imported, so true dependency injection is incomplete.
- parser pipeline, output-target selection, and several helper entrypoints now accept injected settings/runtime objects; `peap/excel_handler.py` no longer mirrors writer state into compatibility globals, but several modules still rely on lazy default-config fallback for backward compatibility

### 2.3 Command layer refactor
Status: partially done

Done:
- Shared CLI logging / summary helpers exist.
- Download task registry was extracted from `peap/download_cli.py`.
- Downloader split planning and chunk state logic were extracted.
- Task-level downloader orchestration was extracted into `peap/download_task_flow.py`.
- Top-level downloader run orchestration was extracted into `peap/download_runner.py`.
- Downloader CLI / one-click / daily wrapper now share typed `DownloadRunRequest` request objects at the Python API boundary.
- One-click downloader orchestration now lives in `peap/download_oneclick.py`.
- Daily pipeline wrapper now calls downloader/parser/PPE through structured Python runners instead of subprocess stdout parsing.
- Parser and PPE CLIs now reuse structured runner modules instead of embedding all run orchestration inside CLI files.
- Downloader task-list formatting and download summary payload construction now live in reusable helpers instead of inside `peap/download_cli.py`.

Still missing:
- standalone CLIs still own argument parsing and the final `write_summary_json(...)` call at the outer boundary

### 2.4 Big-file decomposition
Status: started, not finished

Done:
- `peap/download_cli.py` lost task registry, split planning, and chunk state persistence logic.
- `peap/download_reporting.py` now owns summary dict conversion, accumulation, and aggregate/user-facing reporting.
- `peap/download_execution.py` now owns auto-split chunk execution, skip/resume decisions, and chunk-state transitions.
- `peap/download_task_flow.py` now owns per-task branching for direct download vs auto-split task execution.
- `peap/download_runner.py` now owns date normalization, output-root validation, runtime dependency checks, top-level task aggregation, and split-plan save flow.
- `peap/download_cli_payloads.py` now owns downloader task-list rendering, structured summary payload construction, and final run message formatting.

Still missing:
- large parser / downloader / PPE rule files still need decomposition
- downloader construction and request adaptation are now reusable through `peap/download_runner.py`, but standalone downloader CLI still owns argparse parsing, logger lifecycle, and the final summary-json write call.

### 2.5 Quality gates
Status: partially done

Done:
- `pytest` + `ruff` are installed and configured.
- recommended repo-wide `python -m ruff check peap_core tests config.py peap peap\downloaders peap_postprocess scripts` now passes
- regression tests exist for runtime helpers, config reload, and extracted downloader split/state modules
- regression tests now cover one-click downloader orchestration and daily pipeline stage composition
- regression tests now cover parser contract boundaries, explicit parser-output objects, and partial structured parse-cache rehydration
- PPE runner request-boundary / exit-code / summary-payload behavior is now covered with injected-dependency tests
- PPE rule-registry / rule-plan ordering / config-list parsing behavior now has targeted regression coverage

Still missing:
- deep per-rule PPE behavior coverage is still shallow outside the targeted registry/runner contract tests
- no dedicated fixture set for exchange-specific downloader behavior

### 2.6 Repo hygiene / ops consistency
Status: mostly done

Done:
- repo defaults now prefer `PEAP_DATA` for runtime output
- `.gitignore` now ignores `.tmp_test_data_root/` and `*.egg-info/`
- default PPE wrappers and direct helper scripts now point at external-template config/output paths under `PEAP_DATA`

Still missing:
- worktree is currently dirty; do not treat it as a clean baseline

## 3. Current Architecture Snapshot

### 3.1 Shared foundation
- `peap_core/runtime.py`
  - path normalization / resolution
  - runtime config loading
  - json read/write helpers
  - atomic json write helper
- `peap_core/cli_support.py`
  - shared CLI logger setup
  - summary json read/write
  - logger handler cleanup for repeated in-process stage runs
- `peap/logger.py`
  - explicit `LegacyLoggerSettings` for legacy Excel-writer logger defaults
  - default logger settings can now be overridden without depending on a module-level `config` import
- `peap_parsers/base.py`
  - shared `ParserContext` for parser-side source metadata
  - explicit `ParserOutput` contract for parser-to-orchestrator payload handoff
  - shared `source_file` / `require_source_file()` contract for parsers that need file-local sidecars or cached tabs

### 3.2 Runtime config
- `config.py`
  - `Config` instance model
  - module-global `config`
  - `reload_config(...)`

### 3.3 Downloader
- `peap/download_tasks.py`
  - runtime-built task registry
  - explicit `DownloadTaskRegistrySettings` for task-page-size injection
  - default task-registry settings can now be overridden without a top-level direct `config` import
- `peap/download_cli_payloads.py`
  - task-list line formatting
  - task-list / run / error summary payload builders
  - final run message formatting
- `peap/download_models.py`
  - `DateChunk`
  - `TaskSplitPlan`
  - `ChunkStateContext`
  - `DownloadTaskRunResult`
  - `DownloadRunResult`
- `peap/download_split_planning.py`
  - split plan load/save
  - candidate extraction
  - date chunk planning
- `peap/download_chunk_state.py`
  - chunk state load/save/update
- `peap/download_task_flow.py`
  - split-plan loading for run mode
  - chunk-state context setup
  - task-level orchestration and result assembly
- `peap/download_runner.py`
  - `DownloadRunRequest` + CLI-to-request adapter helpers
  - explicit `DownloadRunnerSettings` for task registry / output-root / chunk-state config slices
  - output-root / dependency validation
  - date normalization
  - task aggregation into final structured run result
  - split-plan save handling
- `peap/download_cli.py`
  - still owns CLI args
  - parser construction now accepts injected config objects and only uses lazy default-config fallback at the outer boundary
  - now mostly owns CLI parsing, logger setup, and final summary json write
- `peap/cli.py`
  - parser CLI argument defaults can now be built from an injected config object instead of a top-level direct `config` import
- `peap/download_oneclick.py`
  - structured split-plan + execute + refresh orchestration
  - consumes typed downloader requests instead of `argparse.Namespace`
  - wrapper-level merged summary assembly
- `peap/parser_runner.py`
  - structured parser runner reused by parser CLI and daily pipeline
  - now builds injected parser pipeline settings from `config_obj`
- `peap/parsing.py`
  - `ParsedProject` now carries both the compatibility raw dict payload and a structured `StandardProject`
  - `ParsedProject` now also exposes explicit routing properties like `project_code` / `status` / `project_type` / `is_pre_disclosure`
  - `build_parsed_project(...)` is the shared hydrator used by live parsing and parse-cache hits
  - `parse_file(...)` now accepts explicit parser-output objects and merges structured parser fields with compatibility fallback payloads
- `peap/targeting.py`
  - explicit `OutputTargetSettings` for parser output-file selection
  - `decide_output_file(...)` now accepts `ParsedProject` as a first-class boundary object, not only raw dict payloads
  - no longer needs module-global config when used through injected settings
- `peap/excel_handler.py`
  - explicit `ExcelSchemaSettings` for schema-path resolution
  - explicit `ExcelOutputRuntime` for writer/output schema state
  - default schema settings can now be overridden explicitly instead of requiring a top-level `config` import
  - parser pipeline and self-check can now consume an injected runtime object instead of depending on import-order-driven module state
  - legacy default writer behavior now routes through a single active runtime object instead of mirrored mutable module globals
- `peap/public_resource_deals.py`
  - explicit `PublicResourceDealSettings` for manual input/output path resolution
  - default path/config fallback is now lazy instead of relying on a top-level direct `config` import
  - workbook builder and CLI defaults can now use injected settings instead of reading module-global config directly
- `peap/checks.py`
  - self-check now resolves default config lazily and still accepts explicit `config_obj` injection

### 3.4 PPE
- `peap_postprocess/postprocess_engine/runner.py`
  - structured PPE runner reused by CLI and daily pipeline
  - now accepts injected config-loader / engine / unresolved-export / logger lifecycle dependencies for testability while preserving default CLI behavior
- `peap_postprocess/compare_regression.py`
  - regression compare helper now accepts injected config for default report output paths and uses lazy fallback otherwise
- `peap_postprocess/postprocess_engine/cli.py`
  - now uses shared CLI/runtime helpers and delegates execution to the runner module
  - default config path points to external template

## 4. Verified Baseline

These commands were executed successfully in this environment after the latest refactor batch:

```powershell
python -m pip install -e .[dev]
python -m playwright install chromium
python -m ruff check peap_core tests config.py peap\download_models.py peap\download_chunk_state.py peap\download_split_planning.py peap\download_tasks.py peap\download_cli.py peap\cli.py bin\daily_pipeline.py bin\download_oneclick.py peap_postprocess\run_postprocess.py peap_postprocess\postprocess_engine\cli.py scripts\prepare_submission.py
python -m pytest tests
python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_after_split_refactor.json
python peap_postprocess\run_postprocess.py run --skip-unresolved-list
python bin\download.py --list-tasks
python -m ruff check peap\download_cli.py peap\download_execution.py peap\download_reporting.py tests\test_download_execution_reporting.py
python -m pytest tests\test_download_execution_reporting.py tests\test_download_split_modules.py
python -m ruff check peap\download_cli.py peap\download_execution.py peap\download_task_flow.py peap\download_reporting.py peap\download_models.py tests\test_download_execution_reporting.py
python -m pytest tests
python -m ruff check peap\download_cli.py peap\download_runner.py peap\download_task_flow.py peap\download_execution.py peap\download_reporting.py peap\download_models.py tests\test_download_runner.py tests\test_download_execution_reporting.py
```

Observed results:
- `ruff`: all checks passed
- `pytest`: 14 passed
- parser self-check: 14 checks, 0 failed
- PPE smoke: passed, discovered 4 files, failed_files=0
- downloader task listing: passed
- new download execution/reporting unit tests: passed
- task-flow extraction tests: passed
- runner extraction tests: passed

Additional validation this batch:
- `python -m pytest tests`: passed, 19 passed
- targeted `ruff` for touched downloader files/tests: passed after import auto-fix
- full `python -m ruff check peap_core tests config.py peap peap\downloaders peap_postprocess scripts`: still fails on pre-existing issues outside this refactor batch
- targeted `ruff` for touched CLI / orchestration files and new tests: passed
- `python -m pytest tests`: passed, 23 passed
- `python bin\download.py --list-tasks`: passed
- `python bin\download_oneclick.py --help`: passed
- `python bin\daily_pipeline.py --help`: passed
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_after_oneclick_refactor.json`: passed, 14 checks, 0 failed
- `python peap_postprocess\run_postprocess.py run --skip-unresolved-list`: passed, discovered 4 files, failed_files=0
- targeted `ruff` for parser/PPE runner request-boundary refactor: passed
- `python -m pytest tests\test_daily_pipeline.py tests\test_runner_request_adapters.py`: passed, 4 passed
- `python -m pytest tests`: passed, 25 passed
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_request_refactor.json`: passed, 14 checks, 0 failed
- `python peap_postprocess\run_postprocess.py run --skip-unresolved-list`: passed, discovered 4 files, failed_files=0
- targeted `ruff` for downloader CLI payload cleanup: passed
- `python -m pytest tests`: passed, 29 passed
- `python bin\download.py --list-tasks`: passed
- `python bin\download_oneclick.py --help`: passed
- `python -m ruff check peap\parsing.py peap_parsers\base.py peap_parsers\__init__.py peap_parsers\public_resource.py tests\test_parsing_contract.py`: passed
- `python -m pytest tests`: passed, 31 passed
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_parser_contract_refactor.json`: passed, 14 checks, 0 failed
- `python -m ruff check peap\pipeline.py peap\parser_runner.py peap\checks.py tests\test_runner_request_adapters.py`: passed
- `python -m pytest tests`: passed, 33 passed
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_pipeline_settings_refactor.json`: passed, 14 checks, 0 failed
- `python -m ruff check peap\targeting.py peap\pipeline.py peap\parser_runner.py tests\test_targeting.py tests\test_runner_request_adapters.py`: passed
- `python -m pytest tests`: passed, 36 passed
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_output_target_settings_refactor.json`: passed, 14 checks, 0 failed
- `python -m ruff check peap\excel_handler.py peap\pipeline.py peap\checks.py peap\parser_runner.py tests\test_excel_schema_settings.py tests\test_runner_request_adapters.py`: passed
- `python -m pytest tests`: passed, 38 passed
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_excel_schema_settings_refactor.json`: passed, 14 checks, 0 failed
- `python -m ruff check peap\parsing.py peap\parse_cache.py peap\pipeline.py tests\test_parsing_contract.py tests\test_parse_cache.py`: passed
- `python -m pytest tests\test_parsing_contract.py tests\test_parse_cache.py`: passed, 3 passed
- `python -m pytest tests`: passed, 44 passed
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_parsed_project_contract_refactor.json`: passed, 14 checks, 0 failed
- `python -m ruff check peap\parsing.py peap\targeting.py peap\pipeline.py tests\test_parsing_contract.py tests\test_parse_cache.py tests\test_targeting.py`: passed
- `python -m pytest tests\test_parsing_contract.py tests\test_parse_cache.py tests\test_targeting.py`: passed, 7 passed
- `python -m pytest tests`: passed, 45 passed
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_parsed_project_routing_refactor.json`: passed, 14 checks, 0 failed
- `python -m ruff check peap\logger.py peap\excel_handler.py tests\test_logger_settings.py tests\test_excel_schema_settings.py`: passed
- `python -m pytest tests\test_logger_settings.py tests\test_excel_schema_settings.py`: passed, 7 passed
- `python -m pytest tests`: passed, 48 passed
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_logger_excel_settings_refactor.json`: passed, 14 checks, 0 failed
- `python -m ruff check peap\cli.py peap\download_cli.py peap\download_tasks.py tests\test_cli_config_injection.py`: passed
- `python -m pytest tests\test_cli_config_injection.py tests\test_config_runtime.py tests\test_runner_request_adapters.py`: passed, 12 passed
- `python -m pytest tests`: passed, 51 passed
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_cli_lazy_config_refactor.json`: passed, 14 checks, 0 failed
- `python bin\download.py --list-tasks`: passed
- `python -m ruff check peap\checks.py peap\public_resource_deals.py peap_postprocess\compare_regression.py tests\test_public_resource_deals_settings.py tests\test_compare_regression.py`: passed
- `python -m pytest tests\test_public_resource_deals_settings.py tests\test_compare_regression.py tests\test_runner_request_adapters.py`: passed, 10 passed
- `python -m pytest tests`: passed, 53 passed
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_aux_tools_lazy_config_refactor.json`: passed, 14 checks, 0 failed
- `python bin\public_resource_deals.py --help`: passed

## 5. Constraints And Guardrails

- Do not revert unrelated user changes in the dirty worktree.
- Use `apply_patch` for manual file edits.
- Keep runtime output outside repo unless there is a strong reason otherwise.
- Preserve working CLI behavior while refactoring internals.
- Prefer extracting logic into modules before rewriting behavior.
- After each meaningful modification batch:
  - update this file
  - run the relevant validation commands
  - record any new risk or blocker here

## 6. Known Dirty Worktree Warning

This repo already contains many modified files beyond the latest refactor work. Before changing any file, inspect its current contents carefully and do not assume the diff was produced only by this thread.

## 7. Priority Queue

Closeout priority order for the next continuations:
1. do one short integrated closeout / smoke-validation sweep and confirm the stop line
2. only then consider a small `P1 config coupling` mop-up if that sweep exposes a real hotspot
3. defer deeper parser-internal dict elimination, downloader CLI thinning, large-file decomposition, and broad test expansion unless a regression forces them back into scope

### P2. Parser contract cleanup
Status: closeout batch completed in the latest continuation

Goal:
- replace implicit parser-side conventions with explicit data contracts

Tasks:
- completed:
  - inspected `peap_parsers/base.py` and `peap/parsing.py`
  - added explicit `ParserContext` in `peap_parsers/base.py`
  - updated `peap/parsing.py` to construct parsers with `context=...` instead of mutating `parser.source_file` after init
  - updated source-file-dependent parsers to use the shared context-backed contract
  - updated router parsers such as `peap_parsers/beijing.py` and `peap_parsers/shanghai.py` to preserve `ParserContext` when delegating into template-specific subparsers
  - added focused regression coverage in `tests/test_parsing_contract.py`
  - promoted `ParsedProject` into an explicit post-parse contract that now carries both raw compatibility data and a structured `StandardProject`
  - updated `peap/parse_cache.py` and `peap/pipeline.py` to reuse that structured parse-result contract instead of rebuilding the standard model downstream
  - updated parse-cache persistence so cached rows now store an explicit serialized `ParsedProject` contract and still read legacy dict-only cache rows for compatibility
  - added focused regression coverage in `tests/test_parse_cache.py`
  - added explicit routing properties on `ParsedProject` so downstream code can read project identity/status/type/pre-disclosure state without indexing raw dict keys
  - updated `peap/targeting.py` and `peap/pipeline.py` so parser output routing and pre-disclosure archive decisions now consume the `ParsedProject` boundary directly
  - extended `tests/test_targeting.py` so output-target selection is covered for `ParsedProject` inputs as well as raw dict compatibility payloads
  - added explicit `ParserOutput` in `peap_parsers/base.py` so parsers can return both compatibility payloads and structured standard-field overrides
  - updated `peap/parsing.py` so `parse_file(...)` accepts parser-output objects and `build_parsed_project(...)` merges explicit standard fields with compatibility-derived defaults
  - updated `peap_parsers/beijing_standard.py` and `peap_parsers/guangzhou.py` to emit the explicit parser-output contract instead of only a raw dict
  - extended `tests/test_parsing_contract.py` and `tests/test_parse_cache.py` with parser-output and partial structured-cache coverage
- still open:
  - deeper parser-internal dict elimination inside the remaining exchange parsers is now backlog unless a regression points back to it
  - more fixture-driven tests for concrete exchange parser contracts are useful, but no longer required for closeout

Acceptance:
- parser inputs/outputs are explicit and testable
- parsing pipeline no longer mutates parser instances with hidden attributes
- one additional parser-internal dict-heavy area is brought behind the explicit parse-result boundary

Current note:
- the parser-to-pipeline boundary is now explicit; remaining dict-heavy code is internal to parser implementations rather than leaked across the main pipeline

Suggested first files to inspect:
- `peap/parsing.py`
- `peap_parsers/beijing_standard.py`
- `peap_parsers/guangzhou.py`
- `peap/pipeline.py`

### P3. Final repo hygiene pass
Status: completed in the current continuation

Goal:
- confirm the repo exits this thread in a stable and predictable runtime state

Tasks:
- verify all default output paths resolve under `PEAP_DATA`
- review `.gitignore` against current generated artifacts
- confirm repo root stays clean after standard smoke runs
- rerun the closeout validation set and record the final state here

Acceptance:
- standard smoke/test commands still pass
- default runtime output does not drift back into the repo
- `todo.md` clearly records the final residual backlog instead of reopening scope

Closeout result:
- default runtime configs and wrapper defaults now point to external-output paths under `PEAP_DATA`
- `.gitignore` review found current generated artifacts already covered for this closeout scope; no new ignore rule was required to keep smoke-run artifacts out of versioned status
- closeout smoke commands completed without creating new unignored runtime output under the repo root

Suggested first files to inspect:
- `.gitignore`
- `assets/runtime_config.json`
- `config.py`
- `todo.md`

### P1. Reduce script-to-script coupling in one-click flows
Status: mostly done, not required for closeout beyond regression protection

Goal:
- wrappers should compose Python APIs, not parse stdout or depend on ad hoc summary files as the primary contract

Tasks:
- completed:
  - inspected `bin/daily_pipeline.py`
  - inspected `bin/download_oneclick.py`
  - defined structured stage result objects in importable modules
  - moved orchestration logic behind Python-callable functions
  - kept summary json as optional artifact instead of the primary integration contract
  - narrowed parser / PPE runner inputs from argparse-shaped namespaces to typed request objects
  - added `DownloadRunRequest` so downloader one-click / daily pipeline now call downloader APIs through typed request objects too
- remaining:
  - standalone entrypoints still do their own argparse parsing and summary-json writing at the outer boundary

Acceptance:
- wrappers can call downloader/parser/PPE through functions and receive structured results directly
- changing log text does not break one-click flows

### P2. Continue large-file decomposition
Status: defer unless a must-finish batch is already done and more cleanup is explicitly requested

Targets likely worth splitting next:
- `peap_parsers/beijing_standard.py`
- `peap/downloaders/sse_physical.py`
- `peap_postprocess/postprocess_engine/rules/builtin.py`
- `scripts/prepare_submission.py`

Approach:
- split by domain responsibility, not arbitrarily by line count
- keep public import surface stable where possible

### P2. Expand automated tests
Status: defer beyond targeted safety coverage for closeout work

Tasks:
- add tests for downloader executor behavior
- add tests for parser pipeline contract
- add tests for PPE registry / rule loading contract
- add fixture-driven tests for split-plan and chunk-resume edge cases

Acceptance:
- refactors in downloader/parser/PPE fail fast under unit tests before black-box smoke checks

### P1. Reduce remaining config coupling
Status: important but no longer primary; only continue for hotspots discovered while finishing parser/hygiene work

Goal:
- move from "reloadable global config" to "config can be injected into core services"

Tasks:
- completed:
  - identified parser pipeline as an immediate config-coupling hotspot
  - added `ParserPipelineSettings` so parser runner / self-check inject a typed settings slice into `ParserPipeline`
  - removed direct global-`config` import from `peap/pipeline.py`
  - added `DownloadTaskRegistrySettings` and `DownloadRunnerSettings` so downloader runner / task registry can consume injected config slices instead of raw config objects in the core path
  - added `OutputTargetSettings` so parser output-file selection can be injected instead of reading global config
  - removed direct global-`config` import from `peap/targeting.py`
  - added `ExcelSchemaSettings` so parser/self-check can reload excel schema from explicit config-derived paths
  - added `PublicResourceDealSettings` so public-resource deal workbook defaults can be injected instead of reading global config directly
  - added `LegacyLoggerSettings` so legacy Excel-writer logging defaults can be injected explicitly instead of depending on a module-level `config` import
  - replaced the top-level direct `config` imports in `peap/logger.py` and `peap/excel_handler.py` with explicit settings plus lazy default-setting fallback
  - replaced the top-level direct `config` imports in `peap/cli.py`, `peap/download_cli.py`, and `peap/download_tasks.py` with injected config/settings inputs plus lazy default fallback at the outer boundary
  - replaced the top-level direct `config` imports in `peap/checks.py`, `peap/public_resource_deals.py`, and `peap_postprocess/compare_regression.py` with injected config/settings inputs plus lazy default fallback
- remaining:
  - identify only the next highest-value importers of module-global `config`
  - replace direct global reads in core services with explicit config object or typed settings slices when those services are already being touched by closeout work
  - keep CLI entrypoints responsible for building config objects

Acceptance:
- core non-CLI services can be instantiated in tests with a provided config object
- changing config at runtime does not depend on import order

Suggested first files to inspect:
- `config.py`
- `peap/cli.py`
- `peap/pipeline.py`
- `peap/download_cli.py`

### P0. Finish downloader command-layer decomposition
Status: defer for this thread unless a downloader regression or user request pulls it back into scope

Goal:
- reduce `peap/download_cli.py` to a thin CLI/orchestration entrypoint

Tasks:
- completed:
  - extracted chunk execution loop into `peap/download_execution.py`
  - extracted aggregate summary/report rendering into `peap/download_reporting.py`
  - extracted task-level direct-vs-split branching into `peap/download_task_flow.py`
  - extracted top-level run aggregation into `peap/download_runner.py`
  - moved one-click downloader orchestration into `peap/download_oneclick.py`
  - moved downloader task-list formatting / summary payload construction into `peap/download_cli_payloads.py`
- still open:
  - decide whether the final downloader CLI logger/session wrapper should also be extracted, or whether the module is thin enough already for current needs

Acceptance:
- `peap/download_cli.py` no longer contains chunk-state transition logic
- `peap/download_cli.py` no longer contains aggregate summary formatting logic
- `python bin\download.py --list-tasks` still passes
- downloader smoke path still runs at least through planning / task listing without regressions

Current note:
- `peap/download_cli.py` reduced from 923 lines to 287 lines across the latest P0 batches and now mostly owns argument parsing, logger setup, and final json emission

Suggested first files to inspect:
- `peap/download_cli.py`
- `peap/download_chunk_state.py`
- `peap/download_split_planning.py`

## 8. Suggested Execution Order For The Next AI

1. Read this file fully.
2. Inspect current diffs with `git status --short`.
3. Take the next `P2 parser contract cleanup` slice; do not open new architectural fronts unless needed by that slice.
4. Update this file with the exact subtask being attempted and keep the scope limited to one cohesive batch.
5. Run:
   - `python -m ruff check ...`
   - `python -m pytest tests`
   - parser self-check if parser/shared runtime code changed
   - at least one relevant smoke command for the touched area
6. Once parser closeout work is acceptable, do the `P3 final repo hygiene pass`.
7. Stop if the must-finish list in section `1.1` is complete; do not automatically continue into deferred cleanup.

## 12. Latest Batch Notes

Completed in the current continuation:
- added `ParsedProject.to_cache_payload()` / `ParsedProject.from_cache_payload()` so parse-result persistence now uses an explicit serialized `ParsedProject` contract instead of only a raw compatibility dict
- added `hydrate_standard_project(...)` in `peap/standard_model.py` and updated `build_parsed_project(...)` so cached structured standard fields can be rehydrated without rebuilding only from the compatibility payload
- updated `peap/parse_cache.py` to persist both legacy `data_json` and a new structured `parsed_json`, preferring the structured contract on cache reads while remaining backward-compatible with legacy cache rows
- extended `tests/test_parse_cache.py` with coverage for structured-cache precedence and legacy dict-only cache compatibility

Validation run in the current continuation:
- `python -m ruff check peap\standard_model.py peap\parsing.py peap\parse_cache.py tests\test_parse_cache.py tests\test_parsing_contract.py`
- `python -m pytest tests\test_parse_cache.py tests\test_parsing_contract.py`
- `python -m pytest tests`
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_parse_cache_contract.json`

Remaining risk / blocker after the current continuation:
- parse-cache persistence is now behind the explicit parse-result contract, but parser implementations themselves still originate exchange-specific dict payloads before reaching `ParsedProject`
- the next highest-value parser-contract step is still inside parser internals such as `peap_parsers/beijing_standard.py` / `peap_parsers/guangzhou.py`, not in downstream cache or routing layers

Completed in the current continuation:
- added `child_context()` / `spawn_child_parser()` helpers in `peap_parsers/base.py` so parser-to-parser delegation reuses an explicit, cloned `ParserContext`
- updated `peap_parsers/beijing.py` and `peap_parsers/shanghai.py` so router parsers preserve `source_file` when handing off to standard/special subparsers
- extended `tests/test_parsing_contract.py` with focused regression coverage for delegated-parser context propagation

Validation run in the current continuation:
- `python -m ruff check peap_parsers\base.py peap_parsers\beijing.py peap_parsers\shanghai.py tests\test_parsing_contract.py`
- `python -m pytest tests\test_parsing_contract.py`
- `python -m pytest tests`
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_router_context_contract.json`

Remaining risk / blocker after the current continuation:
- router-level parser delegation now preserves explicit context, but parser implementations still originate exchange-specific dict payloads before the `ParsedProject` boundary
- parse-cache persistence still stores compatibility payload dicts and rehydrates `ParsedProject` from them, so parser contract cleanup remains only partially end-to-end

Completed in the latest continuation:
- removed compatibility runtime-global mirroring from `peap/excel_handler.py` so active writer/schema state now lives in a single `ExcelOutputRuntime` object
- simplified excel schema status construction and reload flow around `reload_excel_output_schema(...)`
- extended `tests/test_excel_schema_settings.py` with coverage for default active-runtime writes and read-only schema snapshot copies

Validation run in the latest continuation:
- `python -m ruff check peap\excel_handler.py tests\test_excel_schema_settings.py`
- `python -m pytest tests\test_excel_schema_settings.py`
- `python -m pytest tests`
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_excel_runtime_state_cleanup.json`

Remaining risk / blocker after the latest continuation:
- `peap\excel_handler.py` no longer mirrors state into compatibility globals, but it still keeps a module-level active runtime for legacy callers that omit explicit runtime injection
- config-coupling work is now less about `excel_handler.py` and more about remaining lazy default-config fallback sites plus parser-internal dict-heavy contracts
- historical notes below still mention compatibility globals because they capture the state of earlier batches

Completed in the latest continuation:
- updated `peap/checks.py` so self-check resolves its default config lazily instead of binding a module-level direct `config` import
- updated `peap/public_resource_deals.py` so default input/output path fallback and CLI construction use injected config/settings or lazy default fallback rather than a top-level direct `config` import
- updated `peap_postprocess/compare_regression.py` so default report-output paths can be derived from an injected config object and otherwise use lazy default fallback
- extended `tests/test_public_resource_deals_settings.py` and added `tests/test_compare_regression.py`

Validation run in the latest continuation:
- `python -m ruff check peap\checks.py peap\public_resource_deals.py peap_postprocess\compare_regression.py tests\test_public_resource_deals_settings.py tests\test_compare_regression.py`
- `python -m pytest tests\test_public_resource_deals_settings.py tests\test_compare_regression.py tests\test_runner_request_adapters.py`
- `python -m pytest tests`
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_aux_tools_lazy_config_refactor.json`
- `python bin\public_resource_deals.py --help`

Remaining risk / blocker after the latest continuation:
- these modules no longer rely on top-level direct `config` imports, but several still use lazy fallback to global runtime config for backward compatibility
- `peap\excel_handler.py` still mirrors runtime state into compatibility globals for legacy callers
- config-coupling work is now shifting from removing top-level imports to reducing compatibility globals and other implicit runtime state

Completed in the latest continuation:
- updated `peap/cli.py` so parser CLI defaults are built from an injected config object and only fall back to the global runtime config lazily inside the outer boundary
- updated `peap/download_cli.py` so downloader CLI parsing/list-task flow use an injected config object instead of a top-level direct `config` import
- updated `peap/download_tasks.py` with overridable default task-registry settings so registry fallback no longer depends on a top-level direct `config` import
- added `tests/test_cli_config_injection.py` for parser CLI defaults, downloader CLI defaults, and default task-registry settings override coverage

Validation run in the latest continuation:
- `python -m ruff check peap\cli.py peap\download_cli.py peap\download_tasks.py tests\test_cli_config_injection.py`
- `python -m pytest tests\test_cli_config_injection.py tests\test_config_runtime.py tests\test_runner_request_adapters.py`
- `python -m pytest tests`
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_cli_lazy_config_refactor.json`
- `python bin\download.py --list-tasks`

Remaining risk / blocker after the latest continuation:
- outer CLI entrypoints still own argparse parsing and summary-json emission by design; this batch only removed top-level direct config imports and improved config injection at the parser/build stage
- legacy global-config imports still remain in modules such as `peap\checks.py`, `peap\public_resource_deals.py`, and `peap_postprocess\compare_regression.py`
- `peap\excel_handler.py` still mirrors runtime state into compatibility globals for legacy callers

Completed in the latest continuation:
- added `LegacyLoggerSettings`, `build_legacy_logger_settings(...)`, and default-setting helpers in `peap/logger.py`
- updated legacy logger setup so it no longer requires a top-level direct `config` import and can instead resolve defaults from explicit settings
- updated `peap/excel_handler.py` with overridable default excel-schema settings so runtime/schema fallback no longer depends on a top-level direct `config` import
- added `tests/test_logger_settings.py` and extended `tests/test_excel_schema_settings.py` for explicit default-settings coverage

Validation run in the latest continuation:
- `python -m ruff check peap\logger.py peap\excel_handler.py tests\test_logger_settings.py tests\test_excel_schema_settings.py`
- `python -m pytest tests\test_logger_settings.py tests\test_excel_schema_settings.py`
- `python -m pytest tests`
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_logger_excel_settings_refactor.json`

Remaining risk / blocker after the latest continuation:
- `peap\logger.py` and `peap\excel_handler.py` now accept explicit settings and no longer use top-level direct `config` imports, but they still retain lazy fallback to the global runtime config for backward compatibility
- `peap\excel_handler.py` still mirrors runtime state into compatibility globals for legacy callers
- legacy global-config imports still remain in modules such as `peap\cli.py`, `peap\download_cli.py`, and `peap\download_tasks.py`

Completed in the latest continuation:
- added explicit `ParsedProject` routing properties in `peap/parsing.py` for project code/name/status/type/pre-disclosure access
- updated `peap/targeting.py` so `decide_output_file(...)` accepts `ParsedProject` directly and resolves source exchange / routing fields from that boundary object
- updated `peap/pipeline.py` so invalid-page checks, output-target routing, and pre-disclosure archive decisions now read from `ParsedProject` instead of raw dict key lookups in the main execution path
- extended `tests/test_parsing_contract.py`, `tests/test_parse_cache.py`, and `tests/test_targeting.py` for the new routing-property contract

Validation run in the latest continuation:
- `python -m ruff check peap\parsing.py peap\targeting.py peap\pipeline.py tests\test_parsing_contract.py tests\test_parse_cache.py tests\test_targeting.py`
- `python -m pytest tests\test_parsing_contract.py tests\test_parse_cache.py tests\test_targeting.py`
- `python -m pytest tests`
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_parsed_project_routing_refactor.json`

Remaining risk / blocker after the latest continuation:
- parser-internal implementations still originate exchange-specific raw dict payloads before the `ParsedProject` boundary, so the contract cleanup is downstream-first rather than end-to-end yet
- `peap\excel_handler.py` still mirrors runtime state into compatibility globals for legacy callers
- legacy global-config imports still remain in modules such as `peap\logger.py`, `peap\cli.py`, and downloader CLI entrypoints

Completed in the latest continuation:
- added `build_parsed_project(...)` in `peap/parsing.py` so `ParsedProject` now carries a structured `StandardProject` alongside the compatibility raw payload
- updated `peap/parse_cache.py` so cache hits rehydrate through the same parsed-project helper instead of reconstructing only the raw dict payload
- updated `peap/pipeline.py` so export mapping consumes `parsed.standard_record` rather than rebuilding the standard model downstream
- extended `tests/test_parsing_contract.py` and added `tests/test_parse_cache.py` for structured parse-result and cache-rehydration coverage

Validation run in the latest continuation:
- `python -m ruff check peap\parsing.py peap\parse_cache.py peap\pipeline.py tests\test_parsing_contract.py tests\test_parse_cache.py`
- `python -m pytest tests\test_parsing_contract.py tests\test_parse_cache.py`
- `python -m pytest tests`
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_parsed_project_contract_refactor.json`

Remaining risk / blocker after the latest continuation:
- parser outputs still originate as exchange-specific dict payloads inside parser implementations; the explicit contract now starts at `ParsedProject`, but parser-internal return shapes are not yet normalized
- `peap\excel_handler.py` still mirrors runtime state into compatibility globals for legacy callers
- legacy global-config imports still remain in modules such as `peap\logger.py`, `peap\cli.py`, and downloader CLI entrypoints

Completed in the current continuation:
- added `DownloadTaskRegistrySettings` in `peap/download_tasks.py` and updated the task registry helpers so registry page-size configuration can be injected explicitly
- added `DownloadRunnerSettings` plus `build_download_runner_settings(...)` in `peap/download_runner.py`
- updated downloader runner/request-adapter flow so `run_download_cli_args(...)` injects typed downloader settings into `run_download_request(...)`, and core runner logic now resolves task registry / output-root / chunk-state defaults from that settings slice
- extended `tests/test_runner_request_adapters.py`, `tests/test_download_runner.py`, and `tests/test_config_runtime.py` for downloader settings injection and runtime-config-derived task page size coverage

Validation run in the current continuation:
- `python -m ruff check peap\download_tasks.py peap\download_runner.py tests\test_runner_request_adapters.py tests\test_download_runner.py tests\test_config_runtime.py`
- `python -m pytest tests\test_runner_request_adapters.py tests\test_download_runner.py tests\test_config_runtime.py`
- `python -m pytest tests`
- `python bin\download.py --list-tasks`

Remaining risk / blocker after the current continuation:
- downloader task registry and runner now accept typed settings slices, but standalone downloader CLI still owns argparse parsing, logger session setup, and summary-json emission at the outer boundary
- legacy global-config imports still remain in modules such as `peap\logger.py`, `peap\cli.py`, and downloader CLI entrypoints
- `peap\excel_handler.py` still mirrors runtime state into compatibility globals for legacy callers

Completed in the current continuation:
- added `PublicResourceDealSettings` plus `build_public_resource_deal_settings(...)` in `peap/public_resource_deals.py`
- updated `default_input_dir(...)`, `default_output_file(...)`, `build_workbook(...)`, and `main(...)` so public-resource deal workbook generation can consume injected settings instead of relying on module-global config for manual input/output paths
- added `tests/test_public_resource_deals_settings.py` for config-derived path defaults and workbook-builder settings injection coverage

Validation run in the current continuation:
- `python -m ruff check peap\public_resource_deals.py tests\test_public_resource_deals_settings.py`
- `python -m pytest tests\test_public_resource_deals_settings.py`
- `python -m pytest tests`
- `python bin\public_resource_deals.py --help`
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_public_resource_deal_settings_refactor.json`

Remaining risk / blocker after the current continuation:
- `peap\public_resource_deals.py` now supports injected path settings, but it still bundles CLI parsing and workbook-building in one module; command-layer extraction is still open if this tool needs to be reused more broadly
- other global-config imports still remain in modules such as `peap\download_tasks.py`, `peap\logger.py`, and CLI entrypoints
- `peap\excel_handler.py` still mirrors runtime state into compatibility globals for legacy callers

Completed in the current continuation:
- added `ExcelOutputRuntime`-aware helper accessors in `peap/excel_handler.py` and made schema snapshots / validation / save paths accept an explicit runtime object
- updated `ExcelBatchWriter` and `save_to_excel(...)` to consume injected runtime state instead of reading writer schema/output columns only from module globals
- extended `ParserPipelineSettings` with `excel_output_runtime` and updated `peap/pipeline.py` / `peap/parser_runner.py` so parser execution instantiates batch writers from injected runtime objects
- updated `peap/checks.py` to validate mapping/writer contracts against an explicit loaded runtime instead of mutating global writer state via reloads
- extended `tests/test_excel_schema_settings.py` and `tests/test_runner_request_adapters.py` with runtime-isolation and runtime-injection coverage

Validation run in the current continuation:
- `python -m ruff check peap\excel_handler.py peap\pipeline.py peap\checks.py peap\parser_runner.py tests\test_excel_schema_settings.py tests\test_runner_request_adapters.py`
- `python -m pytest tests\test_excel_schema_settings.py tests\test_runner_request_adapters.py`
- `python -m pytest tests`
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_excel_output_runtime_refactor.json`

Remaining risk / blocker after the current continuation:
- `peap\excel_handler.py` now has an explicit runtime object, but it still mirrors that runtime into compatibility globals for legacy callers; a later cleanup can remove that mirror once the remaining call sites are migrated
- global-config imports still remain in other parser/downloader helpers such as `peap\public_resource_deals.py` and downloader-side services
- parser outputs are still dict-heavy even though writer/runtime contracts are cleaner

Completed in the current continuation:
- added `OutputTargetSettings` plus `build_output_target_settings(...)` in `peap/targeting.py`
- removed direct global-`config` usage from `peap/targeting.py` by resolving parser output files through injected settings
- extended `ParserPipelineSettings` so parser pipeline receives output-target settings explicitly
- updated `peap/pipeline.py` and `peap/parser_runner.py` to pass the injected output-target settings through the parser execution path
- added `tests/test_targeting.py` and extended `tests/test_runner_request_adapters.py`

Validation run in the current continuation:
- `python -m ruff check peap\targeting.py peap\pipeline.py peap\parser_runner.py tests\test_targeting.py tests\test_runner_request_adapters.py`
- `python -m pytest tests`
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_output_target_settings_refactor.json`

Remaining risk / blocker after the current continuation:
- parser output-file routing is injectable now, but `peap\excel_handler.py` still loads schema/config state from module-global config at import time
- parser flow now depends less on global config, but other helpers such as `peap\public_resource_deals.py` and downloader-side modules still need similar treatment

Completed in the current continuation:
- added `ParserPipelineSettings` plus `build_parser_pipeline_settings(...)` in `peap/pipeline.py`
- removed direct global-`config` usage from `peap/pipeline.py` by injecting parser cache / compare-report settings
- updated `peap/parser_runner.py` to pass injected parser pipeline settings and to forward `config_obj` into parser self-check
- updated `peap/checks.py` so self-check can use caller-provided config and can build parser pipeline settings explicitly
- extended `tests/test_runner_request_adapters.py` with parser pipeline settings and self-check config propagation coverage

Validation run in the current continuation:
- `python -m ruff check peap\pipeline.py peap\parser_runner.py peap\checks.py tests\test_runner_request_adapters.py`
- `python -m pytest tests`
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_pipeline_settings_refactor.json`

Remaining risk / blocker after the current continuation:
- parser pipeline construction is now injectable, but parser/output subsystems such as `peap\targeting.py`, `peap\excel_handler.py`, and parts of `peap\checks.py` still rely on module-global config
- config-coupling reduction has started in parser flow, but downloader core services still need the same treatment

Completed in the current continuation:
- added `ParserContext` plus shared `source_file` / `require_source_file()` helpers in `peap_parsers/base.py`
- updated `peap/parsing.py` to pass explicit parser context at construction time instead of mutating parser instances after init
- updated parser implementations that read local sidecars or cached tabs to use the shared source-file contract
- added `tests/test_parsing_contract.py`

Validation run in the current continuation:
- `python -m ruff check peap\parsing.py peap_parsers\base.py peap_parsers\__init__.py peap_parsers\public_resource.py tests\test_parsing_contract.py`
- `python -m pytest tests`
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_parser_contract_refactor.json`

Remaining risk / blocker after the current continuation:
- parser source-file access is explicit now, but parser outputs are still dict-heavy and rely on exchange-specific implicit field conventions
- broader parser-file `ruff` is still red on pre-existing issues in large modules such as `peap_parsers\beijing_standard.py` and `peap_parsers\guangzhou.py`

Completed in the newest batch:
- created `peap/download_oneclick.py` and moved one-click downloader orchestration / merged stage summary assembly there
- created `peap/daily_pipeline.py` and moved daily wrapper orchestration to direct Python API composition
- created `peap/parser_runner.py` and `peap_postprocess/postprocess_engine/runner.py` so parser / PPE CLIs share reusable structured runners
- simplified `bin/download_oneclick.py` and `bin/daily_pipeline.py` to thin CLI entrypoints
- added `tests/test_download_oneclick.py` and `tests/test_daily_pipeline.py`
- updated `peap_core/cli_support.py` to close old logger handlers so repeated in-process runs do not leak file handles

Validation run in the newest batch:
- `python -m ruff check peap_core\cli_support.py peap\download_runner.py peap\cli.py peap\parser_runner.py peap\download_cli.py peap\download_oneclick.py peap\daily_pipeline.py peap_postprocess\postprocess_engine\cli.py peap_postprocess\postprocess_engine\runner.py bin\download_oneclick.py bin\daily_pipeline.py tests\test_download_oneclick.py tests\test_daily_pipeline.py`
- `python -m pytest tests\test_download_oneclick.py tests\test_daily_pipeline.py tests\test_download_runner.py tests\test_download_execution_reporting.py`
- `python -m pytest tests`
- `python bin\download.py --list-tasks`
- `python bin\download_oneclick.py --help`
- `python bin\daily_pipeline.py --help`
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_after_oneclick_refactor.json`
- `python peap_postprocess\run_postprocess.py run --skip-unresolved-list`

Remaining risk / blocker after this batch:
- downloader one-click still carries argparse-shaped downloader args internally; parser/PPE runner boundaries are now explicit request objects
- full repo `ruff` is still red because of pre-existing issues outside this refactor batch

Completed in this turn:
- created `peap/download_reporting.py` and moved summary accumulation / dict conversion / aggregate printing there
- created `peap/download_execution.py` and moved auto-split chunk execution / chunk-state updates there
- updated `peap/download_cli.py` to call the new modules instead of owning those details directly
- added `tests/test_download_execution_reporting.py` for reporting helpers and split-task executor behavior

Completed in the most recent continuation:
- created `peap/download_task_flow.py` and moved task-level branching / split-plan loading / chunk-state context setup there
- added `DownloadTaskRunResult` in `peap/download_models.py`
- updated `peap/download_execution.py` so it only owns task-local execution state, not aggregate accumulation
- extended `tests/test_download_execution_reporting.py` with task-flow coverage

Completed in the latest batch:
- created `peap/download_runner.py` and moved top-level downloader run orchestration there
- added `DownloadRunResult` in `peap/download_models.py`
- simplified `peap/download_cli.py` to mostly CLI parse/setup + final summary write
- added `tests/test_download_runner.py`

Validation run in this turn:
- `python -m ruff check peap\download_cli.py peap\download_execution.py peap\download_reporting.py tests\test_download_execution_reporting.py`
- `python -m pytest tests\test_download_execution_reporting.py tests\test_download_split_modules.py`
- `python -m pytest tests`
- `python bin\download.py --list-tasks`
- `python -m ruff check peap\download_cli.py peap\download_execution.py peap\download_task_flow.py peap\download_reporting.py peap\download_models.py tests\test_download_execution_reporting.py`
- `python -m ruff check peap\download_cli.py peap\download_runner.py peap\download_task_flow.py peap\download_execution.py peap\download_reporting.py peap\download_models.py tests\test_download_runner.py tests\test_download_execution_reporting.py`

Remaining risk / blocker:
- full repo `ruff` is still red because of pre-existing import-order / unused-import / style issues in unrelated files such as `peap\downloaders\cquae.py`, `peap\downloaders\tpre.py`, `peap\excel_handler.py`, `peap\pipeline.py`, and `peap_postprocess\...`
- downloader CLI is now thin enough that the next high-value step is probably not more downloader-CLI decomposition, but replacing one-click wrapper coupling with direct Python API composition

Completed in the current batch:
- added `ParserRunRequest` in `peap/parser_runner.py` and split CLI-arg adaptation from parser execution
- added `PostProcessRunRequest` in `peap_postprocess/postprocess_engine/runner.py` and split CLI-arg adaptation from PPE execution
- updated `peap/daily_pipeline.py` to build typed parser/PPE requests instead of synthetic `argparse.Namespace` objects
- added `tests/test_runner_request_adapters.py` and extended `tests/test_daily_pipeline.py` to cover request conversion and typed orchestration calls

Validation run in the current batch:
- `python -m ruff check peap\parser_runner.py peap_postprocess\postprocess_engine\runner.py peap\daily_pipeline.py tests\test_daily_pipeline.py tests\test_runner_request_adapters.py`
- `python -m pytest tests\test_daily_pipeline.py tests\test_runner_request_adapters.py`
- `python -m pytest tests`
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_request_refactor.json`
- `python peap_postprocess\run_postprocess.py run --skip-unresolved-list`

Remaining risk / blocker after the current batch:
- downloader one-click still wraps downloader execution with argparse-shaped data, so the command-layer boundary is cleaner for parser/PPE than for downloader
- full repo `ruff` is still red because of pre-existing issues outside this refactor batch

Completed in the latest continuation:
- added `DownloadRunRequest`, `build_download_run_request(...)`, `run_download_request(...)`, and `run_download_cli_args(...)` in `peap/download_runner.py`
- updated `peap/download_oneclick.py` to store a typed downloader request inside `DownloadOneClickRequest`
- updated `peap/daily_pipeline.py` and `bin/download_oneclick.py` to build typed downloader requests instead of forwarding `argparse.Namespace`
- updated `peap/download_cli.py` to use the shared downloader CLI adapter
- extended request-boundary tests for downloader one-click / daily pipeline / CLI adapters

Validation run in the latest continuation:
- `python -m ruff check peap\download_runner.py peap\download_oneclick.py peap\download_cli.py peap\daily_pipeline.py bin\download_oneclick.py tests\test_download_oneclick.py tests\test_daily_pipeline.py tests\test_runner_request_adapters.py`
- `python -m pytest tests`
- `python bin\download.py --list-tasks`
- `python bin\download_oneclick.py --help`

Remaining risk / blocker after the latest continuation:
- downloader command-layer boundaries are cleaner now, but `peap/download_cli.py` still owns outer-boundary list-tasks printing and summary-json emission
- full repo `ruff` is still red because of pre-existing issues outside this refactor batch

Completed in the newest continuation:
- created `peap/download_cli_payloads.py` and moved downloader task-list line formatting, task-list summary payload construction, run summary payload construction, and final run-message formatting there
- updated `peap/download_cli.py` to use shared payload helpers for `--list-tasks` output and final run summary emission
- updated `peap/download_oneclick.py` to reuse the same download summary/error payload helpers for wrapper stage summaries
- added `tests/test_download_cli_payloads.py`

Validation run in the newest continuation:
- `python -m ruff check peap\download_cli.py peap\download_oneclick.py peap\download_cli_payloads.py tests\test_download_cli_payloads.py tests\test_download_oneclick.py`
- `python -m pytest tests`
- `python bin\download.py --list-tasks`
- `python bin\download_oneclick.py --help`

Remaining risk / blocker after the newest continuation:
- standalone downloader CLI still owns argparse parsing, logger session setup, and the final `write_summary_json(...)` boundary call
- the next highest-value step is probably no longer downloader CLI decomposition, but either parser contract cleanup or config-coupling reduction
- full repo `ruff` is still red because of pre-existing issues outside this refactor batch

Completed in the current continuation:
- added `ParsedProject.to_compat_payload(...)` in `peap/parsing.py` so downstream compare/export layers can consume a legacy-compatible view without reading parser-specific raw dicts directly
- updated `peap/compat_compare.py` so dual-run compare accepts `ParsedProject` inputs and resolves compare fields from the parsed-project compatibility boundary
- updated `peap/output_mapping.py` so excel payload mapping accepts `ParsedProject` directly, and updated `peap/pipeline.py` to pass parsed-project objects into compare/export steps instead of reaching into `.data` / `.standard_record`
- updated `peap/checks.py` so self-check covers the parsed-project boundary in the standard-mapping smoke path
- extended `tests/test_parsing_contract.py` with direct coverage for parsed-project compare/output mapping behavior

Validation run in the current continuation:
- `python -m ruff check peap\parsing.py peap\compat_compare.py peap\output_mapping.py peap\pipeline.py peap\checks.py tests\test_parsing_contract.py tests\test_parse_cache.py`
- `python -m pytest tests\test_parsing_contract.py tests\test_parse_cache.py`
- `python -m pytest tests`
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_parsed_project_downstream_boundary.json`

Remaining risk / blocker after the current continuation:
- parser implementations themselves still originate exchange-specific raw dict payloads; this batch only pushed more downstream consumers behind the `ParsedProject` boundary
- parse cache persistence still stores the compatibility raw payload and rehydrates `ParsedProject` from it, so the cache layer is not yet a fully structured parse-result store
- the final repo-hygiene / default-output closeout pass is still pending after this parser-contract batch

Completed in the current continuation:
- updated `peap_postprocess/build_type_unresolved_mapping_list.py` so the direct script path now defaults to `postprocess_external_template.json`, adds an explicit project-root bootstrap for reliable imports, and writes its default workbook under the configured external `output_dir`
- updated `peap_postprocess/run_postprocess.ps1` and `peap_postprocess/run_postprocess.bat` so wrapper defaults no longer point at the legacy in-repo `postprocess.json`
- updated `peap_postprocess/ppe_business_user_guide.md` so operator guidance matches the new external-template default and external audit/output locations
- added `tests/test_postprocess_defaults.py` to lock in the shared external-template default path and verify the default PPE config resolves output/audit directories under `PEAP_DATA`

Validation run in the current continuation:
- `python -m ruff check peap_postprocess\build_type_unresolved_mapping_list.py peap_postprocess\postprocess_engine\config.py peap_postprocess\postprocess_engine\runner.py tests\test_postprocess_defaults.py tests\test_compare_regression.py`
- `python -m pytest tests`
- `python peap_postprocess\build_type_unresolved_mapping_list.py --help`
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_repo_hygiene_closeout.json`
- `python peap_postprocess\run_postprocess.py run --skip-unresolved-list`
- `python bin\download.py --list-tasks`
- `python peap_postprocess\build_type_unresolved_mapping_list.py`

Remaining risk / blocker after the current continuation:
- legacy sample configs such as `peap_postprocess\ppe_config\postprocess.json` and `.yaml` still exist for manual/dev use and still point to repo-local paths if chosen explicitly, but they are no longer used by any default wrapper or smoke path
- parser internals and parse-cache storage are still partially dict-heavy, but they are now backlog items rather than closeout blockers
- full repo `ruff` is still red because of pre-existing issues outside the targeted refactor batch

Completed in the current continuation:
- added `ParserOutput` plus shared builder helpers in `peap_parsers/base.py` so parser-to-orchestrator output is no longer limited to an implicit raw-dict contract
- updated `peap/parsing.py` so live parsing accepts explicit parser-output objects and merges parser-provided structured fields with compatibility fallback data before building `ParsedProject`
- updated `peap_parsers/beijing_standard.py` and `peap_parsers/guangzhou.py` to emit explicit parser-output contracts, and kept router parsers compatible with either raw dict or parser-output returns
- extended `tests/test_parsing_contract.py` and `tests/test_parse_cache.py` with explicit parser-output, concrete exchange parser, and partial structured-cache rehydration coverage
- cleaned up nearby low-risk `ruff` issues in `peap_parsers/beijing_standard.py` and import ordering in `peap_parsers/guangzhou.py` while touching those files

Validation run in the current continuation:
- `python -m ruff check peap\parsing.py peap_parsers\base.py peap_parsers\__init__.py peap_parsers\beijing.py peap_parsers\beijing_standard.py peap_parsers\guangzhou.py peap_parsers\shanghai.py tests\test_parsing_contract.py tests\test_parse_cache.py`
- `python -m pytest tests`
- `python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_parser_output_contract_refactor.json`

Validation note in the current continuation:
- the first sandboxed self-check attempt failed with `PermissionError: [Errno 13]` while opening `C:\Users\ZDS-2603052\Desktop\PEAP_DATA\logs\parser_20260319_092425.log`; suspected cause was sandbox denial for writing outside the workspace into `PEAP_DATA\logs`
- reran the same self-check with escalated access and it passed (`14 checks, 0 failed`)

Remaining risk / blocker after the current continuation:
- most exchange parsers still build their compatibility payloads via internal dict mutation, but the parser-to-pipeline boundary is now explicit and test-covered
- full repo `ruff` is still red because of pre-existing issues outside the targeted refactor batch
- final delivery now mainly needs one short integrated closeout sweep, not another architectural refactor batch

Completed in the current continuation:
- updated `bin/daily_pipeline.py` and `bin/download_oneclick.py` so wrapper entrypoints lazily load runtime config and also accept injected `config_obj` in tests/in-process callers
- added `LazyLoggerProxy` plus file-handler fallback in `peap/logger.py` and switched `peap/excel_handler.py` to lazy logger binding so module import no longer eagerly opens default `PEAP_DATA` log files
- updated `peap/checks.py` and `peap/parser_runner.py` so parser self-check dry-parse respects request-level parse-cache overrides instead of always falling back to config defaults
- extended `tests/test_cli_config_injection.py`, `tests/test_logger_settings.py`, and `tests/test_runner_request_adapters.py`

Validation run in the current continuation:
- `python -m ruff check peap\logger.py peap\excel_handler.py peap\checks.py peap\parser_runner.py bin\daily_pipeline.py bin\download_oneclick.py tests\test_cli_config_injection.py tests\test_logger_settings.py tests\test_runner_request_adapters.py`
- `python -m pytest tests`
- `python bin\download_oneclick.py --help`
- `python bin\daily_pipeline.py --help`
- `python bin\download.py --list-tasks`
- `python bin\parse.py --self-check --log-dir .tmp_test_data_root\logs --no-parse-cache --summary-json .tmp_test_data_root\self_check_closeout_sweep.json`

Remaining risk / blocker after the current continuation:
- no mandatory refactor batch remains for the current stop line; further work is optional backlog unless a new regression is found
- full repo `ruff` is still red because of pre-existing issues outside the targeted refactor surface
- parser smoke commands still assume writable external `PEAP_DATA` paths by default, so sandboxed validation may need explicit `--log-dir` / cache overrides even though the codepath itself is now stable

Completed in the latest continuation:
- updated `peap_postprocess/postprocess_engine/runner.py` so `run_postprocess_request(...)` accepts injected config loading, engine construction, unresolved-list export, and logger lifecycle dependencies instead of hard-wiring those collaborators inside the function body
- extracted a shared PPE summary-dict helper so CLI/result payload shaping stays consistent while the runner becomes easier to test in-process
- added `tests/test_postprocess_runner.py` to cover success-path dependency injection, config-load failure short-circuiting, unresolved-export failure exit-code propagation, and structured PPE summary payload emission

Validation run in the latest continuation:
- `python -m ruff check peap_postprocess\postprocess_engine\runner.py tests\test_postprocess_runner.py tests\test_runner_request_adapters.py`
- `python -m pytest tests\test_postprocess_runner.py tests\test_runner_request_adapters.py`
- `python -m pytest tests`
- `python peap_postprocess\run_postprocess.py run --skip-unresolved-list`

Validation note in the latest continuation:
- the first sandboxed PPE smoke attempt failed with `PermissionError: [Errno 13]` while opening `C:\Users\ZDS-2603052\Desktop\PEAP_DATA\logs\postprocess\postprocess_20260319_094814.log`; suspected cause was sandbox denial for writing outside the workspace into the default external `PEAP_DATA\logs\postprocess` directory
- reran the same PPE smoke with escalated access and it passed (`mode=plan`, `discovered_files=4`, `failed_files=0`)

Remaining risk / blocker after the latest continuation:
- PPE runner boundaries are now explicit and test-covered, but rule-registry / rule-selection coverage is still shallower than parser/downloader boundary coverage
- full repo `ruff` is still red because of pre-existing issues outside the targeted refactor surface
- no mandatory refactor batch remains for the stated delivery stop line; remaining work is optional backlog triage rather than closeout-critical engineering

Completed in the latest continuation:
- added `tests/test_postprocess_rule_registry.py` to cover PPE known-rule ordering, mixed `RuleSettings`/dict registry plan building, disabled/unknown rule filtering, and list-mode config parsing with path normalization
- used fake rules for registry-contract assertions so ordering/selection behavior is verified without coupling the tests to heavyweight built-in rule internals

Validation run in the latest continuation:
- `python -m ruff check tests\test_postprocess_rule_registry.py tests\test_postprocess_runner.py tests\test_postprocess_defaults.py`
- `python -m pytest tests\test_postprocess_rule_registry.py tests\test_postprocess_runner.py tests\test_postprocess_defaults.py`
- `python -m pytest tests`

Remaining risk / blocker after the latest continuation:
- PPE registry/request boundaries now have explicit regression coverage, but deeper built-in rule behavior still depends mostly on smoke coverage rather than broad fixture-driven unit tests
- no mandatory refactor batch remains for the stated delivery stop line; remaining work is optional backlog triage rather than closeout-critical engineering

Completed in the latest continuation:
- cleaned up the last repo-wide `ruff` debt in the recommended validation scope by fixing import ordering in `peap\downloaders\cquae.py`, `peap\downloaders\sse_physical.py`, `peap\downloaders\tpre.py`, `peap\output_contract.py`, and `peap_postprocess\postprocess_engine\audit.py`
- renamed the ambiguous local variable in `peap\finance_fallback.py` so the fallback financial-metric detector is clearer and `ruff`-clean

Validation run in the latest continuation:
- `python -m ruff check peap_core tests config.py peap peap\downloaders peap_postprocess scripts`
- `python -m pytest tests`

Remaining risk / blocker after the latest continuation:
- the recommended repo-wide `ruff` gate is now green, so remaining cleanup is mainly deeper behavior coverage rather than style debt
- PPE registry/request boundaries now have explicit regression coverage, but deeper built-in rule behavior still depends mostly on smoke coverage rather than broad fixture-driven unit tests
- no mandatory refactor batch remains for the stated delivery stop line; remaining work is optional backlog triage rather than closeout-critical engineering

Completed in the latest continuation:
- renamed the main parser/downloader package from `app_v2/` to `peap/`, renamed `parsers/` to `peap_parsers/`, and renamed `postprocess_system/` to `peap_postprocess/`
- renamed entry scripts to drop `main_` / versioned legacy naming (`bin/parse.py`, `bin/download.py`, `bin/download_oneclick.py`, `bin/daily_pipeline.py`, `bin/public_resource_deals.py`, `bin/parser_regression.py`) and renamed `bin/_bootstrap.py` to `bin/bootstrap.py`
- merged the former `bin/main_download_auto_split.py` wrapper into the canonical downloader entrypoint; auto-split is now invoked via `python bin/download.py --auto-split`
- renamed docs to lower_snake_case (`docs/project_layout.md`, `docs/development_plan.md`, `docs/parser_rule_risk_report.md`, `docs/submission_guide.md`) and renamed PPE docs/config templates to consistent snake_case names under `peap_postprocess/`
- updated imports, tests, `pyproject.toml`, `.gitignore`, runtime/template references, and README command examples to the new canonical names

Validation run in the latest continuation:
- `python -m ruff check peap_core tests config.py peap peap_parsers peap_postprocess scripts bin`
- `python -m pytest tests`
- `python bin\download.py --list-tasks`
- `python bin\download_oneclick.py --help`
- `python bin\daily_pipeline.py --help`
- `python bin\parser_regression.py --help`
- `python bin\public_resource_deals.py --help`
- `python bin\parse.py --help`
- `python peap_postprocess\run_postprocess.py run --skip-unresolved-list`

Validation note in the latest continuation:
- `python bin\parse.py --self-check --html-root .tmp_test_data_root\download_verify --log-dir .tmp_test_data_root\logs --no-parse-cache --summary-json .tmp_test_data_root\self_check_naming_migration.json` failed in dry-parse because the sample file `.tmp_test_data_root\download_verify\cq_eq.html` did not pass exchange detection; this looked like a fixture/environment issue rather than an entrypoint naming regression, so `python bin\parse.py --help` was used as the direct renamed-entrypoint smoke check

Remaining risk / blocker after the latest continuation:
- the canonical repo naming migration is complete for the active code/documentation surface, but old external habits/scripts that still call removed names such as `bin\main_v2.py` or `bin\main_download_sse.py` will need to switch to the new canonical entrypoints
- deeper behavior coverage remains a better next investment than further naming churn
- no mandatory refactor batch remains for the stated delivery stop line; remaining work is optional backlog triage rather than closeout-critical engineering

Completed in the latest continuation:
- added `tests/test_naming_cleanup.py` to lock the naming migration by asserting retired package/entry/doc/template names are absent from the active repo surface and canonical replacements exist
- used the new test to formalize old-call cleanup: removed lingering active-surface references to retired names in docs/scripts/config metadata and treated the old `app_v2` / `postprocess_system` / `bin/main_*` names as fully retired

Validation run in the latest continuation:
- `python -m ruff check peap_core tests config.py peap peap_parsers peap_postprocess scripts bin`
- `python -m pytest tests`

Remaining risk / blocker after the latest continuation:
- active in-repo references to retired names are now test-guarded, but external operator habits or unpublished local wrapper scripts may still need manual switching to the new canonical entrypoints
- deeper behavior coverage remains a better next investment than further naming churn
- no mandatory refactor batch remains for the stated delivery stop line; remaining work is optional backlog triage rather than closeout-critical engineering

## 9. Validation Checklist For Every Refactor Batch

- `python -m ruff check ...`
- `python -m pytest tests`
- parser self-check if parser/shared config code changed
- PPE smoke if PPE/shared runtime code changed
- downloader smoke or task listing if downloader/shared runtime code changed

Recommended commands:

```powershell
python -m ruff check peap_core tests config.py peap peap\downloaders peap_postprocess scripts
python -m pytest tests
python bin\parse.py --self-check --summary-json .tmp_test_data_root\self_check_latest.json
python peap_postprocess\run_postprocess.py run --skip-unresolved-list
python bin\download.py --list-tasks
```

## 10. If Something Fails

- Do not immediately revert broad areas.
- isolate whether the failure is in:
  - shared runtime/config
  - downloader orchestration
  - parser pipeline
  - PPE CLI/runtime
- document the exact command, traceback summary, and suspected root cause here before ending the turn

## 11. Handoff Notes

- Dependencies are already installed in this environment, but a fresh environment should still run:

```powershell
python -m pip install -e .[dev]
python -m playwright install chromium
```

- The repo is not clean; unrelated changes exist.
- The integrated closeout sweep is complete; the recommended repo-wide `ruff` gate and full test suite are green.
- The highest-value next step, if any, is optional backlog triage (deeper parser-internal dict cleanup or broader PPE rule fixtures), not baseline quality-gate repair.
- The canonical code/documentation naming scheme now uses `peap/`, `peap_parsers/`, `peap_postprocess/`, and lower_snake_case entry/doc filenames; old `main_*` / `app_v2` / `postprocess_system` names should be treated as retired.
- Parser regression now defaults to `<data_root>/reference` rather than the stale `<data_root>/reference/parser_regression`, and the regression compare layer normalizes output-contract aliases, share-ratio formatting, additive remark segments, and benign industry enrichment.
- Current external reference package at `C:\Users\ZDS-2603052\Desktop\PEAP_DATA\reference` is wired in and validated. In sandboxed regression runs, Guangzhou pages still show 4 field diffs because those pages require remote tab fetches and the local reference package does not include the tab sidecars; the same two files parse correctly when network access is allowed.
- Validation snapshots from this continuation: sandboxed parser regression ended at `total_diffs=4` in `.tmp_test_data_root\actual_validation\parser_regression_after_fix_v3_summary.json`, while the escalated live parser regression ended at `total_diffs=0` in `.tmp_test_data_root\actual_validation\parser_regression_after_fix_v3_live_summary.json`.
- For this thread's stop condition, the project is at a reasonable delivery handoff point; remaining distance is mainly human closeout/reporting rather than another mandatory refactor batch.
- Keep behavior stable first, improve contracts second, and only then optimize aesthetics or deeper redesign.

Completed in the latest continuation:
- fixed desktop-side legacy skip semantics by normalizing historical `skip-cbex-otc-page` records/events out of `parse_failed` into `skipped`, so old北交互联/北交互联登录页不再在记录页和任务明细里伪装成解析失败
- corrected streaming archive behavior so copied HTML snapshots rewrite `*_files/...` asset references when the archive file name changes; this addresses archived pages opening without images even though the resource folder exists
- desktop runtime now uses a single workspace root under the user documents directory by default; database, raw html, archive, exports, logs, cache, and Playwright browsers all live together
- browser runtime detection and bootstrap now both use the configured workspace browser cache instead of mixing workspace and repo-local `.cache/ms-playwright`
- streaming export and record rendering were realigned to the CLI output contract so fields such as `类型` / `挂牌次数` come from the same canonical payload path as the legacy exporter
- desktop API no longer exposes a separate `download-ingest` launch route; the operator UI keeps a single date-range one-click execution path
- recent jobs / task detail were moved into a workspace-level sidebar, and saved mapping rules were changed from a freeform list into a toggleable filterable table view
- expanded downloader stage protocol with per-task context (`task_label`, `task_index`, `task_total`, `phase_percent`) and changed stage wording from “扫描网页并保存网页” to a real split of “扫描网页” then “保存网页”
- tightened manual/auto export semantics: export ids are now collision-safe within the same second, manual export returns `status/message`, and auto-export records “当前没有可导出的记录” instead of a fake “导出完成”
- reworked desktop UI structure:
  - overview header compressed into a compact single-row workbench bar
  - pending-mapping stat card now jumps to 映射补录
  - 数据记录页 moved 最近任务/任务明细 into a side column instead of burying them below the table
  - progress card now shows percentage, current object, and stage-specific counters instead of one static line
  - basic archive/export paths are now click-to-pick inputs; advanced readonly path fields are click-to-open/locate
  - mapping page now supports batch “一键导入待补项” into editable drafts plus bulk save/reprocess

Validation run in the latest continuation:
- `python3 -m unittest tests.test_app_config tests.test_app_service tests.test_runtime_dependencies tests.test_streaming_daily_pipeline tests.test_streaming_ingest tests.test_streaming_postprocess tests.test_streaming_store tests.test_streaming_export tests.test_download_oneclick`
- `python3 -m compileall desktop_backend peap`
- `node --check desktop_app/renderer.js`
- `node --check desktop_app/main.js`
- `node --check desktop_app/preload.js`
- `node --check desktop_app/backend_launch.js`
- `node --check desktop_app/build_backend_sidecar.js`
- `node --test desktop_app/backend_launch.test.js`

Remaining risk / blocker after the latest continuation:
- the latest desktop changes are code- and unit-level verified, but the new compact layout, click-to-pick path UX, batch mapping draft list, and task-progress readability still need a real Electron window pass on the operator machine
- historical local databases will self-normalize legacy skip rows/events when the desktop backend reads them, but only records/events touched by the skip markers are rewritten; there was no broad destructive migration beyond that narrow legacy correction path
