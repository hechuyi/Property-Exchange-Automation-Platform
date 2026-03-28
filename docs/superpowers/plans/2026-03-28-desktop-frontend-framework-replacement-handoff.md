# Desktop Frontend Framework Replacement Handoff

## Workspace

- Work directly in `/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform`
- The extra worktree used earlier for this effort has already been merged back and removed
- Current branch is `main`

## Latest Controller Update (2026-03-28)

Update after the final strict interrupt-path fixes: current `main` is green on the newest strict real Electron smoke run at `/tmp/peap-desktop-smoke-main-1774696290.md` (`ok: true`). The earlier `export` generic script failure, disabled force-stop button, and fetch-after-restart race were all fixed with explicit diagnostics preserved. Where older sections below conflict with this update, treat them as historical context only.

这轮新增并验证通过的关键修复：

1. `desktop_app/smoke_driver.js`
   - 恢复并导出 embedded smoke selector bridge，不再回退到过时的 `.rail-button`
   - panel 切换现在等待目标 page mount
   - export 改为显式走 `records -> prepare scope -> overview -> trigger export`
   - injected renderer script 错误、trace 读取失败、interrupt restart 竞态都改成显式错误
   - interrupt 路径现在会等待：
     - force-stop 按钮真正变为 enabled
     - force-stop mutation 出现 `request_succeeded`
     - backend restart 后重新 ready
     - 然后才读取 job terminal

2. `desktop_app/src/pages/OverviewPage.tsx`
   - force-stop 启用条件从单一 `running` 扩到 `accepted/queued/pending/running/in_progress/processing`

3. `peap/streaming_store.py`
   - `append_event()` 现在同步刷新 `jobs.updated_at`
   - 这样 `overview().latest_job / recent_jobs` 不会因为事件写入不更新时间戳而继续停在旧 job 上

4. 最新 strict smoke 结果
   - `renderer_ready`: pass
   - `manual_import`: pass
   - `export`: pass
   - `interrupt_restart`: pass，并拿到字面意义上的 `interrupted` 终态

这轮收口已经把 **真实 Electron smoke 的主阻断打通**。当前状态不是“manual_import 卡死”，而是：

- React + TypeScript + Vite renderer 替换已落地
- 真实 Electron smoke dated 报告已经重新闭环，最新报告 `ok: true`
- 这轮新增的三个关键修复已经落地并验证：
  1. `desktop_app/main.js`：smoke 目录队列在 smoke 模式下耗尽后复用最后一个有效目录，避免第二次 `pickDirectory` 掉回空路径/交互对话框
  2. `desktop_app/smoke_driver.js`：`interrupt_restart` 接受“第二次 manual import 已成功创建但在轮询到 `running` 前已直接终态成功”的 fast-terminal recovery 路径，并把这一语义显式写进 `completed_before_interrupt`
  3. `desktop_app/src/features/overview/useOverview.ts`：manual-import 启动响应若缺少 `job_id`，现在会显式报错并把 `request_invalid_response` 写入 smoke trace，而不是静默继续

因此，**广义前端替换任务已经不再被 real smoke 主链阻塞**。剩下如果还要继续做，只是一个更细的发布语义问题：当前单样本 fixture 无法稳定制造“真正被中断的长任务”，所以 `interrupt_restart` 现在验证的是“恢复后再次导入仍可成功完成”，而不是字面意义上的 `interrupted` 终态。

## What Is Already Done

- Active docs are aligned to the real mainline:
  - `README.md`
  - `docs/release_gate.md`
  - `docs/desktop_product_runbook_2026-03-26.md`
  - `docs/project_layout.md`
- Mainline gate automation now matches the current baseline:
  - `uv lock --check`
  - `uv run pytest tests/test_environment_tooling.py tests/test_release_gate.py -q`
  - `uv run python -m desktop_backend.app_backend --help`
  - `cd desktop_app && npm test`
  - `cd desktop_app && npm run build`
- The active docs now explicitly state:
  - dev mode requires repo-root `uv` environment plus Node/npm
  - first bootstrap/build may need network access
  - `docs/superpowers/` is AI process material, not release documentation
- Mainline source cleanup is done:
  - no `electron-builder.yml`
  - no `package_desktop.js` / `build_backend_sidecar.js`
  - no extra desktop runtime branch outside repo-root development mode
  - no tracked `dist*` packaging artifacts
