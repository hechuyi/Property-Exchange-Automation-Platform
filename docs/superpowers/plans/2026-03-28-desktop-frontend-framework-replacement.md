# Desktop Frontend Framework Replacement Plan

## Summary

以 `Refine + Ant Design` 作为桌面 renderer 的正式前端框架，并按“一次替换”实施，不保留现有手写 DOM 页面作为长期共存方案。目标是把 `desktop_app` 从单文件 imperative renderer 切到 React/Refine 架构，同时保持 Electron 主进程、本地 Python backend、`window.peapDesktop` bridge、以及现有业务 API 语义基本不变。

实现策略是“前端重构，后端尽量不动”。后端继续作为本地 REST/command API 提供者；前端新增一层 typed adapter，把现有 `/api/overview`、`/api/jobs`、`/api/jobs/:id/events`、`/api/records`、`/api/mappings`、`/api/exports`、`/api/jobs/manual-import` 等端点规范化成 Refine 可消费的数据与 action 接口。只有在某个端点 shape 让前端适配层出现不合理复杂度时，才允许做小范围接口整形，但不改变业务语义。

## Key Changes

### 1. Renderer 技术栈切换

- 在 `desktop_app` 引入 `React + TypeScript + Vite + Refine + Ant Design`，renderer 改为打包产物加载，不再由 `index.html + renderer.js` 直接承载全部业务逻辑。
- 保留 `main.js` 与 `preload.js` 的 Electron 责任边界：
  - `main.js` 继续负责 backend lifecycle、窗口创建、IPC、smoke 启动。
  - `preload.js` 暂不改公开 bridge 语义，继续暴露 `getBackendConfig / openPath / showItemInFolder / pickDirectory / restartBackend`。
- `index.html` 收缩成 React mount 容器；现有手写 DOM 结构全部迁移到 React 页面与组件。
- `electron-builder.yml` 与打包脚本改为包含 Vite renderer 产物，而不是直接把 `renderer.js` / `styles.css` 当作唯一 UI 入口。

### 2. 前端应用结构与职责

- 用 Refine 构建单窗口桌面后台壳，页面明确拆成五类：
  - `OverviewPage`：一键执行、导出、手动导入、强制停止、运行环境状态、关键指标卡。
  - `TasksPage`：任务列表 + 事件流。
  - `RecordsPage`：记录筛选、分页、明细表格、归档/定位操作。
  - `MappingsPage`：待补映射、规则编辑、批量保存、重处理。
  - `SettingsPage`：基础设置、高级设置、运行环境入口。
- 不把 `overview` 和 `tasks` 硬塞成伪 CRUD。它们在 Refine 中作为 custom pages；`records` 和 `mapping entries` 作为资源型页面；`manual import / export / one-click / restart / reprocess` 作为 command actions。
- 新增前端 adapter 层，统一做三件事：
  - 从 `window.peapDesktop.getBackendConfig()` 初始化 base URL 和 token。
  - 适配后端 envelope 与错误模型，输出前端内部统一类型。
  - 把命令式端点包装成明确的 action 函数，而不是散落在页面里直接 `fetch`。
- 页面状态管理按“server state 优先”组织：
  - 列表/详情/轮询由 query 层管理。
  - 表单局部态只保留在组件内部。
  - 当前 `desktopState` 这类全局 mutable 对象不再保留为核心架构。

### 3. 后端接口策略

- 默认保持现有 HTTP API 不变，尤其是以下接口路径与主要语义不变：
  - `GET /api/overview`
  - `GET /api/jobs`
  - `GET /api/jobs/:id`
  - `GET /api/jobs/:id/events`
  - `GET /api/records`
  - `GET /api/mappings`
  - `POST /api/jobs/one-click`
  - `POST /api/jobs/manual-import`
  - `POST /api/exports`
  - `POST /api/mappings`
  - `POST /api/mappings/preview`
  - `POST /api/mappings/reprocess-pending`
  - `POST /api/records/:id/reprocess`
- 明确约束：v1 迁移不做后端业务重写，不更改 store/state machine，不重命名公共 API。
- 唯一允许的后端变更是“前端适配性整形”，而且必须满足两条：
  - 只调整 envelope 一致性或字段命名映射，不能改变业务规则。
  - 调整后要补后端契约测试，保证旧语义仍成立。
- 前端内部要定义稳定类型：
  - `BackendConfig`
  - `OverviewViewModel`
  - `JobListItem` / `JobEventItem`
  - `RecordRow`
  - `PendingMappingItem` / `MappingEntry`
  - `CommandResult`
  这些类型只存在于 renderer 侧，作为后端返回 shape 与 UI 组件之间的中间层。

### 4. 测试与验收重构

- 现有 `main/preload/backend_launch/backend_ready/package_desktop` 这类 Node 测试继续保留。
- renderer 测试体系切换到 React 组件/页面测试，覆盖至少以下行为：
  - overview 页面能显示 latest progress、runtime 状态、关键操作按钮。
  - tasks 页面能渲染任务列表并切换到事件流。
  - records 页面能按状态、业务类型、日期、关键字筛选并翻页。
  - mappings 页面能导入待补项、编辑草稿、触发 preview/save/reprocess。
  - command action 失败时显示产品级错误文案，而不是原始内部异常。
- 真实 Electron smoke 不能再依赖当前硬编码 DOM id 约定。迁移后要保留一套稳定的 automation hooks：
  - 页面级 route/key
  - 按钮级 `data-testid`
  - 关键列表/表单的稳定选择器
- 当前 `smoke_driver` 需要同步改造到 React 新 UI，但 smoke 路径本身不变：`manual-import -> mappings -> export -> interrupt/restart`。

## Test Plan

- 前端构建与单测：
  - renderer build 成功，Electron 开发启动成功。
  - React renderer 单测全绿，覆盖四个主面板和 command adapter。
- 桥接与集成：
  - `window.peapDesktop` 的公开方法在新 renderer 下仍可调用。
  - API adapter 能正确处理 token 注入、分页 query、capacity envelope、后端错误 payload。
- 端到端验收：
  - 一键执行主路径仍能从 overview 发起并在 tasks 中看到进度。
  - 手动导入后，待补映射能在 mappings 中完成并回刷。
  - records 页面能正确反映 `ready / pending_mapping / skipped / failed` 等状态。
  - 导出成功时能形成 artifact；失败/空导出时文案符合当前产品语义。
  - 强制停止后 backend 可恢复，overview/tasks 状态不出现假完成。
- 发布门槛：
  - 现有 Python 测试基线不回退。
  - `desktop_app` 的 Node/Electron 测试命令继续覆盖 main/preload/packaging。
  - 真实 Electron smoke 报告在新 UI 上重新闭环一次。

## Assumptions

- 采用 `Refine + Ant Design`，不考虑 `react-admin` 或 `Ant Design Pro` 作为本轮实施路线。
- 迁移方式是一次替换，不维护旧 renderer 与新 renderer 的长期双栈共存。
- 后端默认不改业务逻辑；若出现接口整形，范围仅限返回 envelope 一致化，不改路径与语义。
- Electron 主进程、preload bridge、桌面 backend 进程管理继续沿用当前模型，不引入 Tauri、Next.js、远程服务或浏览器版前端入口。
- renderer 新代码应使用 TypeScript；main/preload 可继续保留当前 CommonJS 风格，除非打包链要求局部调整。
