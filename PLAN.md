# 语义契约优先的桌面主链重构与扩展准备计划

## Summary

本轮不是按旧编号逐条打补丁，而是一次性把桌面主链重构到四个稳定契约之上：任务语义契约、范围契约、对象身份契约、fallback/cap 契约。实现目标是让现有 `listing` 工作流真正可用、可解释、可回归，同时把系统边界扩展到能容纳未来更多站点和 `deal` 记录族，而不是继续把“挂牌记录”写成默认宇宙。

本轮采用“一次性切换”的方式，但只交付 `listing` 的用户可见能力。系统内部和 HTTP/UI 契约现在就显式引入 `record_family`，默认值固定为 `listing`。`deal` 这轮不接入桌面主链，也不针对现有 `public_resource_deals` 做特化桥接，只保留足够宽泛的框架。

## Implementation Changes

### 1. 建立共享语义骨架，先把系统从“挂牌写死”提升到“记录族可扩展”

- 在共享模型中引入 `RecordFamily = "listing" | "deal"`，并把它纳入记录、任务、导出请求、事件上下文、记录查询作用域；本轮所有现有桌面入口默认填 `listing`。
- 新增 `peap/source_registry.py`，用注册表而不是散落分支管理站点适配能力。每个 source 只声明 `source_id`、站点标识、支持的 `record_family`、支持的 job 类型、runner/adapter 工厂；当前已有下载器全部注册为 `listing` source。
- 保留 `project_type` 作为 `listing` 家族内部的业务细分，不再把它混同为系统级记录分类。未来 `deal` 家族不复用 `project_type` 做上层判别。
- `output_contract` 继续保留现有输出 kind，但导出链路不再把 output kind 当成顶层记录语义；顶层统一以 `record_family -> scope -> export strategy` 驱动。

### 2. 重做任务语义契约，让任务生命周期先自洽

- 新增 `desktop_backend/progress_contract.py`，把 [`app_service.py`](/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/desktop_backend/app_service.py) 中 `latest_progress`、任务标题、终态解释、事件标题语义抽成纯逻辑；`app_service.py` 只负责 orchestration。
- 统一任务投影视图为 `ProgressView`，固定包含：`job_id`、`job_type`、`record_family`、`job_status`、`phase_code`、`phase_label`、`is_terminal`、`current_item_label`、`current_index`、`current_total`、`metrics`、`latest_stage_code`、`latest_stage_label`、`latest_stage_summary`。`metrics` 使用 `{key,label,value}` 列表，不再暴露一套对所有任务都带着 `archive_*` 的假通用字段。
- `launch_one_click()` 及其共享启动逻辑改成“未拿到有效 job_id 就不能返回 accepted success”。`job_created_callback` 是成功返回的硬门槛；如果后台线程在建账本前失败，直接返回明确失败结果，不允许空 `job_id` 假成功。
- `manual_import` 与 `mapping_refresh` 的成功判定改成“至少一条实际进入可接受完成态”才可落成成功。`parse_failed`、`postprocess_failed` 这类终态必须计入失败，不再通过 `imported_count/refreshed_count` 伪装成 `success_with_warnings`。
- `interrupted`、`failed`、`completed_with_warnings` 的终态上下文必须是干净终态。进入终态时清空运行中残留的 `current_item_label/current_index/current_total`，并且事件标题以终态优先，不再用过程 `stage` 覆盖终态语义。
- 前端新增 `desktop_app/renderer/tasks.mjs`，专门负责任务标题、任务 hint、事件标题与终态 copy；[`renderer.js`](/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/desktop_app/renderer.js) 只保留 DOM wiring 和数据绑定，不再内嵌任务语义判断。

### 3. 重做范围契约，让记录页、导出、空态解释永远来自同一 scope