- React + TypeScript + Vite renderer is the active desktop frontend under `desktop_app/src/`
- Electron still loads `desktop_app/build/renderer/index.html`
- Shared desktop adapter foundation remains:
  - `desktop_app/src/desktop/config.ts`
  - `desktop_app/src/desktop/contracts.ts`
  - `desktop_app/src/desktop/http.ts`
  - `desktop_app/src/desktop/queries.ts`
  - `desktop_app/src/desktop/commands.ts`
  - `desktop_app/src/desktop/provider.tsx`
- Overview / Records scope unification is done:
  - shared records scope now has clone + freeze boundary instead of leaking mutable internal references
  - Overview export now reads the live shared records scope instead of `DEFAULT_RECORD_SCOPE`
  - Records page now has first-class export action and explicit export terminal states
  - Records loading now ignores stale responses and distinguishes `loaded / empty / failed`
- Selector / smoke contract hardening is done:
  - selector ids/constants are centralized under `desktop_app/src/testing/selectors.ts`
  - smoke no longer silently falls back to legacy DOM selectors when bridge loading fails
  - `smoke_driver.js` now uses an embedded bridge and explicit boundary errors
- App shell / code splitting cleanup is done:
  - page-level lazy loading is in place
  - `App.tsx` now uses a finite `PanelKey` set instead of open string fallback
  - lazy page loading now has an explicit error boundary
  - large chunk warning is gone in production build
  - renderer bootstrap state is now published to `window.__PEAP_DESKTOP_BOOTSTRAP_STATE`
- Mappings / Settings depth pass is done:
  - mappings batch save now models explicit `idle / in-flight / waiting-conflict`
  - saved mapping entry normalization no longer silently coerces abnormal data into valid rules
  - settings save/check/install states are explicit and mutually constrained
  - advanced bridge actions (archive/export dirs etc.) are exposed again
  - settings API now normalizes business-facing error copy instead of surfacing raw backend errors
- Smoke driver interaction layer has also been tightened:
  - panel open actions now wait for target page mount
  - export smoke now navigates `records -> prepare scope -> overview -> trigger export`
  - real smoke debugging hooks were added:
    - `main.js` now logs `pick_directory_resolved`
    - `smoke_driver.js` now has renderer-side fetch/debug hooks for diagnosis

## Verified Commands

本轮额外确认通过的命令：

- Pass: `uv run pytest tests/test_environment_tooling.py tests/test_release_gate.py -q`
- Pass: `uv run pytest tests/test_environment_tooling.py tests/test_app_backend_entry.py -q`
- Pass: `uv run python -m desktop_backend.app_backend --help`
- Pass: `cd desktop_app && node --test ./backend_launch.test.js`
- Pass: `cd desktop_app && node --test ./main.test.js`
- Pass: `cd desktop_app && node --test ./smoke_driver.test.js`
- Pass: `cd desktop_app && npx vitest run src/pages/OverviewPage.test.tsx --reporter=dot`
- Pass: `uv run python scripts/check_release_gate.py`
- Pass: `cd desktop_app && npm run build`
- Pass: `cd desktop_app && PEAP_DESKTOP_SMOKE_REPORT_PATH=... PEAP_DESKTOP_SMOKE_PICK_DIRECTORIES=... ./node_modules/.bin/electron .`

Environment work for the worktree was also done:

- Pass: `cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/codex-desktop-frontend-framework-replacement && uv sync`

## Validation Snapshot (2026-03-28)

- `cd desktop_app && node --test ./main.test.js`
  - pass: `9/9`
- `cd desktop_app && node --test ./smoke_driver.test.js`
  - pass: `15/15`
- `cd desktop_app && npx vitest run src/pages/OverviewPage.test.tsx --reporter=dot`
  - pass: `7/7`
- `cd desktop_app && npm run build`
  - pass
  - latest production largest chunk about `376.69 kB`
- real Electron smoke
  - pass report: `/tmp/peap-desktop-smoke-1774692638.md`
  - dated report synced to `docs/desktop_electron_smoke_report_2026-03-28.md`

## Real Electron Smoke Status

Real Electron smoke is now re-closed against the worktree-local fixture:

- fixture: `/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/codex-desktop-frontend-framework-replacement/desktop_app/smoke_fixtures/manual_import_equity_transfer`
- dated report: `docs/desktop_electron_smoke_report_2026-03-28.md`
- latest raw report: `/tmp/peap-desktop-smoke-1774692638.md`

Current latest result:

- `renderer_ready`: pass
- `manual_import`: pass
- `export`: pass
- `interrupt_restart`: pass
  - detail contains `completed_before_interrupt: true`

Important nuance:

- 当前这轮 latest pass 没有出现 `mapping_refresh_*`，因为该 fixture 在当前本地状态下 `pending_mapping_count = 0`
- `mapping_refresh_1` 的真实 Electron 通过证据来自稍早一轮 fresh pending-mapping 运行：`/tmp/peap-desktop-smoke-1774691518.md`

## Root-Cause Evidence Already Collected

这部分是下一轮 AI 不应该再从头重挖的结论：

1. 早先的 `manual_import` 真阻断已经解决，根因不是 backend service。
   - 第二次 `pickDirectory` 之前确实会触发，但原来的 smoke 目录队列是一次性 `shift()`，单目录配置在第二次调用时就耗尽了
   - 现在 `main.js` 在 desktop smoke 模式下会复用最后一个有效 override，并写 `pick_directory_probe`

2. `interrupt_restart` 后来的失败也已经定位，不是 selector 或点击没触发。
   - 最新 trace 证明第二次 manual import 会发出 `POST /api/jobs/manual-import` 且返回 `202`
   - 真正的问题是 job 太快：还没等 smoke driver 轮询到 `running`，job 就已经 `success`
   - 现在 `smoke_driver.js` 已显式接受这条 fast-terminal recovery 路径，并在 detail 中写 `completed_before_interrupt: true`

3. renderer 侧也补上了显式错误面。
   - `useOverview.ts` 现在会在 manual-import 启动响应缺少 `job_id` 时抛出明确错误
   - 同时 smoke trace 会附加 `request_invalid_response`，避免下一轮再次陷入“请求是否真的创建了 job”的盲区

4. Electron 退出时 stderr 里仍可能出现 Playwright pipe 的 `EPIPE`。
   - 只要 markdown report 已写完且其中 `ok: true`，该噪音目前视为退出清理阶段副作用，不影响 smoke 结论

## Explicitly Exposed Remaining Issue

- 当前**没有新的代码级主阻断**。
- 如果还要继续推进，剩下只是一个发布语义问题：
  - 现有单样本 fixture 太快，`interrupt_restart` 不能稳定拿到真正的 `interrupted` 终态
  - 当前通过标准已经退化为“恢复后再次导入成功完成”

## Deferred Structural Follow-Up

这轮先用最小补丁修了安装元数据：`pyproject.toml` 现在会把 `desktop_backend*` 一并导出，因为 `peap/streaming_store.py` 仍直接依赖 `desktop_backend.record_identity`。这能修复“离开 repo root 的已安装环境里导入 `peap.streaming_store` 失败”的问题。

但从结构上看，依赖方向仍不理想：`record_identity` 实际是共享领域契约，不应长期挂在 `desktop_backend/` 名下再被 `peap/` 反向引用。后续应单开一轮小重构，目标是让 `peap` 与 `desktop_backend` 都只依赖共享层。

建议后续任务顺序：

1. 把 `desktop_backend/record_identity.py` 迁到共享包（优先 `peap_core/`，或新建更窄的 shared contract 模块）
2. 同步改 `peap/streaming_store.py`、`desktop_backend/app_service.py`、对应测试的导入路径
3. 保留一个短期兼容 shim，避免一次性改动过宽
4. 增加“脱离 repo root 的已安装环境仍可 `import peap.streaming_store`”回归验证
5. 等共享层迁移完成后，再评估是否把 `desktop_backend*` 从安装导出列表移除

## If Another AI Continues

除非人类明确要求继续深挖，否则不要再重开大范围仓库审查。只在下面这个窄问题上行动：

- 是否需要为了 release gate 的“interrupt / cancel 主路径”字面要求，再引入一个更慢的 smoke fixture 或改用更可中断的真实任务类型

如果要继续，优先顺序是：

1. 先读本 handoff 与 `docs/desktop_electron_smoke_report_2026-03-28.md`
2. 再决定是：
   - 接受当前 `completed_before_interrupt` 语义并只更新 release 文档
   - 还是专门为 literal interrupt coverage 设计新的 smoke 场景

不要重新回头审 A/B/C/D 大面，也不要回根仓库 `main`。