- 新增 `desktop_backend/record_scope.py`，定义唯一的 `RecordScope`。字段固定为：`record_family`、`state`、`project_type`、`keyword`、`date_from`、`date_to`、`page`、`page_size`。`list_records()` 与 `run_export()` 都只能消费这个 scope，不允许再走“记录页一套、导出另一套”的隐式分叉。
- `run_export()` 的 HTTP payload 改为显式传 `scope`，而不是只传日期。桌面前端从当前记录视图构造导出 scope；本轮 UI 仍只支持 `listing`，但请求形状已经是 record-family-aware。
- 记录页默认业务筛选改为真正的“全量视图”：`record_family=listing` 且 `project_type=all`。`equity_transfer` 只作为用户显式筛选结果，不再作为首屏默认。
- `list_records()` 的 summary 拆成两套计数：`filtered_state_counts` 表示整个过滤结果集，`page_state_counts` 表示当前页。前端 summary、分页、空态说明一律使用 `filtered_state_counts`，不再混用页内计数。
- 空导出结果必须携带结构化解释，不只是一句字符串。导出结果在 `status=empty` 时返回 `empty_reason_code` 与 `scope_state_counts`，前端再用 `exports.mjs` 渲染文案；这样后续新增 `deal` 家族时仍能复用同一套 scope 解释。
- 前端新增 `desktop_app/renderer/exports.mjs`，专门负责从当前视图生成导出请求，以及根据 `empty_reason_code + scope_state_counts` 生成空导出解释。`records.mjs` 保留查询串和记录列表 summary 逻辑，但统一改为围绕 `RecordScope` 工作。

### 4. 重做对象身份契约，让失败对象不再被读路径改写

- 新增 `desktop_backend/record_identity.py`，定义失败对象与成功对象的不同身份规则。核心原则是：`archive_path` 是工件位置，不是身份锚点；读路径永远不能改写失败对象身份。
- 存储层新增不可变身份字段：`record_family`、`identity_anchor`、`source_identity_json`。`identity_anchor` 在首次 ingest 时生成并永久不变；`source_identity_json` 保存原始证据路径、原始来源标识、可用的 page/project token、初始 source 指纹。
- `business_key` 不再允许对无编号失败对象直接依赖当前 `source_file` 路径。失败对象的去重与重处理定位统一改用不可变 `identity_anchor/source_identity_json`，避免匿名归档后重新算出另一条业务键。
- `_repair_missing_archives_once()` 只允许修复可归档成功对象的工件位置，不允许对 `parse_failed/postprocess_failed` 记录做匿名归档式重写，不允许删除其原始失败现场，不允许在读路径下把失败对象切换到匿名 archive 副本。
- `reprocess_record()` 对失败对象必须优先使用原始证据路径；如果原始证据缺失，返回显式“不可恢复/证据缺失”错误，而不是静默切到匿名归档副本。
- 重新导入同一失败原始对象时，若其不可变身份未变，应当命中同一失败对象并新增 revision，而不是裂成第二条 `parse_failed` 记录。

### 5. 净化 HTTP 与 fallback/cap 合同，移除隐式上限与假空结果

- `get_job()` 不再内嵌一个被静默截断的 `events` 数组；任务详情与事件流分离，`GET /api/jobs/:id` 只返回 job summary，`GET /api/jobs/:id/events` 负责事件列表。
- 事件接口固定返回：`events`、`returned_count`、`total_count`、`truncated`。如果存在上限，必须显式告诉前端和用户；本轮统一事件分页/上限语义，不再保留 `100/200` 双上限。
- `get_job()` 与 `get_job_events()` 的 not-found 语义统一为 404，不允许再出现 “job 404，但 events 200 + []” 的双轨行为。
- `tests/test_app_backend.py` 不再依赖真实 `ThreadingHTTPServer` 绑定 `127.0.0.1`。改为在进程内调用 handler 的请求/响应 harness，避免 sandbox 和 CI 环境下的 loopback 限制，同时让 HTTP 契约测试真正稳定可回归。
- 所有默认值与 fallback 都要收敛成显式策略：首屏默认 scope、导出 scope、任务终态投影、失败对象恢复路径、事件上限。任何 fallback 只要会改变业务语义，就必须进入 contract 模块和测试，而不能继续散落在 `renderer.js` 或 `app_service.py` 分支里。

### 6. 前端结构与站点扩展准备同步收口，但 UI 仍只交付 listing

- [`renderer.js`](/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/desktop_app/renderer.js) 只保留页面状态管理、事件监听、API 调用和 DOM 拼装；任务语义、导出语义、记录 scope 解释全部下沉到 `tasks.mjs`、`exports.mjs`、`records.mjs`。
- 前端状态模型现在就显式保存 `record_family`，但 UI 仍只有 `listing` 工作流，不提前做 `deal` 页面、面板或菜单。
- 站点扩展只通过 source registry 加入；未来新网站适配不得再通过 `app_service.py`/`renderer.js` 的 if/else 增枝，而要作为 source capability 注册到统一入口。
- 本轮不把 `public_resource_deals` 接进桌面主链，也不围绕它做特殊桥接；它只作为“未来可能的 `deal` source”存在于架构说明和扩展约束里。

## Public APIs / Interfaces

- `RecordScope` 成为记录查询和导出的唯一作用域对象。HTTP 层与前端内部都使用同一字段集合：`record_family`、`state`、`project_type`、`keyword`、`date_from`、`date_to`、`page`、`page_size`。
- `record_family` 现在就进入 service/API/frontend 契约；本轮唯一允许值是 `listing`，但接口按枚举设计，不把它降格为隐藏常量。
- `ProgressView` 替代现有混杂的 `latest_progress` 字段集合。旧的 `archive_pending_count/archive_completed_count` 不再作为通用顶层字段保留。
- `GET /api/jobs/:id` 返回 job summary，不再夹带 inline events。
- `GET /api/jobs/:id/events` 返回统一分页/截断语义，并与 job not-found 保持一致。
- `run_export()` 返回结果在 empty 情况下提供结构化空态原因，而不是只返回非结构化字符串。

## Test Plan

- 服务层回归必须直接锁住：一键执行在未创建任务前不能返回成功；全失败 `manual_import` 必须是 `failed`；零实际修复的 `mapping_refresh` 必须是 `failed`；`interrupted` 终态不能残留运行中上下文；导出任务不能再投影成归档进度。
- 范围回归必须直接锁住：记录页默认 scope 是 `listing + all`；记录页与导出使用同一 `RecordScope`；`filtered_state_counts` 与 `page_state_counts` 分离；空导出 blocker 计数只来自当前 scope。
- 身份回归必须直接锁住：`overview()` / `list_records()` 不得改写失败对象身份；失败对象重处理必须读取原始证据路径；再次导入同一失败对象只新增 revision 不新增第二条记录；匿名失败对象必须保留可追溯身份信息。
- HTTP 合同回归必须直接锁住：`get_job` 与 `get_job_events` 的 not-found 一致；事件截断语义显式可见；不再有 `100/200` 双上限。
- 前端 Node 回归新增 `tasks.test.js` 与 `exports.test.js`。`records.test.js` 扩展到 default scope、summary 双计数、scope-to-export 对齐。
- 现有 `tests/test_app_service.py`、`tests/test_streaming_store.py`、`tests/test_app_backend.py` 保留为主回归面；后端 HTTP 测试改为无 socket harness，避免环境限制影响主合同验证。

## Assumptions

- 本轮只交付 `listing` 的桌面用户可见能力；`deal` 只是架构就绪，不做页面、不做工作流、不接入现有成交来源。
- 允许本轮对桌面内部 HTTP/service/frontend 契约做净化式调整；旧字段和旧默认语义不做兼容保留，只在同一发布内统一切换。
- 未来站点扩展统一走 source registry；未来 `deal` 能力统一走 `record_family`，而不是给 `listing` 语义继续打补丁。
- 文档与报告在代码稳定后同步更新，但代码设计不再围绕旧 issue 编号组织；issue 编号只作为验收映射，不作为模块边界。
