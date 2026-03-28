# 真实操作链路测试报告

日期：2026-03-23

## 1. 目标与范围

本次测试的目标不是再做一轮静态代码阅读，而是按桌面产品当前真实主路径逐条模拟操作，确认「用户点击之后实际会发生什么」，并找出实现逻辑、状态流转、异常处理和前后端联动中的断裂点。

本次以当前仓库声明的主产品路径为准：

- 前端：`desktop_app/`
- 本地后端：`desktop_backend/`
- 仍在产品运行链路上的引擎模块：`peap/`、`peap_parsers/`、`peap_postprocess/`

不把已经退役的旧 CLI 壳层当作主产品路径，但保留对其内部引擎逻辑的回归验证，因为桌面端仍然调用这些模块。

### 1.1 语义不变量路由表

后续高风险发现按三条主线和六类不变量回收。完整冻结口径见 [语义不变量系统性筛查设计](/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/docs/superpowers/specs/2026-03-25-semantic-invariant-screening-design.md)；本节只保留路由索引。

| 主线 | 六类不变量中的归属 | 典型已知编号 | 路由说明 |
| --- | --- | --- | --- |
| 任务语义主线 | 任务生命周期 | `5.1`、`5.15`、`5.17`、`5.21`、`5.32`、`5.37`、`5.41` | 任务创建、终态解释、失败/中断/提示一致性 |
| 范围语义主线 | 范围一致性、默认值 / 回退路径、截断 / 上限 / 容量 | `5.18`、`5.30`、`7.45`、`7.49`、`7.53`、`11.17` | 列表、筛选、分页、导出、空态、总数与默认范围 |
| 对象身份主线 | 对象身份、读写副作用 | `5.36`、`5.38`、`5.40`、`5.43` | 失败对象、重处理对象、读修重试路径上的同一性 |

只在三条件同时满足时新开 `5.x`：既有报告没有明确写过、能够稳定复现、且问题本质已经脱离既有主线裂缝；否则并入既有 `7.x / 11.x` 边界量化。

### 1.2 2026-03-25 契约回归关账

本轮只记录已经被主链契约回归真正关掉的旧 finding，以及仍然阻断发布信任的剩余 blocker。判断依据只认当前 `main` 上的自动化证据，不写“设计更优雅”之类无效结论。

| 归类 | 已关闭 finding | 关闭依据 |
| --- | --- | --- |
| 任务语义 | `5.15`、`5.17`、`5.21`、`5.32`、`5.37`、`5.41`、`5.42` | `tests.test_app_service.test_manual_import_all_failed_resolves_to_failed_not_success_with_warnings`、`tests.test_app_service.test_export_progress_uses_export_semantics_not_archive_semantics`、`tests.test_app_service.test_overview_terminal_state_does_not_keep_old_running_phase`、`tests.test_app_service.test_terminal_progress_clears_current_item_context`、`tests.test_app_service.test_mapping_refresh_zero_actual_repairs_resolves_to_failed`、`desktop_app/renderer/tasks.test.js` 中的 `progressPreset treats interrupted as terminal`、`eventTitle prefers terminal status semantics over stage semantics`、`formatProgressHint treats terminal download tasks as finished states` |
| 范围语义 | `5.18`、`5.30`，以及其边界量化 `7.45`、`7.49`、`7.53`、`11.17` | `tests.test_record_scope.test_record_scope_defaults_to_listing_all_and_pagination_defaults`、`tests.test_app_service.test_list_records_and_run_export_share_same_scope_contract`、`tests.test_app_backend.test_exports_endpoint_requires_scope_payload`、`desktop_app/renderer/records.test.js` 中的 `buildRecordsQuery defaults to listing + all` / `formatRecordsSummary prefers filtered_state_counts over page_state_counts for overview copy`、`desktop_app/renderer/exports.test.js` 中的 `buildExportRequestFromView carries current scope instead of date-only payload` / `formatEmptyExportMessage uses empty_reason_code and scope_state_counts` |
| 对象身份 | `5.36`、`5.38`、`5.39`、`5.40`、`5.43` | `tests.test_record_identity.test_identity_anchor_does_not_depend_on_current_source_file_path`、`tests.test_record_identity.test_pick_reprocess_evidence_path_prefers_original_evidence_path`、`tests.test_streaming_store.test_failed_record_identity_anchor_does_not_change_when_source_file_changes`、`tests.test_streaming_store.test_reimport_same_failed_source_reuses_same_record_and_adds_revision`、`tests.test_streaming_store.test_reimport_failed_record_merges_new_candidate_tokens`、`tests.test_app_service.test_overview_and_list_records_do_not_rewrite_failed_record_identity`、`tests.test_app_service.test_reprocess_failed_record_uses_original_evidence_path` |
| fallback / cap | `5.35` | `tests.test_http_contract.test_build_not_found_payload_uses_fixed_shape`、`tests.test_app_backend.test_missing_job_and_missing_job_events_both_return_404` |

仍然阻断发布信任的 blocker 只剩以下几组：

- 任务语义：`5.1` 一键执行在未创建任务时返回假成功；`5.12` 浏览器未就绪时后端仍接受启动；`5.24` 后端在 ready 前退出时桌面端仍加载主窗口。
- 范围语义：`5.13` 导出 `rebuild` 仍按增量语义运行；`5.27` 实际导入仍会把项目类型退化为 `未知`；`5.28` 北交互联品牌化 OTC 详情模板仍会被整页跳过；`5.29` 默认映射模板缺失仍会在真实导入中暴露。
- 对象身份：本轮契约回归后，报告中的高优先级身份类 blocker 已清空；后续如果再出现问题，必须先证明它不属于已关闭的 `5.36/5.38/5.39/5.40/5.43`。
- fallback / cap：`5.4` 无效导入目录仍返回 `500`；`5.7`、`5.11`、`5.23` 的上限/截断路径还没有把容量边界清晰投影给操作者。

## 2. 测试方法

### 2.1 语义不变量筛查框架

本报告后续的高风险发现只按三条主线收口，且问题归类遵守“只在三条件同时满足时新开 5.x”：既有报告没有明确写过、能够稳定复现、且问题本质已经脱离既有主线裂缝。更完整的冻结定义回链到 [语义不变量系统性筛查设计](/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/docs/superpowers/specs/2026-03-25-semantic-invariant-screening-design.md)。

完成标准如下：

1. 主链任务在所有终态下只剩一套一致解释。
2. 用户看到的集合、操作作用的集合、提示解释的集合一致。
3. 失败对象在读、修、重试、重处理四种路径下身份稳定且可追溯。

本次采用四层方法并行验证：

第一层是现有自动化测试全量回归，确认当前代码基线没有明显回归。实际执行了以下命令：

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/desktop_app
npm test
```

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform
.venv-desktop/bin/python -m unittest discover -s tests -v
```

第二层是服务层直接模拟，绕过 UI，直接调用 `AppService`，验证任务创建、状态互斥、设置保存、导入导出、映射回刷等真实业务入口。

第三层是 HTTP API 级模拟，使用 `ThreadingHTTPServer + build_handler(service)` 启动真实本地后端处理器，按桌面前端的请求方式发送 `GET/POST`，验证鉴权、响应码和返回体。

第四层是针对可疑路径做定向复现，包括：

- 一键执行在线程中提前失败但未创建任务的情况
- 反向日期区间
- 设置保存后首页一键执行表单的默认值是否同步
- 冷启动和后台重启后的任务事件面板首屏行为
- 手动导入对扩展名大小写的容忍性

## 3. 自动化基线结果

### 3.1 前端 Node 测试

`desktop_app/package.json` 中声明的 33 个测试全部通过，覆盖范围主要是：

- 后端启动命令解析
- 后端就绪探测
- 打包契约
- 渲染层 API header 注入
- 记录页查询参数构造
- 映射保存/批量保存纯函数行为
- 布局契约

结论：前端纯逻辑与布局契约当前没有自动化回归。

### 3.2 Python `unittest` 全量回归

共运行 202 个测试，全部通过，覆盖范围包括：

- `desktop_backend` 的配置、HTTP handler、服务层
- 流式日常管线
- 下载器拆分执行
- 存储层
- 流式导出
- 流式导入
- 映射规则后处理
- 运行时依赖检查
- 解析契约与输出契约

结论：核心模块现有单元与集成测试基线稳定，没有出现现成测试即可发现的明显回归。

## 4. 真实操作路径覆盖结果

### 4.1 启动与就绪检查

检查了 Electron 主进程、后端启动配置和就绪探测逻辑。当前实现采用：

- 主进程先起后端，再立即创建窗口
- 前端自己轮询 `/api/ready`
- `ready` 端点不要求 token
- 其他 API 要求 `X-PEAP-Desktop-Token`

该路径的主逻辑成立，且已有自动化测试覆盖。

### 4.2 设置读取与保存

通过真实 HTTP 请求验证了：

- `/api/settings/basic` 可以保存默认交易所、项目类型、并发数
- `/api/settings/advanced` 可以保存 `save_json` 和 `postprocess_config`
- 固定路径类字段会被后端强制回写成工作区派生值，而不是信任客户端提交值

后端行为符合设计边界，但前端存在设置保存后首页默认值不同步的问题，见第 5.2 节。

### 4.3 浏览器运行环境检查

通过真实 API 调用验证：

- `/api/runtime/dependencies` 可以正确返回浏览器安装状态、安装状态机和产品 readiness
- 已安装状态下，readiness 与 download readiness 一致

当前逻辑没有发现新的实现错误。

### 4.4 一键执行

一键执行主路径目前依赖：

- `desktop_backend.app_service.AppService.launch_one_click()`
- `peap.streaming_daily_pipeline.run_streaming_daily_pipeline()`

正常链路的契约在现有测试中覆盖较充分，包括：

- 不自动开启 refresh
- 不自动导出
- 启动前修复 legacy 视图
- 启动前修复 archive 链接
- 数据库文件丢失后的恢复

但异常链路存在实质性逻辑错误，见第 5.1 节。

### 4.5 导出 Excel

导出路径验证了以下行为：

- 空结果时不会崩溃，会返回 `empty`
- 如果没有 ready 记录但有 `pending_mapping`，消息会带待补映射数量
- 导出任务会被写入任务列表，并产生任务事件

当前没有发现新的主逻辑错误。

### 4.6 手动导入解析

验证了以下路径：

- 目录递归扫描
- `.html/.htm/.mhtml` 文件识别
- 大写扩展名 `.HTML/.HTM/.MHTML` 也能被识别
- 空目录会创建成功任务并立即结束
- 无效目录当前会返回 HTTP 500，并直接把路径文本作为错误消息返回

这里没有发现数据流错误，但错误语义偏粗糙：无效目录更像用户输入错误，语义上更适合 4xx，而不是 500。

### 4.7 映射预览、保存与批量回刷

验证了以下链路：

- 单条映射保存前会先 preview
- 覆盖已有规则必须显式确认
- 保存成功后会异步启动 `mapping_refresh`
- 批量重处理当前所有 `pending_mapping` 记录的逻辑成立
- 线程内重处理时使用 thread-local 锁避免自锁

当前没有再发现新的高优先级错误。映射表存储键按 `match_field + target_field + source_name` 建立，和后处理侧匹配模型是一致的。

### 4.8 记录列表、筛选、分页与单条重处理

验证了以下路径：

- 记录页支持状态、业务类型、日期区间、关键字筛选
- 分页元数据与本页汇总是区分的
- 展示字段与导出契约对齐
- 单条记录重处理会优先用 archive 文件，archive 不存在时回退到 source 文件

主逻辑成立，没有发现新的高优先级错误。

### 4.9 强制停止当前任务

当前强制停止的产品语义不是“取消当前任务”，而是：

- Electron 主进程重启本地后端
- 后端启动时把所有 `running` 任务统一标记为 `interrupted`

这个设计本身是自洽的，现有测试也覆盖了 stale running job 的中断恢复；本轮没有发现新的实现错误。

## 5. 已确认问题

### 5.1 P1: 一键执行在未创建任务时返回假成功

这是本轮最严重的问题，已经用真实调用复现。

#### 现象

当一键执行在后台线程中提前失败，且失败发生在 `create_job()` 之前时，前端/调用方仍然会收到：

```json
{
  "job_id": "",
  "db_path": ".../streaming_ingest.sqlite3",
  "job_type": "one_click"
}
```

随后 UI 会显示“已开始一键执行”，但任务列表实际上是空的。

#### 已复现场景

场景 1：底层管线在 `job_created_callback` 之前抛异常。

场景 2：用户填写反向日期区间，例如：

- `start_date = 2026-03-23`
- `end_date = 2026-03-22`

此时我用真实 `AppService.launch_one_click()` 和真实 HTTP `/api/jobs/one-click` 都复现了同样结果：

- HTTP 返回码：`202`
- 返回体：空 `job_id`
- `GET /api/jobs`：空列表

#### 根因

`AppService._launch_streaming_job()` 的返回条件是“线程已启动并等待 2 秒”，而不是“任务已创建成功”。

关键实现位于：

- `desktop_backend/app_service.py:1646-1698`
- `peap/streaming_daily_pipeline.py:217-230`
- `peap/streaming_daily_pipeline.py:259-272`

具体链路如下：

1. 服务层启动后台线程。
2. 服务层仅用 `ready.wait(timeout=2.0)` 等待 `job_created_callback`。
3. 但等待超时后，无论 `job_id` 是否为空，都会直接返回响应。
4. 同时，流式管线在日期反转时会在 `create_job()` 之前直接返回 `StreamingDailyPipelineRunResult(exit_code=2, job_id="")`。

因此出现“API accepted，但没有任务”的假成功状态。

#### 影响

- 用户会误以为任务已启动
- 任务页没有任何任务可追踪
- 前端状态提示与数据库状态不一致
- 后续轮询无法解释这次“启动成功但没有任务”的请求

### 5.2 P2: 保存默认设置后，首页一键执行表单不会同步刷新

#### 现象

用户在设置页修改：

- 默认交易所
- 默认项目类型
- 默认并发数

保存成功后，设置页显示的是新值，但首页一键执行区仍保留旧值，除非：

- 用户手动再改首页输入框
- 或完全重启应用

#### 根因

`loadSettings()` 里把首页表单默认值的回填做成了“一次性初始化”：

- `desktop_app/renderer.js:1129-1134`

而 `saveSettings()` 保存后只是再次调用 `loadSettings()`：

- `desktop_app/renderer.js:1449-1467`

由于 `actionDefaultsInitialized` 已经在首次加载后变成 `true`，后续保存设置不会再把新默认值同步到首页的一键执行表单。

#### 影响

- 用户看到“设置已保存”，但首页实际提交的仍可能是旧参数
- 产品层面会形成“设置页真值”和“首页提交值”分裂
- 很容易导致误跑错交易所、错业务类型或错并发数

### 5.3 P3: 冷启动和后台重启后，任务事件面板首屏会空白一个轮询周期

#### 现象

在冷启动或执行“强制停止当前任务并重启后台”之后，任务列表通常会先出现，但任务事件面板首屏会被清空，直到下一轮轮询或用户手动点一次任务项才恢复。

#### 根因

`loadInitial()` 并发执行：

```js
await Promise.all([loadOverview(), loadSettings(), loadJobs(), loadJobEvents()]);
```

但 `selectedJobId` 是在 `loadJobs()` 中第一次设置的，而 `loadJobEvents()` 在发现 `selectedJobId` 为空时会立即 `renderEvents([])`。

关键位置：

- `desktop_app/renderer.js:1048-1077`
- `desktop_app/renderer.js:1144-1147`

#### 影响

- 首屏任务详情短暂空白
- 给用户造成“任务列表有东西，但右侧没有详情”的错觉
- 在后台重启场景下体验尤其明显

### 5.4 P3: 手动导入无效目录返回 500，错误语义不合适

#### 现象

我用真实 HTTP 调用：

```json
POST /api/jobs/manual-import
{"input_dir": "/not/exist"}
```

返回：

- HTTP 500
- payload：`{"error": "/not/exist"}`

#### 问题点

逻辑上这是可预期的用户输入错误，不是服务内部崩溃，更适合：

- 4xx
- 明确的人类可读消息，例如“目录不存在”或“请选择有效目录”

这项问题不影响主流程正确性，但会降低错误提示的可理解性。

### 5.5 P2: 待补映射入口默认把用户导向错误的规则类型

#### 现象

当前记录进入 `pending_mapping` 的主要原因不是缺集团，而是缺“类型”。后处理在判定必填映射字段时，当前只要求 `类型`：

- `peap/streaming_postprocess.py:68-73`

但前端从待补映射列表点击“使用待补”时，会固定把映射表单预设成：

- 规则类型：`transferor_group`
- 来源名称：转让方名称

对应实现：

- `desktop_app/renderer.js:721-730`

批量导入待补项到草稿区时，默认规则类型也存在同样偏差。当前推断逻辑是：

- 如果已有 `隶属集团`，默认 `group_type`
- 否则默认 `transferor_group`

对应实现：

- `desktop_app/renderer.js:610-618`

这里依然不会把“无集团但缺类型”的常见场景默认成 `transferor_type`。

#### 问题本质

这不是单纯的 UX 偏好，而是默认操作方向与待补原因不一致。对于大量“缺类型”的记录，系统会优先把运营引到“补集团”而不是“补类型”，容易造成：

- 先录一条不解决出口约束的规则
- 误以为保存后应该解除待补，但实际上仍然待补
- 规则维护成本上升，运营需要额外理解四种规则类型的细分语义才能纠正默认值

#### 影响

- 待补映射入口的默认操作方向不可靠
- 常见场景下会增加一次甚至多次无效录入
- 容易让人误判为“映射回刷没生效”，实际上是录入了不解决问题的规则

### 5.6 P2: `conflict` 记录不会参与映射回刷

#### 现象

保存映射规则后，服务层只会从 `ready` 和 `pending_mapping` 两种状态里找需要回刷的记录：

- `desktop_backend/app_service.py:1079-1085`

这意味着状态为 `conflict` 的最新记录，即使它的转让方/集团名称正好命中新保存的规则，也不会进入映射回刷任务。

我已直接复现：

1. 先插入一条 `state="conflict"`、`转让方="华润测试公司"` 的记录。
2. 再保存规则 `transferor -> source_type = 央企`。
3. 返回结果中：

```json
{
  "affected_count": 0,
  "job_id": ""
}
```

也就是说规则保存成功，但这条 `conflict` 记录完全不会被回刷。

#### 问题本质

系统其他位置并没有把 `conflict` 当成彻底无效记录处理。相反：

- 记录列表会展示它
- 映射回刷任务本身把 `conflict` 视为可接受的成功状态之一

但“找待回刷记录”的入口又把它排除掉了，造成前后语义不一致。

#### 影响

- `conflict` 记录的集团/类型字段可能长期停留在旧值
- 保存映射后，用户会看到“当前没有匹配到需要回刷的记录”，但实际上有产品可见记录没有被更新
- 状态机对 `conflict` 的处理前后不一致

### 5.7 P3: 映射回刷和待补批量重处理都被静默截断在 5000 条

#### 现象

映射相关的两条记录查找路径都写死了 `limit=5000`：

- 映射规则保存后的受影响记录查找：`desktop_backend/app_service.py:1079-1085`
- 批量待补映射重处理：`desktop_backend/app_service.py:1087-1088`

当前实现不会向调用方暴露“总命中数是否超过 5000”，因此当命中记录数超过 5000 时：

- preview 的 `affected_count` 会被低估
- 实际回刷集合会被截断
- 用户看不到任何“仅处理了前 5000 条”的提示

#### 问题本质

这里不是单纯的性能保护，而是“有上限但无告知”。如果产品真实数据量增长到这个级别，行为会从“部分处理”伪装成“完整处理”。

#### 影响

- 映射回刷结果不完整
- 受影响数量展示失真
- 批量清理待补映射时会留下未处理尾部记录

### 5.8 P1: 只有集团、没有转让方的记录无法通过映射补齐类型

#### 现象

后处理应用映射时，`apply_mapping_entries()` 先取公司名：

- `company_name = _first_non_empty(resolved, COMPANY_FIELDS)`

随后如果公司名为空，立即返回，不再尝试任何集团规则：

- `peap/streaming_postprocess.py:260-262`

这意味着只要记录没有“转让方/融资方”，即使它已经带有 `隶属集团`，系统也不会去匹配现有的 `group -> source_type` 规则。

我已直接复现：

- 输入 payload：`{"项目编号":"X2","项目类型":"股权转让","隶属集团":"华润"}`
- 配置规则：`group=华润 -> source_type=央企`

执行 `run_record_postprocess()` 后，结果仍然没有 `类型`，只留下通用的“缺少类型，暂不能进入导出”提示，说明集团规则完全没有生效。

#### 问题本质

这是映射能力缺口，不是展示问题。对于一类真实记录，系统当前根本没有补齐路径：

- 单条重处理无效
- 映射回刷无效
- 录入集团级类型规则也无效

#### 影响

- 一部分仅含集团信息的记录会永久停留在 `pending_mapping`
- 用户会误以为“集团 -> 类型”规则失效
- 产品对集团级规则的支持是不完整的

### 5.9 P1: 集团缺失型待补记录在 UI 上也没有可行补救路径

#### 现象

待补映射列表渲染时，按钮“导入规则”只会把公司名带入：

- `desktop_app/renderer.js:703-730`

其中 `data-company` 来自 `pendingRecordCompany(record)`，该函数只看：

- `转让方`
- `融资方`
- `转让方名称`
- `融资方名称`

见：

- `desktop_app/renderer.js:606-607`

如果待补记录只有 `隶属集团`，没有公司名，则：

- 列表里会显示“公司：未识别”
- 点击“导入规则”后，表单会被强行预设为 `transferor_group`
- `sourceName` 为空
- 后续保存会直接报 `source_name is required`

#### 问题本质

后处理层已经无法靠集团规则解决这类记录；前端待补入口又进一步把用户引向一个必然失败的“转让方规则”表单。也就是说，这不是单点 bug，而是前后端共同形成的无解闭环。

#### 影响

- 用户无法从待补面板直接修复这类记录
- 系统没有给出“请录集团规则”之类的正确引导
- 这类记录只能停留在待补状态

### 5.10 P2: 批量导入待补项时，已知集团但缺类型的记录默认成 `group_type`，容易诱导写出过宽规则

#### 现象

草稿区默认规则类型推断逻辑是：

- 有 `隶属集团`：`group_type`
- 否则：`transferor_group`

见：

- `desktop_app/renderer.js:610-618`

但系统真正的规则优先级是：

- 先看 `transferor -> type`
- 再回退到 `group -> type`

这在现有后处理测试中已有体现。

#### 问题本质

当记录已经有集团、只是缺类型时，默认成 `group_type` 并不总是最安全的产品决策。成熟产品通常应优先建议更窄、更具体的规则，避免运营在不经意间写出一个覆盖整个集团的宽规则，而不是先写公司级规则。

这项问题没有前两项那样形成“完全不可修复”的死角，但会明显提高误建广义规则的概率。

### 5.11 P3: 待补映射列表被硬截断为 200 条，UI 不提示截断

#### 现象

后端对待补映射列表的返回写死了：

- `desktop_backend/app_service.py:1023-1025`

也就是只返回前 200 条 `pending_mapping`。

前端摘要则直接把收到的数组长度当成“当前待补总数”展示：

- `desktop_app/renderer.js:636-646`

因此当真实待补数大于 200 时，页面会显示“当前 200 条待补项”，但用户无法知道：

- 这是全量 200 条
- 还是被截断后的前 200 条

#### 问题本质

这里没有分页，没有“还有更多”标记，也没有总数。它不是单纯的性能优化，而是把截断伪装成完整结果。

#### 影响

- 运营会误以为待补项已经基本清空
- 批量导入草稿和批量重处理都只能针对首批可见记录操作
- 隐性漏处理会随着数据量增长快速恶化

### 5.12 P1: 浏览器运行环境未就绪时，后端仍接受一键执行

#### 现象

我把真实后端进程的浏览器缓存目录指到一个空目录，使 `/api/overview` 返回：

```json
{
  "product_readiness": {
    "ready": false,
    "download_ready": false,
    "browser_runtime_ready": false
  }
}
```

随后继续对同一个真实后端发送：

```json
POST /api/jobs/one-click
{
  "start_date": "2026-03-22",
  "end_date": "2026-03-22",
  "exchange": "sse",
  "project_type": "physical_asset",
  "concurrency": 1,
  "max_pages": 1
}
```

返回结果仍然是：

- HTTP 202
- 有效 `job_id`
- `GET /api/jobs` 中已经出现 `one_click` 任务

也就是说，“运行环境未就绪”只阻止了前端按钮，没有阻止服务层实际受理请求。

#### 根因

产品 readiness 的计算只存在于：

- `desktop_backend/app_service.py:481-499`

但一键执行入口：

- `desktop_backend/app_service.py:1632-1701`

并没有在服务层检查 `download_ready`。当前 gate 只存在于前端渲染层：

- `desktop_app/renderer.js:969-1037`
- `desktop_app/renderer.js:1162-1175`

#### 影响

- 通过 HTTP / 脚本 / 非标准前端入口都能绕过 gate
- “下载不可用”和“任务可创建”语义分裂
- 任务账本会出现本不该被允许启动的任务

### 5.13 P1: 导出的 `rebuild` 模式实际上仍按增量语义运行

#### 现象

我用真实 `StreamingStore + run_ready_export()` 构造了一条 `ready` 记录，然后连续执行两次：

```python
run_ready_export(store, ExportRequest(mode="rebuild", ...))
run_ready_export(store, ExportRequest(mode="rebuild", ...))
```

实际结果是：

- 第一次：`new_records = 1`，生成 1 个文件
- 第二次：`new_records = 0`，`changed_records = 0`，生成 0 个文件

这说明 `rebuild` 并不会重建当前筛选命中的全部记录，而是和增量导出一样依赖历史导出游标。

#### 根因

服务层默认把导出模式设成 `rebuild`：

- `desktop_backend/app_service.py:1521-1527`

但底层导出实现：

- `peap/streaming_export.py:139-205`

无论 `request.mode` 是什么，都会先读取 `get_exported_revision_map(cursor_key)`，然后跳过未变化 revision。

现有测试只覆盖了“默认 mode 字符串是 rebuild”：

- `tests/test_app_service.py:1570-1587`

以及一般导出输出契约：

- `tests/test_streaming_export.py:107-129`

但没有任何测试验证 `rebuild` 是否真的会重导全部记录。

#### 影响

- 用户选择“重建导出”时，得到的其实仍是增量结果
- “增量/重建语义”被破坏
- 在数据纠偏、重跑和交付复核场景下会直接产出缺文件结果

### 5.14 P2: 缺失归档修复只会在单次进程生命周期内执行一次

#### 现象

我用真实 `AppService` 复现了下面的过程：

1. 插入一条 archive 缺失、source 仍存在的记录
2. 第一次调用 `overview()`，系统成功把归档补回
3. 手工删除刚修复出来的 archive 文件
4. 第二次调用 `overview()`，文件仍然缺失，没有再次修复

这说明 repair 不是“按需修复”，而是“进程启动后只扫一遍”。

#### 根因

`_repair_missing_archives_once()` 使用一次性标志位：

- `desktop_backend/app_service.py:353-421`

只要 `_archive_repair_attempted` 变成 `True`，后续 `overview()`、`list_records()`、`launch_one_click()` 和 `run_export()` 虽然都会再次调用该函数，但会被立刻短路。

#### 影响

- 运行中的新归档丢失不会自愈
- 用户在不重启后端的情况下没有恢复路径
- “可恢复性”只在第一次启动时成立，运行期不成立

### 5.15 P2: 手动导入全部解析失败时，任务仍记为 `success_with_warnings`

#### 现象

我启动了真实后端 HTTP 进程，对一个仅包含普通占位 HTML 的目录执行：

```json
POST /api/jobs/manual-import
{
  "input_dir": ".../manual-import-fixture"
}
```

最终任务结果是：

- `status = "success_with_warnings"`
- `downloaded_count = 1`
- `persisted_count = 0`
- `exception_count = 1`
- `summary.imported_count = 1`
- `summary.failed_count = 1`

同时记录列表中唯一一条记录的状态是：

- `parse_failed`

事件流里这条失败文件对应的结束事件文案仍然是：

- `label = "手动导入完成"`
- `state = "parse_failed"`

也就是说，整批没有任何成功导入记录，但任务账本仍把它记成“成功但有警告”，并把失败文件算进了 `imported_count`。

#### 根因

手动导入作业在拿到 `result` 后会先做：

- `imported += 1`

再根据 `state` 判断是否失败：

- `desktop_backend/app_service.py:1348-1360`

最终状态又按“`failed > 0` 且 `imported > 0`”决定成 `success_with_warnings`：

- `desktop_backend/app_service.py:1372-1385`

这里的 `imported` 实际表示“处理过文件”，不是“成功导入记录”。但字段名、任务状态和事件文案都在把它表述成成功导入。

#### 影响

- 全失败批次会被伪装成部分成功
- 任务摘要里的 `imported_count` 含义失真
- UI / 任务账本 / 记录状态三层语义不一致

### 5.16 P2: `conflict` 在真实运行中会被立即折叠成 `ready`，已成为幽灵状态

#### 现象

我直接用真实 `AppService + StreamingStore` 运行链路插入了一条：

- `state = "conflict"`
- `findings = [{"type": "archive_conflict", ...}]`
- 其余导出必需字段完整

随后第一次读取记录列表时，返回行已经不是 `conflict`，而是：

```json
{
  "state": "ready",
  "status_label": "已录入",
  "status_detail": "归档文件曾同名，当前文件为 G32025SH1000194__conflict1.html"
}
```

同时：

- `overview().record_state_counts.conflict = 0`
- `list_records({"state": "conflict", ...}).total_count = 0`

这说明当前不是“前端没有做状态筛选”这么简单，而是 `conflict` 在真实读路径第一次经过服务层时就被吞并成了 `ready`。

#### 根因

`overview()` 和 `list_records()` 都会先调用 `_normalize_legacy_views()`：

- `desktop_backend/app_service.py:342-351`
- `desktop_backend/app_service.py:794-798`

而 `_normalize_legacy_views()` 内部调用的 `normalize_required_mapping_states()` 会把 `ready / pending_mapping / conflict` 三种状态统一重新判定，只要没有映射缺口，就强制回写成 `ready`，并不会保留 `conflict` 这个终态：

- `peap/streaming_store.py:503-540`

但系统其他路径仍在把 `conflict` 当成独立状态使用，例如：

- 映射回刷和批量重处理把 `conflict` 视为可接受完成态：`desktop_backend/app_service.py:1190-1206`、`desktop_backend/app_service.py:1353-1359`
- 记录页摘要格式器和样式仍预留了 `conflict`：`desktop_app/renderer/records.mjs:40-46`、`desktop_app/styles.css:732`
- 测试也显式把 legacy `conflict` 回写成 `ready`，同时保留冲突细节：`tests/test_app_service.py:285-331`、`tests/test_app_service.py:1084-1115`
- 记录页状态筛选器又根本没有 `conflict` 选项：`desktop_app/index.html:241-248`

这不是孤立边角，而是状态机、读模型和前端筛选三处语义已经分裂。

#### 影响

- 用户无法按 `conflict` 状态检索、统计或复核归档重名记录
- 归档重名在产品表面会被伪装成“已录入”
- 任何后续依赖 `conflict` 独立语义的修复、回刷或治理逻辑都无法稳定建立在当前 API 输出之上

### 5.17 P2: 导出任务的 `latest_progress` 被错误投影成归档进度

#### 现象

我直接创建了一个真实 `export_excel` 任务，并写入：

- `downloaded_count = 12`
- `persisted_count = 2`
- 运行中事件：`stage = "exporting"`

此时 `overview().latest_progress` 返回的是：

```json
{
  "phase_code": "archive_pending",
  "phase_label": "正在存档",
  "archive_pending_count": 10,
  "archive_completed_count": 2
}
```

随后我把该任务正常落成：

- `status = "success"`
- `summary.new_records = 10`
- `summary.changed_records = 2`
- `summary.artifacts.length = 2`

再次读取 `overview().latest_progress`，结果虽然 `phase_code = "completed"`，但仍然残留：

- `archive_pending_count = 10`
- `archive_completed_count = 2`

这两个字段对导出任务都没有稳定业务语义，更不应该在成功结束后继续表示“还有 10 条待归档”。

#### 根因

`_build_latest_progress()` 当前没有按 `job_type` 分支，而是对所有任务一律用：

- `archive_pending_count = downloaded_count - persisted_count - skipped_count - exception_count`
- `archive_completed_count = persisted_count`

对应实现：

- `desktop_backend/app_service.py:692-724`
- `desktop_backend/app_service.py:773-783`

因此只要导出任务写入了 `downloaded_count / persisted_count`，它就会被 API 当成“归档型任务”投影；运行态还会优先落到 `phase_code = "archive_pending"`，压过真实的 `stage = "exporting"`。

前端虽然对导出任务的终态摘要做了局部特殊处理，但它依赖 `phase_code` 已经正确，无法修正运行中的错误阶段，也无法消除 API 残留的伪归档计数：

- `desktop_app/renderer.js:345-365`

当前测试也只覆盖了下载任务的 `archive_pending` 投影，没有覆盖导出任务的 `latest_progress` 语义：

- `tests/test_app_service.py:907-943`

#### 影响

- 导出任务在运行时会被错误展示成“正在存档”
- `overview.latest_progress` 不能被可靠地当作跨任务类型的一致进度接口
- 后续仪表盘、自动化或外部调用方如果直接消费该字段，会拿到错误的任务语义

### 5.18 P3: 记录页首屏默认不是“无筛选查看”，而是偷偷预筛成股权转让

#### 现象

记录页的方法学检查项里，`26. 无筛选查看` 的产品含义应当是“首屏进入时看到全部记录”。但当前前端默认查询并不是 `project_type=all`，而是：

```text
state=all&project_type=equity_transfer&page=1&page_size=50
```

我做了一个最小运行复现：同一库里插入两条 `ready` 记录，分别属于：

- `股权转让`
- `实物资产`

然后把上面的默认查询串直接送入真实 `list_records()`，结果返回只有 1 条；而显式传 `project_type=all` 时返回 2 条。

也就是说，用户首次进入记录页时，系统展示的不是“全部记录”，而是“全部股权转让记录”。

#### 根因

这个行为不是偶发，而是前端默认值在三个位置被写死了：

- `desktop_app/index.html:230-237` 把 `equity_transfer` 放在业务筛选器的首个选项
- `desktop_app/renderer/records.mjs:6-20` 的 `buildRecordsQuery()` 默认参数就是 `projectType = "equity_transfer"`
- `desktop_app/renderer.js:1091-1096` 的 `loadRecords()` fallback 也写死成了 `equity_transfer`

因此只要用户没有手动改筛选器，记录页首屏就一定不是全量视图。

#### 影响

- 记录页首屏总数会系统性低估
- 非股权项目在首屏会被无声隐藏，用户容易误判“没有记录”
- 方法学中的“无筛选查看”当前实际上未被满足

### 5.19 P1: 数据库文件丢失后，系统会静默重建成空库并伪装成正常首启

#### 现象

我做了两层真实复现。

第一层是直接运行真实 `AppService`：

1. 先保存基本设置：
   - `default_exchange = cbex`
   - `default_project_type = physical_asset`
   - `default_concurrency = 7`
2. 再插入 1 条 `ready` 记录。
3. 然后直接删除 `streaming_ingest.sqlite3`。
4. 再调用 `overview()`、`get_basic_settings()`、`get_advanced_settings()`。

结果是：

- 原先的记录数从 `1` 变成 `0`
- `recent_jobs = []`
- `latest_progress.phase_label = "暂无任务"`
- 基本设置被回退成默认值：
  - `default_exchange = all`
  - `default_project_type = all`
  - `default_concurrency = 4`

第二层是启动真实 HTTP handler 后再删库。删库前后两次请求：

- `GET /api/overview`
- `GET /api/settings/basic`

返回码都仍然是 `200`。删库后的响应只是“空记录 + 默认设置”，没有任何告警字段、错误码或恢复提示。

这说明当前实现不是“检测到数据库丢失并向用户告警”，而是把“数据库消失”静默降级成了“全新工作区”。

#### 根因

`StreamingStore` 的连接层会在每次连接时直接：

- `sqlite3.connect(self.db_path)`
- `self._ensure_schema(conn)`

对应实现：

- `peap/streaming_store.py:226-238`

因此数据库文件一旦不存在，下一次任何读操作都会自动创建一个全新的空库。

而服务层的设置读取与概览读取又都按“取不到就给默认值”处理：

- `desktop_backend/app_service.py:501-516`
- `desktop_backend/app_service.py:518-546`

所以最终用户看到的是一套语义完整但数据全空的正常响应，而不是“数据库已丢失”。

#### 影响

- 记录、任务、设置会在无告警的情况下全部消失
- 用户无法区分“第一次启动”和“数据库异常丢失”
- 这是典型的静默数据丢失，会诱导用户在错误前提下继续操作
- 一旦用户继续写入，新空库会覆盖异常现场，恢复难度进一步上升

### 5.20 P2: 运行中再次启动会返回 HTTP `500`，把任务互斥冲突伪装成服务器异常

#### 现象

我用真实 HTTP handler 做了“运行中再次启动”复现：

1. 先对一个包含 400 个 HTML 的目录发起：

```json
POST /api/jobs/manual-import
```

接口正常返回 `202`，并创建了 `manual_import` 任务。

2. 在该任务仍运行时，立即再发起：

```json
POST /api/exports
POST /api/jobs/one-click
```

两条请求都返回：

- HTTP 状态码：`500`
- 返回体：`{"error": "已有执行中的任务：手动导入解析"}`

也就是说，系统实际上已经正确识别出“当前有执行中的任务”，但用户侧拿到的却是服务器内部错误，而不是可恢复的忙碌态。

#### 根因

服务层通过 `_reserve_mutating_job()` 做全局互斥，冲突时直接抛 `RuntimeError`：

- `desktop_backend/app_service.py:1037-1043`

而 `manual_import`、`run_export` 和 `launch_one_click` 都直接走这条互斥路径：

- `desktop_backend/app_service.py:1388-1435`
- `desktop_backend/app_service.py:1517-1520`
- `desktop_backend/app_service.py:1641-1644`

HTTP 层没有把这种“业务忙碌冲突”单独映射成 409/423 一类的业务响应，而是把所有普通异常一律吞成：

- `HTTPStatus.INTERNAL_SERVER_ERROR`

对应实现：

- `desktop_backend/app_backend.py:149-175`

#### 影响

- 用户无法区分“稍后重试即可”的忙碌冲突与真正的后端故障
- 前端只能把这类可预期冲突当成异常提示
- 自动化调用方无法基于状态码做正确重试或等待策略

### 5.21 P3: 任务被中断后，`latest_progress` 仍残留上一条运行中的任务上下文

#### 现象

我做了真实进程级复现：

1. 启动真实 `desktop_backend.app_backend` 进程。
2. 发起一个大批量 `manual_import`。
3. 在处理中直接杀掉后端进程。
4. 用同一工作区重启后端，再读取：
   - `GET /api/overview`
   - `GET /api/jobs/<job_id>`

结果表明任务主状态已经正确变成：

- `job.status = "interrupted"`
- `latest_progress.phase_code = "interrupted"`
- `latest_progress.phase_label = "已中断"`

但同一份 `latest_progress` 里仍然保留了上一条运行中事件留下的上下文，例如：

- `current_task_label = "137.html"`
- `task_index = 44`
- `task_total = 300`

这会把一个已经终止的任务继续渲染成“像是停在某个处理中间步骤”，而不是一个干净的终态。

#### 根因

`_build_latest_progress()` 会先从最近的运行阶段事件里提取：

- `current_task_label`
- `task_index`
- `task_total`

对应：

- `desktop_backend/app_service.py:698-700`

当 `job_status == "interrupted"` 时，它只覆盖：

- `phase_code`
- `phase_label`
- `phase_percent`

但没有清空上述运行中上下文字段：

- `desktop_backend/app_service.py:714-717`

因此中断态会和旧的运行态 payload 混在同一个投影里。

当前测试只断言了中断后的 `phase_code/phase_label`，没有覆盖这些残留字段：

- `tests/test_app_service.py:850-873`

#### 影响

- 中断任务的终态投影不干净，容易误导用户以为还停在某个文件或某一页
- `latest_progress` 不能被稳定当作“当前真实终态”的单一来源
- 前端如果展示 `current_task_label/task_index`，会在终态继续带出陈旧上下文

### 5.22 P2: 同一秒内高频创建任务时，最近任务列表会把最旧任务排到最前面

#### 现象

我直接用真实 `AppService + StreamingStore` 在同一秒内连续创建并完成了 35 个任务，每个任务都带一个递增编号 `index=0..34`。

按成熟产品预期：

- `list_jobs(limit=20)` 应该返回最近 20 个任务，也就是大致 `34..15`
- `overview().recent_jobs` 应该返回最近 5 个任务，也就是大致 `34..30`

但实际结果是：

- `list_jobs(limit=20)` 返回的是 `0..19`
- `overview().recent_jobs` 返回的是 `0..4`

也就是说，在高频场景下，“最近任务”实际上变成了“最早任务”。

#### 根因

任务时间戳只有秒级精度：

- `peap/streaming_store.py:158-159`

而任务列表排序只按：

- `updated_at DESC, created_at DESC`

没有再加稳定的 tie-break 字段：

- `peap/streaming_store.py:993-1003`

一旦多条任务落在同一秒，排序就不再等价于“最近创建/最近更新”，会退化成数据库未承诺的内部顺序。

#### 影响

- 任务列表首屏可能优先展示旧任务而不是刚完成/刚失败的新任务
- `overview.recent_jobs` 在任务高频场景下会失真
- 用户排查最近异常时，首先看到的可能不是最新一次运行

### 5.23 P2: 任务事件明细被静默截断在 100/200 条，UI 不提示

#### 现象

我创建了一个真实任务，并连续写入 250 条事件。随后读取两条正式产品路径：

- `get_job(job_id)` 返回内联事件
- `get_job_events(job_id)` 返回事件明细列表

结果是：

- `get_job(job_id).events` 只返回 100 条，范围是 `event-249` 到 `event-150`
- `get_job_events(job_id)` 只返回 200 条，范围是 `event-249` 到 `event-50`

前端事件面板直接渲染拿到的数组，没有任何“仅展示最近 N 条”或“已截断”的提示。

这意味着当任务事件规模稍大时，用户会直接丢失最早那部分执行历史，但产品表面看不出发生了截断。

#### 根因

服务层把两个事件读取入口的上限写死成了不同值：

- `get_job()` 内联事件：`limit=100`，见 `desktop_backend/app_service.py:1013-1017`
- `get_job_events()` 默认：`limit=200`，见 `desktop_backend/app_service.py:1019-1021`

底层查询也直接按 `LIMIT ?` 返回，不暴露总量：

- `peap/streaming_store.py:1020-1032`

前端事件面板只是把返回数组全量渲染：

- `desktop_app/renderer.js:568-590`
- `desktop_app/renderer.js:1076-1077`

因此这是典型的静默截断，而不是显式分页。

#### 影响

- 长任务的早期事件会无声丢失
- 排障时无法完整回溯执行历史
- UI、详情接口和内联任务对象三者对“完整事件集”的语义不一致

### 5.24 P1: 后端在 ready 前退出时，桌面端仍会加载主窗口，不会按启动失败处理

#### 现象

我对 `desktop_app/main.js` 做了真实启动链模拟，但把 Electron 壳层替换成最小 stub，以便稳定观察主进程决策；后端子进程本身仍然是实际 `spawn()` 出来的真实进程。

我做了两组对照：

- 对照组 A：`PEAP_BACKEND_CMD=/definitely/missing/peap-backend`
- 对照组 B：`PEAP_BACKEND_CMD=node`，`PEAP_BACKEND_ARGS=["-e","process.exit(1)"]`

预期上，这两组都属于“桌面后端无法完成启动”，成熟产品都应该走统一的启动失败路径，至少弹出致命错误并退出，不应继续加载主界面。

实际结果却分裂成两套行为：

- A 组会正确触发 `startup_fatal`，弹框并退出
- B 组虽然真实发生了 `backend_exit` 和后续 `backend_ready_failed`，但主窗口仍然加载，且没有任何致命错误弹框

也就是说，只要后端是“先成功 spawn，再在 ready 前崩掉”，桌面端就会进入一个“窗口起来了，但服务其实没起来”的假启动成功状态。

#### 根因

主进程把后端就绪检查做成了 fire-and-forget：

- `startBackend()` 在真正 `spawn()` 后只调用 `void monitorBackendReady()`，并不等待结果，见 `desktop_app/main.js:99-166`

而创建窗口的路径也不等待后端就绪：

- `app.whenReady()` 里先 `startBackend()`，然后立即 `await createMainWindow()`，见 `desktop_app/main.js:254-294`
- `createMainWindow()` 只要 `backendProcess` 变量非空就会继续 `loadFile(index.html)`，见 `desktop_app/main.js:216-243`

更关键的是，异步就绪失败并不会上抛到统一的启动致命错误处理：

- `monitorBackendReady()` 在失败时只是记录 `backendStartFailure` 并返回 `false`，见 `desktop_app/main.js:76-97`
- 真正负责弹框退出的 `handleStartupFatalError()` 只挂在 `app.whenReady().then(...).catch(...)` 这一层，见 `desktop_app/main.js:62-68` 和 `desktop_app/main.js:254-294`

因此，“命令不存在”这种同步校验失败会被 `catch` 到，而“后端先启动再早退”这种异步失败不会。

#### 影响

- 用户会看到已经打开的桌面主界面，但后台实际不可用
- 启动故障被错误伪装成普通页面加载成功，问题定位显著变难
- 这条路径与显式缺少后端二进制时的启动语义不一致，产品层面无法形成稳定预期

### 5.25 P2: 运行中后台崩溃后，首页仍保留“运行环境已就绪”，且没有任何可见失联提示

#### 现象

我用真实 `index.html + renderer.js` 做了前端复现。为避免普通浏览器的跨域限制，我起了一个同源本地代理页服务，把 `/api/*` 原样转发到隔离的真实桌面后端；前端代码本身没有改动，仍然走产品里的真实 `fetch`、轮询和 DOM 更新逻辑。

复现步骤如下：

1. 启动真实后端，页面成功进入稳定基线：
   - `heroRuntimeStatus = 运行环境已就绪`
   - `runOneClickBtn.disabled = false`
2. 在页面保持打开的情况下，直接杀掉后台进程。
3. 等待两轮轮询，再读首页 DOM。
4. 再额外点击一次页面上的“刷新”按钮。

按成熟产品预期，后台失联后首页至少应该出现明确的用户可见降级信号，例如：

- 顶部运行状态改成“后台不可用 / 连接中断”
- 一键执行等动作被禁用
- 某个状态栏显示需要重启后台或刷新应用

实际结果却是：

- 被动轮询失败后，首页仍然保持：
  - `heroRuntimeStatus = 运行环境已就绪`
  - `runOneClickBtn.disabled = false`
  - `settingsResult = ""`
  - `runResult = ""`
- 手动点击“刷新”后，页面仍没有任何可见错误文案，DOM 依旧保持旧状态
- 真正发生的失败只出现在浏览器控制台和页面级未捕获异常里：`TypeError: Failed to fetch`

这意味着后台已经真实死亡，但用户可见层仍持续展示“系统健康、动作可用”的旧状态。

#### 根因

轮询失败只会走控制台日志，不会把页面切换到“backend unavailable”状态：

- 轮询循环捕获异常后只调用 `onError(error)`，见 `desktop_app/renderer/polling.mjs:12-27`
- 实际传入的 `onError` 只是 `console.error(error)`，见 `desktop_app/renderer.js:1768-1780`

也就是说，后台失联后没有任何一条代码会主动清空或降级这些可见状态：

- `heroRuntimeStatus`
- `productReadiness`
- 按钮可用性
- 首页状态栏

它们会继续保留最后一次成功拉取时的旧值。

主动“刷新”路径也没有兜底：

- 刷新按钮直接 `await loadOverview(); await refreshCurrentPanel();`，见 `desktop_app/renderer.js:1629-1632`
- 这里没有 `try/catch`，所以在后台已死时会形成未捕获页面错误，而不是稳定的用户提示

#### 影响

- 后台已经崩溃，但首页仍向用户宣称“运行环境已就绪”
- 关键动作按钮继续保持可点，用户只能在点击之后才逐步发现系统其实不可用
- 被动轮询失败不会形成可恢复的产品提示，只会产生控制台噪音和未捕获异常
- 这条路径破坏了运行时健康状态的可信度，属于典型静默降级

### 5.26 P3: 记录页当前页在数据收缩后不会自动回到有效页，会把真实有数据的筛选结果展示成空页

#### 现象

我用真实 `AppService` 构造了一个自然的分页收缩场景：

1. 当前筛选条件下先有 21 条 `ready` 记录。
2. 用户停在第 2 页，页大小为 20。
3. 后台随后把其中 1 条记录移出当前筛选结果。

这时成熟产品通常有两种合理行为：

- 自动把当前页钳回最后一页有效页
- 或者直接返回第 1 页并明确更新页码

实际结果却是：

- 收缩前：`page=2, page_count=2, total_count=21, visible_count=1`
- 收缩后：`page=2, page_count=1, total_count=20, visible_count=0`

也就是说，系统已经明确知道总页数只剩 1 页，但仍把用户留在不存在的第 2 页，并返回空结果。

这不是“没有记录”，而是“页码失效后没有归位”。

#### 根因

服务层完全信任传入页码，不会在 `page > page_count` 时做归位：

- `desktop_backend/app_service.py:799-852`
- `desktop_backend/app_service.py:864-879`

前端也会把这个失效页码原样继续保留：

- `renderRecords()` 直接写回 `desktopState.records.page = payload.page`，见 `desktop_app/renderer.js:896-905`
- 记录页轮询时直接再次调用 `loadRecords()`，不会先重置或校正页码，见 `desktop_app/renderer/polling.mjs:20-21`

因此一旦后台数据量在用户停留当前页期间发生收缩，记录页就可能稳定停在一个已经无效的页号上。

#### 影响

- 用户会看到“当前筛选条件下没有记录”，但实际上前一页仍然有完整数据
- 分页控件可能出现 `第 2 / 1 页` 这种自相矛盾状态
- 在记录页自动轮询开启时，这个问题可以自然发生，不需要用户手工输入非法页码

### 5.27 P1: 手动导入真实挂牌页时，`项目类型` 依赖目录名，记录会以 `未知` 落库并污染项目类型筛选

#### 子域 / 操作编号

- 子域：数据记录
- 操作编号：`28` 项目类型筛选
- 关联操作：`48` 导入后的任务、记录、归档联动

#### 复现步骤

1. 选取真实上交所资产页 `submission/2026年3月/GR2026SH1000265-5-淮安市淮阴医院有限公司部分资产（CT机）.html`，把 HTML 与同名 `_files` 目录放进一个任意临时目录。
2. 用真实 `AppService.launch_manual_import({"input_dir": ...})` 导入，等待任务结束。
3. 调 `list_records({"state":"all","project_type":"all"})`，返回 1 条记录，但 `row.project_type = ""`，`row.values["项目类型"] = "未知"`。
4. 对同一批数据分别调用 `list_records({"project_type":"physical_asset"})` 与 `list_records({"project_type":"equity_transfer"})`，两者都返回这同一条记录，说明项目类型筛选已经失去约束力。
5. 再保存真实映射 `group=中国华润有限公司 -> source_type=央企` 并等待 `mapping_refresh` 结束，记录仍停留在 `pending_mapping`，因为导入链还保留了 `project_type_unknown` 阻塞项。
6. 交叉用真实北交所股权页 `submission/2026年3月/G32026BJ1000003-江西倬慧信息科技有限公司49%股权.html` 重跑，同样得到空 `project_type` 与双筛选同时命中的结果。

#### 成熟产品的预期行为

真实挂牌页一旦进入手动导入，系统应当从页面内容、项目编号或其他稳定业务信号中确定正确的 `项目类型`；至少也应给出用户可执行的补救路径，而不是把记录放进一个既不能正确筛选、也不能继续闭环的中间态。

#### 实际行为

当前实现把这类真实页面导成：

- 记录层 `project_type = ""`
- 展示层 `项目类型 = 未知`
- `equity_transfer` 与 `physical_asset` 两个筛选都能命中同一条记录
- 即便补齐了 `类型` 映射，记录仍然无法离开 `pending_mapping`

也就是说，这不是单纯展示空值，而是“导入后业务类型语义丢失，且没有恢复路径”。

#### 根因

根因由四段逻辑共同组成：

- 手动导入入口只传 `source_file`，没有补任何 `project_type_fallback`：`desktop_backend/app_service.py:1304-1308`
- 解析总控在 parser 完成后仍用 `detect_category_from_path(file_path)` 覆盖/写入 `项目类型`，而这个函数只看路径中是否出现 `股权转让/实物资产/增资扩股/预披露` 目录名：`peap/parsing.py:248-264`、`peap/pathing.py:20-43`
- 后处理把空/未知项目类型视为正式阻塞项 `project_type_unknown`：`peap/streaming_postprocess.py:91-101`
- 记录页项目类型筛选只在 `record_project_type` 非空时才排除，因此空类型记录会同时落入多个筛选结果：`desktop_backend/app_service.py:813-816`

#### 影响范围

- 当前 `submission/2026年3月` 样本里，北交所与上交所都能复现，不是单一交易所特例
- 手动导入后的真实记录会长期卡在 `pending_mapping`
- 记录页“项目类型筛选”不再可信
- 后续导出前置条件无法稳定建立，因为记录从源头就缺失正确业务类型

#### 严重度

`P1`。这是数据进入系统后的基础语义丢失，而且当前 UI 没有提供项目类型补录或纠偏路径。

### 5.28 P1: 北交互联真实详情页会被 `skip-cbex-otc-page` 整页跳过，手动导入只落空白 `skipped` 记录

#### 子域 / 操作编号

- 子域：手动导入
- 操作编号：`48` 导入后的任务、记录、归档联动
- 关联操作：`43` 选择有效目录

#### 复现步骤

1. 选取真实北交互联详情页 `submission/2026年3月/GR2026BJ1001615-2台机器设备（半轴壳体清洗机等）.html`，把 HTML 与 `_files` 放进有效临时目录。
2. 用真实 `AppService.launch_manual_import({"input_dir": ...})` 导入，接口返回 `discovered_count = 1`，说明文件发现链路成立。
3. 等任务结束后读取 `get_job(job_id)`，任务状态为 `success_with_warnings`，`summary.skipped_count = 1`。
4. 再读 `list_records({"state":"all","project_type":"all"})`，只得到一条 `state = "skipped"` 的空白记录，`project_code/project_name/project_type` 全为空，状态详情只是“当前网页按规则跳过，不进入录入”。
5. 最后把当前 `submission/2026年3月` 的 40 个真实样本整体导入复核，结果 `skipped_count = 9`；被跳过的 9 个文件全部是 `GR2026BJ...` 北交互联实物资产详情页。

#### 成熟产品的预期行为

这些页面本身已经是实际项目详情页，HTML 内明确包含标题、项目编号和正文信息；成熟产品至少应把它们解析成正式记录，或者在无法支持时给出“此类页面暂不支持”的明确产品级反馈，而不是把它们伪装成可忽略的普通跳过页。

#### 实际行为

当前导入链会把这类页面在 parser 入口处直接短路成 `SkipParse`，最终结果是：

- 任务层看见的是 `success_with_warnings`
- 记录层看见的是一条空白 `skipped` 记录
- 用户拿不到项目编号、项目名称、交易所、业务类型
- 当前产品里没有任何后续操作可以把这批页面重新纳入录入主链

#### 根因

`peap/parsing.py` 先用 `_is_cbex_otc_page()` 识别“北交互联”标记，只要命中标题、关键词或登录回调等模式，就在真正 parser 路由之前直接：

- `_is_cbex_otc_page()`：`peap/parsing.py:196-208`
- `raise SkipParse("skip-cbex-otc-page: ...")`：`peap/parsing.py:222-223`

但本轮真实样本表明，这个判定并不是“广告页/壳页”专属，而会命中实际的北交互联项目详情页。

#### 影响范围

- 当前真实样本集中已有 `9 / 40` 页被整页跳过
- 被跳过页面全部落成空白 `skipped` 记录，无法进入后续映射、重处理或导出链路
- 手动导入任务的 `imported_count` 仍然会把这些文件算作“已处理”，容易让人低估真实漏入量

#### 严重度

`P1`。这是对真实业务页面的整页丢弃，且当前没有用户可执行的恢复路径。

### 5.29 P2: 默认后处理配置引用了 4 个不存在的映射模板文件，真实导入会直接暴露内部路径错误

#### 现象

当前仓库默认使用的 `peap_postprocess/ppe_config/postprocess_external_template.json` 在规则 `R005_normalize_source_type` 里声明了 4 个映射模板文件：

- `../ppe_config/transferor_type_mapping_template.csv`
- `../ppe_config/transferor_group_mapping_template.csv`
- `../ppe_config/group_group_mapping_template.csv`
- `../ppe_config/group_type_mapping_template.csv`

但当前 `peap_postprocess/ppe_config/` 目录里实际只存在：

- `postprocess.json`
- `postprocess.yaml`
- `postprocess_external_template.json`

并没有任何上述 CSV 模板文件。

我用一个不受 5.27 / 5.28 干扰的真实预披露页 `submission/2026年3月/G32026SH1000077-0-山东葛洲坝利鑫能源有限公司51%股权.html` 复核，结果是：

- `项目类型 = 预披露`
- `转让方`、`隶属集团` 都能正常解析
- 但真实 `AppService.launch_manual_import()` 导入后，记录仍直接落成 `pending_mapping`
- `status_detail` 暴露内部相对路径错误：`entity_type_mapping_file not found: ../ppe_config/group_type_mapping_template.csv`

同一条记录的 findings 里还能看到另外 3 个缺失模板文件警告，说明这不是单个路径写错，而是默认配置引用了一整组未随仓库提供的模板资源。

#### 成熟产品的预期行为

如果这 4 个模板文件属于产品默认能力的一部分，它们应当随默认配置一起提供；如果它们只是可选扩展，默认配置就不应该在首轮导入时直接引用不存在的路径，更不应该把内部相对路径原样暴露给用户。

#### 实际行为

当前产品首次导入真实页面时，即使 `项目类型` 已经正确、页面也没有被 skip，记录仍可能因为默认类型映射资源缺失而直接进入 `pending_mapping`，用户看到的是一串内部文件路径错误，而不是产品语义层的提示。

更具体地说，这个错误在用户界面里会被拆成两种互相不一致的表现：

- 真实 HTTP `/api/mappings` 返回的待补项只带 `project_code + payload`，不带 `status_detail`；因此映射页只会显示“公司 / 当前集团”，不会告诉用户为什么这条记录处于 `pending_mapping`
- 真实 HTTP `/api/records` 则会把 `status_detail` 回给前端，记录页再把它原样渲染出来，于是用户看到的是 `entity_type_mapping_file not found: ../ppe_config/group_type_mapping_template.csv` 这类内部相对路径

我继续做了真实前端回放后确认，用户如果沿映射页继续补一条 `group -> source_type` 规则，页面保存反馈只会提示“已启动映射回刷任务”，并不会解释上一轮待补其实来自默认模板资源缺失。

这不是“完全无法恢复”的死锁。我继续用真实 `upsert_mapping()` 保存：

- `match_field = group`
- `source_name = 中国葛洲坝集团股份有限公司`
- `target_field = source_type`
- `target_value = 央企`

随后真实 `mapping_refresh` 成功把该记录推进到 `ready`。因此问题本质不是“映射系统彻底失效”，而是“默认交付包缺少被默认配置显式依赖的模板资源，导致首轮体验直接退化成手工补录”。

#### 根因

- 默认后处理配置把 4 个模板 CSV 写进了 `R005_normalize_source_type`：`peap_postprocess/ppe_config/postprocess_external_template.json:52-59`
- 当前仓库 `peap_postprocess/ppe_config/` 并没有这些文件
- 规则加载时只要路径不存在，就会累计 `source_type_table_error`：`peap_postprocess/postprocess_engine/rules/builtin.py:1022-1028`
- 规则应用时会把这些缺失资源以 warning 形式写回记录 findings：`peap_postprocess/postprocess_engine/rules/builtin.py:1421-1432`
- 同一缺失原因在产品投影层被拆散了：`desktop_backend/app_service.py:1023-1025` + `peap/streaming_store.py:1183-1207` 的待补列表只返回 `payload_json`，`desktop_app/renderer.js:692-714` 也只渲染项目编号、公司和当前集团；但 `desktop_backend/app_service.py:277-296`、`desktop_backend/app_service.py:832-842` 会把 findings 第一条消息投影成 `status_detail`，再由 `desktop_app/renderer/records.mjs:68-73` 直接展示在记录页
- 映射保存成功后的前端反馈也只显示任务启动信息：`desktop_app/renderer.js:1391-1395`

#### 影响

- 默认配置下，真实导入记录会把内部相对路径错误直接暴露到产品状态详情
- 当前 `submission/2026年3月` 的 40 个样本整批导入里，全部 `31` 条非 skip 记录都带有 `source_type_table_error`
- 即使 `项目类型` 本身已经正确，用户仍需要先手工维护类型映射，才能把记录推进到 `ready`
- 首次使用者会把“缺模板资源”误解成“自己映射没配对”或“系统规则异常”

## 6. 未发现问题但已重点验证的路径

以下路径本轮重点验证后，未发现新的实现逻辑问题：

- token 鉴权与 `/api/ready` 例外放行
- 浏览器运行环境状态读取
- 浏览器安装状态机
- 浏览器缺失时的真实安装链路与重复点击去重
- 浏览器缺失时的首屏自动安装触发与失败态回显
- 工作区派生路径强制回写
- 一键执行空日期提交会按当天入账（2026-03-23）
- 记录页日期筛选、关键字筛选、分页与页大小切换组合回归
- 手动导入空目录的真实任务语义
- 手动导入大小写扩展名兼容
- 手动导入深层递归目录的任务、记录与归档联动
- `_files` 资源目录随归档复制并改写 HTML 引用
- 导出空结果消息分流
- 导出 HTTP 返回体、任务账本 summary 与磁盘产物一致
- 北京股权样本 `G32026BJ1000091-川翔投资管理（北京）有限公司14.5%股权.html` 的联系人信息未污染 `转让方`；真实导入结果仍为 `转让方=北京国科军融创新科技有限公司`
- 映射保存前 preview 与 overwrite 确认
- 批量 `pending_mapping` 重处理
- archive/source 回退重处理
- legacy skip / pending mapping / archive link 修复链路
- stale running job 在服务重启后会被正确标记为 `interrupted`
- 本机 `dir` 打包链、包内 sidecar 落位与独立启动
- `packaged runtime` 与 `dev runtime` 在同一工作区下的路径派生一致

## 7. 本轮实际执行的关键复现证据

### 7.1 一键执行反向日期区间

真实 HTTP 复现结果：

```json
"reverse_oneclick": [
  202,
  {
    "job_id": "",
    "db_path": ".../streaming_ingest.sqlite3",
    "job_type": "one_click"
  }
],
"jobs_after_reverse": [
  200,
  {
    "jobs": []
  }
]
```

这是当前最关键的证据：接口已接受，但任务根本不存在。

### 7.2 手动导入无效目录

真实 HTTP 复现结果：

```json
"invalid_manual_import": [
  500,
  {
    "error": "/not/exist"
  }
]
```

### 7.3 手动导入大小写扩展名

通过直接调用 `launch_manual_import()`，使用：

- `a.HTML`
- `b.HTM`
- `c.MHTML`
- `d.html`

验证得到 `discovered_count = 4`，说明当前扫描逻辑对大小写扩展名兼容。

### 7.4 浏览器未就绪时，真实后端仍接受一键执行

真实 HTTP 复现结果：

```json
{
  "readiness": {
    "ready": false,
    "download_ready": false,
    "browser_runtime_ready": false
  },
  "launch": [
    202,
    {
      "job_id": "d81f948d12bf4d5389b043c3209d827b",
      "db_path": ".../streaming_ingest.sqlite3",
      "job_type": "one_click"
    }
  ]
}
```

这说明当前 “浏览器未就绪禁止启动” 只是前端约束，不是服务层约束。

### 7.5 手动导入全失败时，任务仍落成 `success_with_warnings`

真实 HTTP 复现结果：

```json
{
  "status": "success_with_warnings",
  "downloaded_count": 1,
  "persisted_count": 0,
  "exception_count": 1,
  "summary": {
    "imported_count": 1,
    "failed_count": 1
  }
}
```

同时记录页里该条记录状态是：

```json
{
  "state": "parse_failed"
}
```

### 7.6 `rebuild` 连续执行两次不会重建全部导出

直接运行真实导出逻辑的复现结果：

```json
{
  "first": {"new": 1, "changed": 0, "artifacts": 1},
  "second": {"new": 0, "changed": 0, "artifacts": 0}
}
```

这与“重建导出”的产品语义不一致。

### 7.7 `conflict` 记录在真实读路径中被立即吞并

直接运行真实服务层的复现结果：

```json
{
  "before_row_state": "ready",
  "overview_conflict_count": 0,
  "after_row_state": "ready",
  "after_row_status_label": "已录入",
  "after_row_status_detail": "归档文件曾同名，当前文件为 G32025SH1000194__conflict1.html",
  "conflict_filter_total": 0
}
```

这里最关键的点不是“显示文案不对”，而是我明确插入的是 `state = "conflict"`，但第一次走 `list_records()` 后，产品可见层已经完全看不到 `conflict` 了。

### 7.8 导出任务的 `latest_progress` 被投影成归档进度

直接运行真实服务层的复现结果：

```json
{
  "running": {
    "phase_code": "archive_pending",
    "phase_label": "正在存档",
    "downloaded_count": 12,
    "persisted_count": 2,
    "archive_pending_count": 10,
    "archive_completed_count": 2
  },
  "completed": {
    "phase_code": "completed",
    "phase_label": "已完成",
    "downloaded_count": 12,
    "persisted_count": 2,
    "archive_pending_count": 10,
    "archive_completed_count": 2,
    "job_summary": {
      "new_records": 10,
      "changed_records": 2,
      "artifacts": [
        {"path": ".../out1.xlsx"},
        {"path": ".../out2.xlsx"}
      ]
    }
  }
}
```

其中 `artifacts.path` 为避免噪音做了路径省略。

运行态和完成态都能看到同一件事：导出任务自己的结果摘要是正常的，但 `latest_progress` 同时还在暴露一套不属于导出任务的归档计数。

### 7.9 记录页默认查询会把“全部记录”缩成“全部股权转让记录”

直接运行真实服务层的复现结果：

```json
{
  "default_query": "state=all&project_type=equity_transfer&page=1&page_size=50",
  "default_total": 1,
  "default_codes": ["EQ001"],
  "all_total": 2,
  "all_codes": ["PA001", "EQ001"]
}
```

这里的关键不是后端筛选本身，而是前端首屏默认查询串已经把“全部业务类型”缩成了 `equity_transfer`。

### 7.10 数据库文件丢失后，真实 API 仍返回正常 `200`，但内容已静默重置

真实 HTTP 复现结果：

```json
{
  "before_overview": {
    "status": 200,
    "ready_count": 1
  },
  "before_basic": {
    "status": 200,
    "default_exchange": "cbex",
    "default_project_type": "physical_asset",
    "default_concurrency": 7
  },
  "after_overview": {
    "status": 200,
    "ready_count": 0,
    "recent_jobs_len": 0,
    "phase_label": "暂无任务"
  },
  "after_basic": {
    "status": 200,
    "default_exchange": "all",
    "default_project_type": "all",
    "default_concurrency": 4
  }
}
```

这里最危险的点不是“报错了”，而是完全没有报错。用户侧看到的是一个语义完整、状态正常、但已经被悄悄清空的系统。

### 7.11 运行中再次启动时，真实 API 把忙碌冲突返回成 `500`

真实 HTTP 复现结果：

```json
{
  "manual_import": [
    202,
    {
      "job_id": "8c76fb47a0d2465d9c0530a31e8dad37",
      "job_type": "manual_import",
      "input_dir": ".../many-html",
      "discovered_count": 400
    }
  ],
  "export_while_running": [
    500,
    {
      "error": "已有执行中的任务：手动导入解析"
    }
  ],
  "oneclick_while_running": [
    500,
    {
      "error": "已有执行中的任务：手动导入解析"
    }
  ]
}
```

这说明当前互斥机制本身不是失效，而是错误地被 HTTP 层包装成了服务器故障。

### 7.12 后端被杀后重启，`interrupted` 任务仍残留旧的运行中上下文

真实进程级复现结果：

```json
{
  "job_status": "interrupted",
  "phase_code": "interrupted",
  "phase_label": "已中断",
  "current_task_label": "137.html",
  "task_index": 44,
  "task_total": 300,
  "downloaded_count": 43,
  "exception_count": 43
}
```

这里的问题不是中断没落账；中断本身已经正确落账了。真正的问题是终态投影里仍然带着旧的运行态任务上下文。

### 7.13 同一秒内快速创建任务时，“最近任务”实际返回的是最早任务

直接运行真实服务层的复现结果：

```json
{
  "created_jobs": 35,
  "recent_jobs_len": 20,
  "recent_indexes": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
  "overview_recent_jobs_len": 5,
  "overview_indexes": [0, 1, 2, 3, 4]
}
```

这里最关键的点不是“列表少了几条”，而是“最近”这个排序语义在高频场景下直接翻转了。

### 7.14 任务事件明细会被静默截断在 100/200 条

直接运行真实服务层的复现结果：

```json
{
  "job_events_inline_len": 100,
  "job_events_inline_first": "event-249",
  "job_events_inline_last": "event-150",
  "events_endpoint_len": 200,
  "events_endpoint_first": "event-249",
  "events_endpoint_last": "event-50"
}
```

同一个 250 事件任务，在不同产品入口里会被截成两种不同长度，而且都没有任何截断提示。

### 7.15 后端在 ready 前退出时，桌面主进程仍会加载主窗口

我使用真实 `desktop_app/main.js` 做了两组启动链对照实验。Electron API 用最小 stub 承接，但后端子进程仍然通过真实 `spawn()` 启动；因此这里验证的是主进程控制流，而不是伪造出来的静态推断。

直接运行结果如下：

```json
{
  "missing_binary": {
    "dialogs": [
      {
        "title": "产权交易所自动录入启动失败",
        "content": "Backend launch target was not found: /definitely/missing/peap-backend"
      }
    ],
    "load_files": [],
    "quits": 1,
    "log_tail": [
      "backend_launch_invalid",
      "startup_fatal"
    ]
  },
  "early_exit_before_ready": {
    "dialogs": [],
    "load_files": [
      "desktop_app/index.html"
    ],
    "quits": 0,
    "log_tail": [
      "backend_spawned",
      "backend_exit",
      "backend_ready_failed"
    ]
  }
}
```

这里最关键的对比不是“有没有报错日志”，而是“同样属于启动失败，缺失命令会弹致命错误并退出，而 ready 前早退却会继续把主窗口拉起来”。

### 7.16 运行中后台崩溃后，真实页面仍保留旧的“已就绪”状态

这一条不是服务层模拟，而是使用真实 `desktop_app/index.html + renderer.js` 运行得到的结果。为了在普通浏览器环境里复现 Electron 页面的网络访问，我起了一个同源本地代理，把 `/api/*` 转发到隔离后端；页面逻辑本身没有改写。

后台健康时的基线页面状态：

```json
{
  "heroRuntimeStatus": "运行环境已就绪",
  "runButtonDisabled": false,
  "settingsResult": "",
  "statLatestStatus": "暂无任务"
}
```

随后我在页面保持打开的情况下杀掉真实后台进程，等待 4 秒后再读 DOM，并额外点击一次“刷新”：

```json
{
  "afterPoll": {
    "statLatestStatus": "暂无任务",
    "settingsResult": "",
    "runResult": "",
    "heroRuntimeStatus": "运行环境已就绪",
    "runButtonDisabled": false
  },
  "afterManualRefresh": {
    "statLatestStatus": "暂无任务",
    "settingsResult": "",
    "runResult": "",
    "heroRuntimeStatus": "运行环境已就绪",
    "runButtonDisabled": false
  },
  "pageErrors": ["Failed to fetch"]
}
```

浏览器控制台里实际已经连续出现了多轮错误：

- `Failed to load resource: net::ERR_EMPTY_RESPONSE`
- `TypeError: Failed to fetch`

但这些错误没有转化成任何用户可见状态；页面仍然停留在崩溃前那一帧“运行环境已就绪”的旧画面。

### 7.17 记录页当前页在数据收缩后不会自动归位

直接运行真实服务层的复现结果：

```json
{
  "before": {
    "page": 2,
    "page_count": 2,
    "total_count": 21,
    "visible_count": 1,
    "row_ids": ["rec-1"]
  },
  "after": {
    "page": 2,
    "page_count": 1,
    "total_count": 20,
    "visible_count": 0,
    "row_ids": []
  }
}
```

这里最关键的点不是“第二页没数据”本身，而是系统已经知道总页数只剩 1 页，却仍然把请求和返回都维持在 `page=2`。

### 7.18 首屏自动安装浏览器与安装失败反馈

这组证据使用真实 `desktop_app/index.html + renderer.js` 回放，页面与 `/api/*` 放在同一源下运行，未改写前端逻辑；只通过注入不同的 runtime manager 分别模拟“浏览器缺失后安装成功”和“浏览器缺失后安装失败”两条自然路径。

成功路径下，页面在未点击任何按钮时会自动发起安装请求：

```json
{
  "requests": [
    "GET /api/runtime/dependencies",
    "POST /api/runtime/install-browser"
  ],
  "success_early": {
    "heroRuntimeStatus": "正在准备浏览器运行环境",
    "settingsResult": "已开始后台安装浏览器",
    "runButtonDisabled": true
  },
  "success_late": {
    "heroRuntimeStatus": "运行环境已就绪",
    "startupGateBody": "运行环境已就绪，可以直接开始任务。",
    "runButtonDisabled": false
  }
}
```

失败路径下，页面也会从同一条自动安装入口回到失败态，并重新放开“安装浏览器”按钮：

```json
{
  "failure_late": {
    "heroRuntimeStatus": "运行环境缺失或异常",
    "startupGateBody": "当前浏览器运行环境未就绪。为避免任务中途失败，一键执行已临时禁用。",
    "startupGateMeta": "安装状态：simulated install failure",
    "runButtonDisabled": true,
    "installDisabled": false
  }
}
```

这说明第 `13`、`15` 号操作本轮已补齐真实前端回放证据。

### 7.19 一键执行空日期默认行为

真实页面回放中，我先把首页一键执行的开始/结束日期都清空，再直接点击“一键执行”。真实请求体与任务账本结果如下：

```json
{
  "request_body": {
    "start_date": "",
    "end_date": "",
    "exchange": "all",
    "project_type": "all",
    "concurrency": 4
  },
  "runResult": "已开始一键执行：b4486714fc324257a79901625b535faa。任务完成后，如需表格请点击“导出 Excel”。",
  "job_metadata": {
    "start_date": "2026-03-23",
    "end_date": "2026-03-23"
  },
  "job_status": "success"
}
```

这里的关键是：前端确实会把空字符串提交出去，但后台会按当天 `2026-03-23` 落账，且 UI 提示、返回体与任务账本保持一致。第 `18` 号操作本轮可视为闭环。

### 7.20 记录页日期筛选、关键字筛选、分页与页大小切换组合回归

真实页面回放结果如下：

```json
{
  "recordRequests": [
    "/api/records?state=all&project_type=equity_transfer&page=1&page_size=50",
    "/api/records?state=all&project_type=equity_transfer&page=1&page_size=20",
    "/api/records?state=all&project_type=equity_transfer&page=2&page_size=20",
    "/api/records?state=all&project_type=equity_transfer&page=1&page_size=20&date_from=2026-03-22&date_to=2026-03-22",
    "/api/records?state=all&project_type=equity_transfer&page=1&page_size=20&keyword=%E5%8D%8E%E6%B6%A6&date_from=2026-03-22&date_to=2026-03-22"
  ],
  "afterPageSize": {
    "summary": "共 25 条 · 第 1 / 2 页 · 本页 20 条 · 已录入 20 条",
    "pageIndicator": "第 1 / 2 页"
  },
  "afterNextPage": {
    "summary": "共 25 条 · 第 2 / 2 页 · 本页 5 条 · 已录入 5 条",
    "pageIndicator": "第 2 / 2 页"
  },
  "afterDate": {
    "summary": "共 10 条 · 第 1 / 1 页 · 本页 10 条 · 已录入 10 条",
    "codes": ["EQ020", "EQ019", "EQ018", "EQ017", "EQ016", "EQ015", "EQ014", "EQ013", "EQ012", "EQ011"]
  },
  "afterKeyword": {
    "summary": "共 5 条 · 第 1 / 1 页 · 本页 5 条 · 已录入 5 条",
    "codes": ["EQ015", "EQ014", "EQ013", "EQ012", "EQ011"]
  }
}
```

可以确认：

- 页大小从 `50` 切到 `20` 后，前端会回到第 `1` 页并正确形成两页。
- 翻到第 `2` 页后，再改日期筛选会自动归回第 `1` 页。
- 日期和关键字组合筛选在真实前端链路下成立。

因此第 `29-31` 号操作本轮已补齐。

### 7.21 手动导入空目录

真实 HTTP 复现结果：

```json
{
  "http_status": 202,
  "response": {
    "job_type": "manual_import",
    "discovered_count": 0
  },
  "job_status": "success",
  "job_summary": {
    "imported_count": 0,
    "pending_mapping_count": 0,
    "skipped_count": 0,
    "failed_count": 0
  },
  "job_events": ["manual_import_scan"]
}
```

这说明空目录不会报错，也不会伪造导入结果；它会创建一个 `0` 文件的成功任务。第 `44` 号操作本轮已闭环。

### 7.22 深层递归目录导入与 `_files` 资源目录联动

真实 HTTP 入口下，对三层以上嵌套目录执行手动导入，结果如下：

```json
{
  "http_status": 202,
  "response": {
    "job_type": "manual_import",
    "discovered_count": 3
  },
  "job_status": "success",
  "job_summary": {
    "imported_count": 3,
    "pending_mapping_count": 0,
    "failed_count": 0
  },
  "record_codes": [
    "G32026SH1000001",
    "GR2026BJ1000002",
    "G32026TJ1000003"
  ],
  "archive_checks": {
    "G32026SH1000001": {
      "archive_exists": true,
      "assets_dir_exists": true,
      "html_rewritten_contains": true,
      "html_old_ref_present": false
    },
    "GR2026BJ1000002": {
      "archive_exists": true,
      "assets_dir_exists": true,
      "html_rewritten_contains": true,
      "html_old_ref_present": false
    }
  }
}
```

这组证据同时说明了三件事：

- 深层递归发现成立，第 `46` 号操作闭环。
- 成功导入后，任务账本、记录列表和归档文件三者联动成立，第 `48` 号操作闭环。
- 伴随 HTML 一起存在的 `_files` 目录会被复制到 canonical 归档名下，且 HTML 内部引用会从原始 `alpha_files/`、`beta_files/` 改写到归档基名对应的 `_files/`，第 `55` 号操作闭环。

### 7.23 导出结果文件与任务账本一致性

在同一批真实导入记录上直接走 HTTP `/api/exports`，结果如下：

```json
{
  "http_status": 200,
  "response": {
    "job_type": "export_excel",
    "new_records": 3,
    "changed_records": 0,
    "artifacts": [
      ".../挂牌_实物资产_新增_20260323_142727_505068_f761e921.xlsx",
      ".../挂牌_股权转让_新增_20260323_142727_505068_f761e921.xlsx"
    ],
    "status": "completed"
  },
  "job_summary": {
    "new_records": 3,
    "changed_records": 0,
    "artifacts": [
      ".../挂牌_实物资产_新增_20260323_142727_505068_f761e921.xlsx",
      ".../挂牌_股权转让_新增_20260323_142727_505068_f761e921.xlsx"
    ],
    "status": "completed"
  },
  "job_downloaded_count": 3,
  "job_persisted_count": 2,
  "artifact_exists": {
    "...实物资产...xlsx": true,
    "...股权转让...xlsx": true
  }
}
```

HTTP 返回体、任务 summary 与磁盘产物三者一致，第 `52` 号操作本轮已补齐。

### 7.24 手动导入真实页面后，`project_type` 会退化为 `未知`，并同时命中多个项目类型筛选

这组证据全部来自真实 `AppService` 调用，不经过单独 parser mock。

上交所资产页 `GR2026SH1000265-5-淮安市淮阴医院有限公司部分资产（CT机）.html` 的复现结果：

```json
{
  "row_project_type": "",
  "values_project_type": "未知",
  "all_total": 1,
  "physical_total": 1,
  "equity_total": 1,
  "after_group_type_mapping": {
    "ready_total": 0,
    "pending_mapping_count": 1
  }
}
```

北交所股权页 `G32026BJ1000003-江西倬慧信息科技有限公司49%股权.html` 的交叉结果：

```json
{
  "project_type": "",
  "values_project_type": "未知",
  "equity_total": 1,
  "physical_total": 1
}
```

这里最关键的点不是“项目类型显示成未知”本身，而是：

- 同一条记录会同时进入不同业务类型筛选；
- 即便补齐了 `类型` 映射，记录仍然因为 `project_type_unknown` 无法闭环。

### 7.25 北交互联真实详情页会被整页 skip

单页复现 `GR2026BJ1001615-2台机器设备（半轴壳体清洗机等）.html` 的结果：

```json
{
  "html_markers": {
    "title": "北交互联-2台机器设备（半轴壳体清洗机等）",
    "project_code_line": "项目编号：GR2026BJ1001615"
  },
  "manual_import": {
    "discovered_count": 1,
    "job_status": "success_with_warnings",
    "skipped_count": 1
  },
  "record": {
    "state": "skipped",
    "project_code": "",
    "project_name": "",
    "status_detail": "当前网页按规则跳过，不进入录入"
  }
}
```

把当前 `submission/2026年3月` 的 40 个真实样本整体导入后，得到：

```json
{
  "pending_mapping_count": 31,
  "skipped_count": 9,
  "skipped_files": [
    "GR2026BJ1001588-报废设备一批.html",
    "GR2026BJ1001611-1台四驱空载磨合台.html",
    "GR2026BJ1001612-2台机器设备（小壳体清洗机等）.html",
    "GR2026BJ1001613-11台机器设备（前轮助力机械手等）.html",
    "GR2026BJ1001615-2台机器设备（半轴壳体清洗机等）.html",
    "GR2026BJ1001619-某单位报废设备资产一批.html",
    "GR2026BJ1001620-沈阳华誉地源热泵供热有限公司报废设备一批.html",
    "GR2026BJ1001621-报废车辆设备一批.html",
    "GR2026BJ1001622-报废设备一批.html"
  ]
}
```

这说明当前不是“偶发单页异常”，而是一整类北交互联真实详情页都被提前排除出录入链。

### 7.26 5.27 在 40 个真实样本中的覆盖面与筛选失真量化

在同一批 `submission/2026年3月` 40 个真实样本上，先直接调用 `parse_file()` 统计 parser 输出，再用真实 `AppService.launch_manual_import()` 整批导入，只看 `项目类型` 相关字段，得到：

```json
{
  "parse_file": {
    "parsed_total": 31,
    "skip_total": 9,
    "unknown_project_type": 29,
    "unknown_by_exchange": {
      "shanghai": "15 / 17",
      "beijing": "14 / 14"
    },
    "unknown_by_prefix": {
      "G3": "7 / 8",
      "GR": "18 / 18",
      "Q3": "4 / 5"
    },
    "non_unknown_files": [
      "G32026SH1000077-0 -> 预披露",
      "Q32026SH1000010-0 -> 预披露"
    ]
  },
  "app_service": {
    "job_status": "success_with_warnings",
    "pending_mapping_count": 31,
    "skipped_count": 9,
    "record_project_type_empty": 38,
    "display_project_type_unknown": 29,
    "equity_transfer_rows": 38,
    "physical_asset_rows": 38,
    "pre_disclosure_rows": 40,
    "equity_equals_physical": true,
    "pre_minus_equity": [
      "G32026SH1000077-0",
      "Q32026SH1000010-0"
    ]
  }
}
```

这把 5.27 的范围从“北交所 + 上交所各举一例”扩成了更明确的样本面结论：当前 40 页真实样本里，除去被 5.28 整页跳过的 9 页，剩余 31 页中有 29 页在 parser 层就被写成 `项目类型 = 未知`；覆盖上海 `15 / 17`、北京 `14 / 14`，并横跨 `G3`、`GR`、`Q3` 三类真实项目编号。只有两个本身带 `-0` 预披露编码的样本没有落入这个问题。

用户可见影响也已经不是“个别记录筛选不准”，而是整批记录的业务类型语义失真。整批导入后，记录层 `project_type` 只有 `2` 条 `预披露`，其余 `38` 条为空；因此记录页里：

- `equity_transfer` 返回 `38` 条
- `physical_asset` 返回 `38` 条
- 两个结果集完全相同
- `pre_disclosure` 甚至会返回全部 `40` 条，因为那 `38` 条空类型记录不会被排除，只有两条真实预披露被额外并入

这与代码链条完全一致：`desktop_backend/app_service.py:1304-1308` 的手动导入入口不传 `project_type_fallback`；`peap/parsing.py:248-264` 在 parser 完成后仍用 `detect_category_from_path(file_path)` 重写 `项目类型`；`peap/pathing.py:20-43` 只认目录名；`peap/streaming_postprocess.py:91-101` 再把未知类型固化成 `project_type_unknown` 阻塞项；`desktop_backend/app_service.py:813-816` 对空 `record_project_type` 不做排除，最终把空类型记录同时暴露给多个项目类型筛选。

### 7.27 5.28 的真实作用域是“北交互联品牌化 OTC 详情模板”，不是当前 9 个文件名本身

把 `submission/2026年3月` 中全部北京样本按页面模板特征拆开，只看 `_is_cbex_otc_page()` 真正使用的强标记：

```json
{
  "non_template_total": 14,
  "non_template_parsed": 14,
  "branded_total": 9,
  "branded_SkipParse": 9,
  "templated_examples": [
    "GR2026BJ1001588 -> 北交互联-报废设备一批",
    "GR2026BJ1001611 -> 北交互联-1台四驱空载磨合台",
    "GR2026BJ1001615 -> 北交互联-2台机器设备（半轴壳体清洗机等）",
    "GR2026BJ1001622 -> 北交互联-报废设备一批"
  ]
}
```

这里的 `branded_total = 9` 指页面至少命中以下任一模板标记：

- `<title>北交互联-...`
- 正文存在 `欢迎来到北交互联`

当前真实样本中，命中这组标记的 `9 / 9` 页全部在 `parse_file()` 入口被 `SkipParse`；同批不命中这组标记的北京页 `14 / 14` 全部正常进入 parser。这说明当前问题不是“所有北京页都受影响”，但也绝不是“只硬编码影响这 9 个文件名”。

更准确地说，受影响的是一整类“北交互联品牌化 OTC 详情模板”。这是结合代码做出的范围推论：`peap/parsing.py:196-208` 的 `_is_cbex_otc_page()` 只看页面内容标记，不看 `GR2026BJ` 前缀、日期目录或文件名白名单，所以任何采用同类标题/欢迎语模板的真实详情页，都会在 parser 路由前被提前短路。当前仓库里的 9 页只是这类模板在现有真实样本中的全部实例。

这一点还和北京 parser 的既有实现相互印证。`peap_parsers/beijing_standard.py:1074-1124` 已经显式写了“北交互联格式 / 北交互联实物资产格式”的项目编号、标题和字段提取逻辑，`peap_parsers/beijing_standard.py:1175-1188` 还专门补了北交互联实物资产格式的价格提取。也就是说，这批页面并不是系统“完全不认识的壳页或登录页”，而是本来已经有 parser 适配意图的真实详情模板，只是当前被 `peap/parsing.py:196` 的整页跳过规则挡在了路由之前。

### 7.28 默认映射模板文件缺失的真实导入与恢复验证

先看默认配置与仓库实际资源的对照：

```json
{
  "postprocess_config_templates": [
    "../ppe_config/transferor_type_mapping_template.csv",
    "../ppe_config/transferor_group_mapping_template.csv",
    "../ppe_config/group_group_mapping_template.csv",
    "../ppe_config/group_type_mapping_template.csv"
  ],
  "ppe_config_files": [
    "postprocess.json",
    "postprocess.yaml",
    "postprocess_external_template.json"
  ]
}
```

再用真实预披露页 `G32026SH1000077-0-山东葛洲坝利鑫能源有限公司51%股权.html` 做单页导入复核。这个样本的 `项目类型 = 预披露`，因此这里观察到的 `pending_mapping` 不再来自 5.27 的 `project_type_unknown`：

```json
{
  "manual_import": {
    "job_status": "success_with_warnings",
    "summary": {
      "failed_count": 0,
      "imported_count": 1,
      "pending_mapping_count": 1,
      "skipped_count": 0
    }
  },
  "record": {
    "project_code": "G32026SH1000077-0",
    "project_type": "预披露",
    "state": "pending_mapping",
    "status_detail": "entity_type_mapping_file not found: ../ppe_config/group_type_mapping_template.csv",
    "transferor": "中国葛洲坝集团电力有限责任公司",
    "group_name": "中国葛洲坝集团股份有限公司"
  },
  "finding_messages": [
    "entity_type_mapping_file not found: ../ppe_config/group_type_mapping_template.csv",
    "transferor_type_mapping_file not found: ../ppe_config/transferor_type_mapping_template.csv",
    "transferor_group_mapping_file not found: ../ppe_config/transferor_group_mapping_template.csv",
    "group_group_mapping_file not found: ../ppe_config/group_group_mapping_template.csv",
    "缺少类型，暂不能进入导出"
  ]
}
```

随后我继续用真实 `upsert_mapping()` 保存：

- `match_field = group`
- `source_name = 中国葛洲坝集团股份有限公司`
- `target_field = source_type`
- `target_value = 央企`

真实 `mapping_refresh` 返回：

```json
{
  "status": "success",
  "refreshed_count": 1,
  "pending_mapping_count": 0,
  "record_after_refresh": {
    "state": "ready",
    "project_type": "预披露",
    "source_type": "央企",
    "status_detail": ""
  }
}
```

这组对照说明 5.29 的产品语义很明确：缺失模板文件不会把映射链彻底锁死，但会让默认交付包在首轮真实导入时稳定暴露内部路径错误，并把本可直接依赖默认表完成的类型归类压力全部推给人工映射。它是“默认资源缺失导致的首轮退化”，不是“用户无论如何都无法恢复”的死锁。

### 7.29 5.29 在真实待补列表、记录详情与前端回刷反馈中的用户可见表现

我在隔离工作区里先用真实 `AppService.launch_manual_import()` 导入上述 `G32026SH1000077-0` 样本，再起真实 `build_handler(service)` 和同源代理页服务回放 `desktop_app/index.html + renderer.js`；前端逻辑本身未改写，只补了最小 `window.peapDesktop` bridge 来承接 Electron API。得到：

```json
{
  "http_before_save": {
    "pending_count": 1,
    "pending_first": {
      "project_code": "G32026SH1000077-0",
      "has_status_detail": false,
      "payload_project_type": "预披露",
      "payload_group_name": "中国葛洲坝集团股份有限公司"
    },
    "record_first": {
      "state": "pending_mapping",
      "status_detail": "entity_type_mapping_file not found: ../ppe_config/group_type_mapping_template.csv"
    }
  },
  "frontend_before_save": {
    "heroRuntimeStatus": "运行环境已就绪",
    "pendingMappingsSummary": "当前 1 条待补项",
    "pendingItemText": "G32026SH1000077-0 · 山东葛洲坝利鑫能源有限公司51%股权\\n公司：中国葛洲坝集团电力有限责任公司 · 当前集团：中国葛洲坝集团股份有限公司\\n导入规则\\n仅重处理",
    "recordsSummary": "共 1 条 · 第 1 / 1 页 · 本页 1 条 · 待补映射 1 条",
    "recordsStatusLabel": "待补映射",
    "recordsStatusDetail": "entity_type_mapping_file not found: ../ppe_config/group_type_mapping_template.csv"
  },
  "frontend_after_save": {
    "mappingResult": "映射规则已保存，已启动映射回刷任务：325d0c6aaea44b85bf3ebf160cd41175，影响 1 条记录",
    "pendingMappingsSummary": "当前没有待补项",
    "recordsSummary": "共 1 条 · 第 1 / 1 页 · 本页 1 条 · 已录入 1 条",
    "recordsStatusLabel": "已录入",
    "recordsStatusDetailCount": 0
  }
}
```

这组证据把 5.29 的用户可见影响具体化成三件事：

- 映射页知道“有 1 条待补”，但不解释待补原因，用户看不到默认模板缺失这件事
- 记录页会把内部相对路径错误原样暴露给用户，而不是翻译成产品语义层提示
- 用户手工补完 `group -> type` 后，页面能恢复到 `ready`，但保存反馈只告诉用户“回刷任务已启动”，不会解释为什么刚才那条记录会先被打成 `pending_mapping`

### 7.30 `pending_mapping` 的 31 条真实记录里，`29` 条是 `5.27 + 5.29` 叠加，只有 `2` 条是纯 `5.29`

我继续在同一批 `submission/2026年3月` 40 个真实样本的整批导入结果上，只看那 `31` 条 `pending_mapping` 最新记录，并按两个真实阻塞信号分层：

- 是否同时带有 4 条默认模板缺失路径错误
- 是否仍带有“项目类型未识别，暂不能进入导出”

得到：

```json
{
  "pending_total": 31,
  "stacked_5_27_plus_5_29": {
    "count": 29,
    "payload_project_type": "未知",
    "examples": [
      "GR2026SH1000324-4",
      "GR2026SH1000266-4",
      "Q32025SH1000075-2"
    ]
  },
  "pure_5_29": {
    "count": 2,
    "project_codes": [
      "Q32026SH1000010-0",
      "G32026SH1000077-0"
    ],
    "project_type": "预披露"
  },
  "other": 0
}
```

这把 section 10 里对操作号 `28` 的“至少两类真实问题”进一步量化成了稳定分布：当前批次里，绝大多数待补项并不是“单纯缺一条类型映射”，而是 `项目类型` 已经退化为 `未知`，同时又叠加了默认模板缺失。它们在记录层会同时暴露两类阻塞项，但产品后果更接近 5.27：记录已经丢失了业务类型语义。结合 5.27 与 7.26 的既有复核，可以把这 `29` 条视为当前产品内不可自恢复的一组，用户继续补 `group/transferor -> source_type` 并不能把它们推进到 `ready`。

剩下的 `2` 条纯 `5.29` 记录，则都落在 7.26 已识别出的两个“项目类型本身仍正确”的预披露样本上。我分别补了两条真实恢复验证：

```json
{
  "recoverability_checks": [
    {
      "project_code": "G32026SH1000077-0",
      "rule": "group -> source_type = 央企",
      "result": "mapping_refresh success -> ready"
    },
    {
      "project_code": "Q32026SH1000010-0",
      "rule": "transferor -> source_type = 民企",
      "result": "mapping_refresh success -> ready"
    }
  ]
}
```

也就是说，纯 `5.29` 在当前产品里虽然首轮体验退化，但仍属于“用户知道该补什么规则时可以自恢复”的一类；而 `29` 条叠加了 5.27 的记录，则仍然需要工程侧介入修正项目类型语义。

再把这组分层和 7.29 的真实前端回放放在一起看，当前待补入口虽然实际承载了 `29` 条“需要工程介入”和 `2` 条“可人工补规则恢复”的不同后果，但对用户呈现时仍会被压平为同一种“待补映射”卡片。用户从入口上看不出哪些记录还有自恢复空间，哪些实际上已经超出了当前映射 UI 的可处理范围。

### 7.31 5.28 在 `48` 上的真实断裂面还包括“任务事件无归档，记录页懒修复后只生成匿名归档名”

我继续用两条真实北交互联品牌化 OTC 详情页做轻量复核：

- `GR2026BJ1001615-2台机器设备（半轴壳体清洗机等）.html`
- `GR2026BJ1001619-某单位报废设备资产一批.html`

两条都先通过真实 `AppService.launch_manual_import()` 单页导入，等待任务结束，再分别对照：

- 任务层 `get_job(job_id)` / `get_job_events(job_id)`
- 记录层 `store.iter_latest_records(states=["skipped"])`
- 记录页读路径 `list_records({"state":"all","project_type":"all"})`

得到：

```json
{
  "before_list_records": [
    {
      "sample": "GR2026BJ1001615",
      "job_status": "success_with_warnings",
      "job_event_status": "skipped",
      "job_event_project_code": "",
      "job_event_archive_path": "",
      "record_project_code": "",
      "record_archive_path": "",
      "record_source_file": ".../import_1/GR2026BJ1001615-2台机器设备（半轴壳体清洗机等）.html"
    },
    {
      "sample": "GR2026BJ1001619",
      "job_status": "success_with_warnings",
      "job_event_status": "skipped",
      "job_event_project_code": "",
      "job_event_archive_path": "",
      "record_project_code": "",
      "record_archive_path": "",
      "record_source_file": ".../import_2/GR2026BJ1001619-某单位报废设备资产一批.html"
    }
  ],
  "after_list_records": [
    {
      "sample": "GR2026BJ1001619",
      "record_project_code": "",
      "record_archive_path": ".../submission/unknown_month/unknown.html"
    },
    {
      "sample": "GR2026BJ1001615",
      "record_project_code": "",
      "record_archive_path": ".../submission/unknown_month/unknown__conflict1.html"
    }
  ],
  "archive_files": [
    "unknown_month/unknown.html",
    "unknown_month/unknown__conflict1.html"
  ]
}
```

这组证据把 5.28 在操作号 `48` 上的断裂面具体化成了三个层次：

- 任务层：手动导入任务会把页面计作 `imported_count = 1 / skipped_count = 1`，但对应 `job_event` 里 `project_code` 与 `archive_path` 都为空，任务视角拿不到任何可追踪的归档对象。
- 记录层：在首次进入记录页之前，这类 `skipped` 记录仍只保留原始导入目录 `source_file`，`archive_path` 为空，说明导入当下并没有形成稳定归档闭环。
- 归档层：记录页读路径触发 `_repair_missing_archives_once()` 后，系统虽然会把原 HTML 补拷进归档目录，但因为 `project_code / project_name / listing_date` 都已经空掉，只能退化成匿名 `unknown_month/unknown*.html`；多条真实页面会进一步挤压成 `unknown__conflict1` 这类无业务语义的冲突名。

这意味着 5.28 对 `48` 的影响不只是“被 skip 的页面没有录成正式记录”，而是：

- 任务完成时没有可识别的归档锚点
- 记录页后续看到的归档文件名已经丢失原始项目身份
- 多条真实页面会在归档目录里压缩成一组匿名 `unknown*` 文件，用户无法从任务、记录、归档三者之间恢复出明确对应关系

按产品语义看，这仍然属于当前 section 10 已经 reopen 的 `48`，不需要再开新的操作号；但它把 `48` 的真实断裂面从“导入后只落空白 skipped 记录”扩成了“任务无归档锚点，记录读路径再把它们匿名归档化”。

### 7.32 在混合了“纯 `5.29`”与“`5.27 + 5.29`”的真实待补集合里，前端仍把两者压平成同一种可补规则草稿

我继续用两条已经在 7.28 / 7.30 里定性的真实样本做最小前端复核：

- 纯 `5.29`：`G32026SH1000077-0-山东葛洲坝利鑫能源有限公司51%股权.html`
- `5.27 + 5.29` 叠加：`GR2026SH1000324-4-淮安市淮阴医院有限公司部分资产（一台双源CT机）.html`

先在隔离工作区里用真实 `AppService.launch_manual_import()` 把这两条都导入成 `pending_mapping`，再起真实 `build_handler(service)` 和同源代理页服务回放 `desktop_app/index.html + renderer.js`。先读真实 `/api/mappings` 返回，再在页面上进入“映射补录”并点击“一键导入待补项”，得到：

```json
{
  "pending_payload": [
    {
      "project_code": "GR2026SH1000324-4",
      "project_type": "未知",
      "transferor": "淮安市淮阴医院有限公司",
      "group_name": "中国华润有限公司"
    },
    {
      "project_code": "G32026SH1000077-0",
      "project_type": "预披露",
      "transferor": "中国葛洲坝集团电力有限责任公司",
      "group_name": "中国葛洲坝集团股份有限公司"
    }
  ],
  "pending_cards": [
    {
      "title": "GR2026SH1000324-4 · 淮安市淮阴医院有限公司部分资产（一台双源CT机）",
      "meta": "公司：淮安市淮阴医院有限公司 · 当前集团：中国华润有限公司",
      "buttons": ["导入规则", "仅重处理"]
    },
    {
      "title": "G32026SH1000077-0 · 山东葛洲坝利鑫能源有限公司51%股权",
      "meta": "公司：中国葛洲坝集团电力有限责任公司 · 当前集团：中国葛洲坝集团股份有限公司",
      "buttons": ["导入规则", "仅重处理"]
    }
  ],
  "draft_items_after_import": [
    {
      "title": "GR2026SH1000324-4 · 淮安市淮阴医院有限公司部分资产（一台双源CT机）",
      "rule_kind": "集团 -> 类型",
      "source_name": "中国华润有限公司"
    },
    {
      "title": "G32026SH1000077-0 · 山东葛洲坝利鑫能源有限公司51%股权",
      "rule_kind": "集团 -> 类型",
      "source_name": "中国葛洲坝集团股份有限公司"
    }
  ]
}
```

这组前端证据说明，后端待补 payload 其实已经包含了一个足以区分两类后果的真实信号：`project_type = 未知` 与 `project_type = 预披露`。但当前待补入口在用户可见层并没有利用这个分化信号，而是把两者压平成同一套产品语义：

- 待补卡片都只显示“项目编号 + 项目名称 + 公司 + 当前集团”，按钮也都是同样的“导入规则 / 仅重处理”
- 批量导入待补项后，两条记录都被默认展开成“`集团 -> 类型`”规则草稿
- 对 `GR2026SH1000324-4` 这类 7.30 已确认“当前产品内不可自恢复”的记录，前端仍把它包装成和 `G32026SH1000077-0` 一样的“可继续补一条类型规则”的普通待补项

也就是说，当前问题已经不只是 7.29 里说的“映射页不解释待补原因”，而是更进一步：在同一批真实待补项里，前端明知后端 payload 已经携带了区分信号，却仍把“需要工程介入”和“用户补规则即可恢复”两类后果压平成同一种可操作草稿。这条新增证据仍归属于 section 10 已 reopen 的 `28`，不需要新开操作号，但它把 `28` 的产品语义问题从“原因不可见”推进到了“默认操作建议也会误导”。

### 7.33 5.28 触发匿名归档后，前端“打开归档 / 定位文件”会把用户直接带到 `unknown*`，任务面板仍无法建立对应关系

我继续只用 7.31 已经确认过的两条真实北交互联品牌化 OTC 详情页做一次合并任务复核：

- `GR2026BJ1001615-2台机器设备（半轴壳体清洗机等）.html`
- `GR2026BJ1001619-某单位报废设备资产一批.html`

先用真实 `AppService.launch_manual_import()` 把这两页放进同一个手动导入任务，再起真实 `build_handler(service)`，额外补一个同源代理页服务回放 `desktop_app/index.html + renderer.js`，只用最小 `window.peapDesktop` bridge 截获前端的 `openPath/showItemInFolder` 调用。得到：

```json
{
  "job": {
    "status": "success_with_warnings",
    "summary": {
      "imported_count": 2,
      "skipped_count": 2
    }
  },
  "job_events_raw": [
    {
      "status": "skipped",
      "stage": "reprocessing",
      "project_code": "",
      "archive_path": "",
      "payload_label": "手动导入完成"
    },
    {
      "status": "skipped",
      "stage": "reprocessing",
      "project_code": "",
      "archive_path": "",
      "payload_label": "手动导入完成"
    }
  ],
  "frontend": {
    "tasks_job_list": "手动导入解析 · 已完成，但有待处理项 / 已处理文件 2 · 已写入 0 · 异常 0 · 已跳过 2",
    "tasks_event_items": [
      "已跳过 / 手动导入完成",
      "已跳过 / 手动导入完成"
    ],
    "records_summary": "共 2 条 · 第 1 / 1 页 · 本页 2 条 · 已跳过 2 条",
    "record_open_targets": [
      ".../submission/unknown_month/unknown.html",
      ".../submission/unknown_month/unknown__conflict1.html"
    ],
    "record_locate_targets": [
      ".../submission/unknown_month/unknown.html",
      ".../submission/unknown_month/unknown__conflict1.html"
    ],
    "openPath_calls": [
      ".../submission/unknown_month/unknown.html",
      ".../submission/unknown_month/unknown__conflict1.html"
    ],
    "showItemInFolder_calls": [
      ".../submission/unknown_month/unknown.html",
      ".../submission/unknown_month/unknown__conflict1.html"
    ]
  }
}
```

这组证据把 7.31 的“匿名归档化”继续推进到了真实用户动作层：

- 任务面板层：同一任务里的两条真实页面，在前端事件列表里仍只是两条几乎完全相同的“已跳过 / 手动导入完成”；没有项目编号，也没有归档路径，任务视角依然无法建立页面级对应关系。
- 记录操作层：进入记录页后，这两条 `skipped` 记录的“打开归档”和“定位文件”按钮，实际都直接指向 `unknown_month/unknown*.html`，而不是任何仍带业务身份的文件名。
- 动作结果层：前端并不是“看不到归档就停住”，而是在用户点击操作按钮时，明确把人带到匿名归档对象；一旦同批出现多条这类页面，用户能操作到的只是一组 `unknown.html / unknown__conflict1.html`，仍无法从 UI 反推出它们分别对应哪条真实项目页。

因此，5.28 在操作号 `48` 上的产品后果已经不只是“任务没有稳定归档锚点、记录页随后匿名归档化”，还包括：前端现有的归档操作会把这种匿名状态继续固化成真实可见、可点击、但不可辨认的错配体验。这条新增证据仍归入当前已 reopen 的 `48`，不需要新开操作号，也不要求改写 section 10。

### 7.34 同样沿前端建议的“集团 -> 类型”补录路径，纯 `5.29` 会恢复，但 `5.27 + 5.29` 会在收到同类成功反馈后继续停留待补

我继续只用两条已经在 7.30 / 7.32 定性的真实样本做最小对照：

- 纯 `5.29`：`G32026SH1000077-0-山东葛洲坝利鑫能源有限公司51%股权.html`
- `5.27 + 5.29` 叠加：`GR2026SH1000324-4-淮安市淮阴医院有限公司部分资产（一台双源CT机）.html`

两条都先用真实 `AppService.launch_manual_import()` 导入成 `pending_mapping`，再起真实 `build_handler(service)` 和同源代理页服务回放 `desktop_app/index.html + renderer.js`。这次不走手工改 API，而是按 7.32 已确认的当前前端默认引导路径操作：

1. 进入“映射补录”面板。
2. 点击“一键导入待补项”。
3. 保持默认草稿 `规则类型 = 集团 -> 类型`。
4. 分别填入同样的目标值 `央企`。
5. 点击“保存已填写规则”。

得到：

```json
[
  {
    "case": "pure_5_29",
    "before_state": {
      "project_code": "G32026SH1000077-0",
      "project_type": "预披露",
      "state": "pending_mapping"
    },
    "frontend_before": {
      "draft_rule_kind": "group_type",
      "draft_source_name": "中国葛洲坝集团股份有限公司"
    },
    "frontend_after": {
      "mapping_result": "已保存 1 条规则，启动 1 个映射回刷任务，共影响 1 条记录",
      "pending_summary": "当前没有待补项",
      "records_summary": "共 1 条 · 第 1 / 1 页 · 本页 1 条 · 已录入 1 条"
    },
    "after_state": {
      "state": "ready",
      "project_type": "预披露",
      "status_detail": ""
    }
  },
  {
    "case": "stacked_5_27_plus_5_29",
    "before_state": {
      "project_code": "GR2026SH1000324-4",
      "project_type": "",
      "state": "pending_mapping"
    },
    "frontend_before": {
      "draft_rule_kind": "group_type",
      "draft_source_name": "中国华润有限公司"
    },
    "frontend_after": {
      "mapping_result": "已保存 1 条规则，启动 1 个映射回刷任务，共影响 1 条记录",
      "pending_summary": "当前 1 条待补项",
      "records_summary": "共 1 条 · 第 1 / 1 页 · 本页 1 条 · 待补映射 1 条"
    },
    "after_state": {
      "state": "pending_mapping",
      "project_type": "",
      "status_detail": "mapping applied for company=淮安市淮阴医院有限公司"
    }
  }
]
```

这组对照把 `28` 的产品语义又往前推了一步：

- 前端建议层：两条记录都被同样导成 `集团 -> 类型` 草稿，用户执行的是同一条系统建议动作，不是两套不同方案。
- 反馈层：保存后前端给出的主反馈几乎完全相同，都是“已保存 1 条规则，启动 1 个映射回刷任务，共影响 1 条记录”，用户在动作完成瞬间看不到哪一条其实仍不可自恢复。
- 结果层：纯 `5.29` 样本会从待补消失并回到 `ready`；但 `5.27 + 5.29` 样本会继续保留在待补列表里，记录页仍显示“待补映射 1 条”。
- 残余语义层：对那条仍不可恢复的 `GR2026SH1000324-4`，记录状态详情不再暴露“项目类型未识别”这类剩余阻塞项，而是退化成 `mapping applied for company=...`。从用户视角看，这更像“映射已经应用成功”，却没有解释为什么记录仍无法离开待补。

因此，`28` 当前不只是“入口把可恢复与不可恢复记录压平”，还包括：用户即便照着前端默认建议完成一次看似正确的 `集团 -> 类型` 补录，也可能收到和可恢复记录几乎同样的成功反馈，但记录依旧停留在 `pending_mapping`，而剩余阻塞原因反而变得更不透明。这条新增证据仍归入当前已 reopen 的 `28`，不需要新开操作号，也不要求改写 section 10。

### 7.35 `全部业务` 记录视图会泄露内部字段名和旧导入路径，而按钮实际打开的是另一条匿名归档路径

我这次不再围绕映射入口，而是切到“数据记录”本身，用两条 7.31 / 7.33 已确认会匿名归档化的真实北交互联品牌化 OTC 详情页做最小复核：

- `GR2026BJ1001615-2台机器设备（半轴壳体清洗机等）.html`
- `GR2026BJ1001619-某单位报废设备资产一批.html`

两条都先用真实 `AppService.launch_manual_import()` 导入，再调用一次真实 `list_records({"state":"all","project_type":"all"})` 触发缺失归档修复。随后分别检查：

- 服务层 `list_records({"state":"all","project_type":"all"})` 返回的列和行值
- 同一批记录在前端“数据记录”面板里的真实表格与关键词搜索

得到：

```json
{
  "service_all_records": {
    "columns": [
      "项目编号",
      "项目名称",
      "项目类型",
      "交易所",
      "project_code",
      "source_file",
      "是否预披露"
    ],
    "rows": [
      {
        "record_source_file": ".../submission/unknown_month/unknown.html",
        "record_archive_path": ".../submission/unknown_month/unknown.html",
        "values_source_file": ".../import_batch/GR2026BJ1001615-2台机器设备（半轴壳体清洗机等）.html"
      },
      {
        "record_source_file": ".../submission/unknown_month/unknown__conflict1.html",
        "record_archive_path": ".../submission/unknown_month/unknown__conflict1.html",
        "values_source_file": ".../import_batch/GR2026BJ1001619-某单位报废设备资产一批.html"
      }
    ]
  },
  "frontend_search": {
    "GR2026BJ1001615": {
      "summary": "共 1 条 · 第 1 / 1 页 · 本页 1 条 · 已跳过 1 条"
    },
    "某单位报废设备资产一批": {
      "summary": "共 1 条 · 第 1 / 1 页 · 本页 1 条 · 已跳过 1 条"
    },
    "unknown": {
      "summary": "共 0 条 · 第 1 / 0 页 · 本页 0 条"
    },
    "unknown__conflict1": {
      "summary": "共 0 条 · 第 1 / 0 页 · 本页 0 条"
    }
  }
}
```

这组证据暴露了一条此前还没写出来的记录检索断裂：

- 展示层：`project_type = all` 的记录表不再只展示产品字段，而是直接把 `project_code`、`source_file` 这种内部键名当成正式列头展示出来。
- 路径一致性层：同一行记录里，顶层 `record.source_file / archive_path` 已经被修成当前真实可操作的匿名归档 `unknown*.html`，但 `values.source_file` 仍停留在旧的原始导入目录；也就是表格展示的路径和按钮实际打开/定位的路径已经不是同一个对象。
- 搜索层：前端关键词搜索能命中 `GR2026BJ1001615` 或“某单位报废设备资产一批”，并不是因为系统还能识别当前记录的业务身份，而是因为表格值里还泄露着旧导入路径；反过来，用户如果想按当前真正存在的匿名归档名 `unknown` / `unknown__conflict1` 去搜，却会得到 `0` 条结果。

因此，这不只是 7.33 里的“按钮会把用户带到匿名归档对象”，而是更进一步：`全部业务` 记录视图本身已经把一条记录拆成了两套并存但不一致的文件身份。一套是表格里泄露出来的旧 `source_file`，另一套是按钮实际操作到的匿名归档路径。用户既会看到内部字段名和绝对路径泄露，也会在搜索、表格和文件动作之间遇到对象不一致。这条是本轮新发现的问题证据，当前先只记在 7.x，不改 5.x 和 section 10。

### 7.36 记录页摘要把整批结果与当前页状态计数混写，同一筛选翻页后会显示自相矛盾的“待补/已跳过”数量

这次我刻意不再追 `28 / 48`，而是回到“数据记录”页本身，验证记录摘要能不能承担整批结果概览的角色。复现方法是：

- 用一套干净临时 `APP_HOME`，通过真实 `AppService.launch_manual_import()` 导入仓库现有唯一真实批次 `submission/2026年3月`
- 导入结果为 `40` 条记录，其中任务汇总明确给出：`pending_mapping = 31`、`skipped = 9`
- 随后启动真实 `build_handler(service)`，按前端实际参数请求 `/api/records?state=all&project_type=all&page=<n>&page_size=10`
- 最后把返回 payload 交给前端真实 `desktop_app/renderer/records.mjs` 里的 `formatRecordsSummary()`，确认页面实际展示文案

得到的真实结果如下：

```json
{
  "import_job_summary": {
    "imported_count": 40,
    "pending_mapping_count": 31,
    "skipped_count": 9
  },
  "records_pages": {
    "page1": {
      "summary.state_counts": {
        "pending_mapping": 5,
        "skipped": 5
      },
      "frontend_summary": "共 40 条 · 第 1 / 4 页 · 本页 10 条 · 待补映射 5 条 · 已跳过 5 条"
    },
    "page2": {
      "summary.state_counts": {
        "pending_mapping": 6,
        "skipped": 4
      },
      "frontend_summary": "共 40 条 · 第 2 / 4 页 · 本页 10 条 · 待补映射 6 条 · 已跳过 4 条"
    },
    "page3": {
      "summary.state_counts": {
        "pending_mapping": 10
      },
      "frontend_summary": "共 40 条 · 第 3 / 4 页 · 本页 10 条 · 待补映射 10 条"
    },
    "page4": {
      "summary.state_counts": {
        "pending_mapping": 10
      },
      "frontend_summary": "共 40 条 · 第 4 / 4 页 · 本页 10 条 · 待补映射 10 条"
    }
  }
}
```

这说明记录页摘要里其实混了两种不同层级的统计：

- 全量层：`共 40 条`、`第 1 / 4 页` 这些数字对应的是当前筛选结果的整批总量。
- 当前页层：`待补映射 X 条`、`已跳过 Y 条` 对应的却只是本页 `10` 条记录里的状态分布，而不是这 `40` 条结果的整批状态分布。
- 呈现层：前端没有任何提示这些状态数只是“本页状态计数”，而是把它们和整批总量并列写在一句摘要里，形成“同一筛选一翻页，整批待补/跳过数量就变了”的观感。

因此，这里暴露的是一条新的产品语义问题：记录页摘要看起来像整批概览，实际却把全量总数和分页局部状态拼接在一起。用户如果拿这句摘要判断“这一批到底还有多少待补、多少已跳过”，会得到随分页漂移的答案；在这 `40` 条真实样本里，任务汇总已经明确是 `31` 条待补、`9` 条已跳过，但记录页摘要从第 1 页到第 4 页只会先后显示 `5/5`、`6/4`、`10/0`、`10/0`。这条是本轮新增的独立问题证据，当前先只记在 7.x，不改 5.x 和 section 10。

## 8. 总结

截至 2026-03-23，本仓库的自动化基线稳定，桌面产品的大部分主业务链条在实现上是连贯的；但当前仍存在多处会直接破坏产品语义的高优先级问题，不再只是局部 UI 细节。

当前最需要优先修正的至少有以下几类：

- 一键执行在未创建任务时返回假成功
- 浏览器运行环境未就绪时，后端仍接受一键执行
- `rebuild` 导出并不真正重建
- 数据库丢失后会静默重建成空库
- 手动导入真实挂牌页时，`项目类型` 会退化成 `未知`，记录无法形成可恢复闭环
- 一整类北交互联真实详情页会被整页跳过
- 映射与待补链路存在多处静默截断

次一级问题主要集中在恢复路径和任务语义：

- 缺失归档修复只执行一次，运行期不可恢复
- 手动导入全失败时仍落成 `success_with_warnings`
- 运行中再次启动会把忙碌冲突暴露成 `500`
- 中断任务的 `latest_progress` 终态仍残留旧上下文
- 启动阶段如果后端在 ready 前早退，桌面端会假装启动成功
- 运行中后台崩溃后，首页仍会静默保留旧的“已就绪”状态
- 记录页在数据收缩后不会自动归位到有效页
- 高频任务场景下“最近任务”排序会失真
- 任务事件明细存在静默截断
- `conflict` 在读路径中被折叠成 `ready`，状态语义失真
- 导出任务的 `latest_progress` 被错误投影成归档进度
- 保存设置后首页默认参数不刷新
- 首次加载时任务事件面板存在并发时序空窗

这些问题的共同特征不是“代码跑不起来”，而是系统会在某些真实操作下把不完整结果、被截断结果或未发生的动作伪装成正常完成。这正是当前产品成熟度最薄弱的部分。

## 9. 已并入的其他问题报告

本报告现作为当前仓库的统一问题总表。以下正式问题报告已并入本文件，原文件保留仅用于追溯原始写作上下文：

- `docs/parser_rule_risk_report.md`

### 9.1 解析器规则误抓风险（并入自 `parser_rule_risk_report.md`）

这一组问题与前面的桌面产品操作缺陷不同，它们属于“解析器可运行，但规则可能抓错字段”的专项风险。当前还没有把它们逐条转成桌面产品层的 `5.x` 缺陷编号，但从源头质量角度，它们已经是当前总问题清单的一部分。

高优先级风险：

- 风险 A：北京“所在地区”正则分组存在歧义，命中 `存放于/位于` 分支时 `group(1)` 可能为空，影响 `所在地区`。位置：`parsers/beijing_standard.py:600`
- 风险 B：北京解析器会把“联系人”回填到“转让方”，属于跨字段污染。影响字段：`转让方`。位置：`parsers/beijing_standard.py:617`, `parsers/beijing_standard.py:620`
- 风险 C：上海增资页面把首列“名称”过宽地识别为公司名称，容易误写 `融资方`。位置：`parsers/shanghai_standard.py:158`, `parsers/shanghai_standard.py:267`, `parsers/shanghai_standard.py:272`
- 风险 D：深圳项目编号兜底模式接受通用 `CQ\\d+`，容易吸入非业务编号文本。影响字段：`项目编号`。位置：`parsers/shenzhen.py:18`, `parsers/shenzhen.py:180`

中优先级风险：

- 风险 E：广州类型推断对“中国”关键词过敏，容易把非央企误判成 `央企`。影响字段：`类型`。位置：`parsers/guangzhou.py:374`, `parsers/guangzhou.py:772`
- 风险 F：广州在缺少结构化字段时，会从整页文本猜 `隶属集团`，可能吸入页脚或平台公共文案。位置：`parsers/guangzhou.py:44`, `parsers/guangzhou.py:402`, `parsers/guangzhou.py:756`
- 风险 G：重庆缺编号时会用 URL `id` 伪造 `CQIDxxxxx` 作为标准项目编号，语义不稳。影响字段：`项目编号`。位置：`parsers/chongqing.py:298`, `parsers/chongqing.py:300`
- 风险 H：山东在项目名称存在时仍会用 `<title>` 无条件覆盖 `项目名称`。位置：`parsers/shandong.py:170`, `parsers/shandong.py:172`

低优先级风险：

- 风险 I：交易所识别的域名兜底可能受页面外链或脚本引用噪声影响，存在 parser 路由误判风险。位置：`parsers/utils.py:100-112`

这组并入问题的修复顺序仍沿用原报告建议：

1. A、B
2. C
3. D
4. E、F
5. G、H、I

## 10. 当前仍未闭环的操作编号

原结论需再次修正。上一版把 `13`、`15`、`18`、`29-31`、`44`、`46`、`48`、`52`、`55` 补齐后，暂时写成了“当前没有新的明确未闭环操作编号”；但本轮基于真实 `AppService` + 当前 `submission/2026年3月` 样本复核后，至少有两项需要重新打开，其中 `28` 的作用域需要扩大、`48` 需要收窄：

- `28` 规则命中 `pending_mapping` 记录：当前至少包含两类真实问题。其一是 5.27，记录以 `project_type_unknown` 入库，并同时命中 `equity_transfer / physical_asset / pre_disclosure` 等筛选，即便补齐 `类型` 映射仍无法离开 `pending_mapping`；其二是 5.29，默认模板缺失会把本来 `项目类型` 正常的记录先打进 `pending_mapping`，映射页不显示真实原因，记录页直接暴露内部相对路径错误，但用户手工补 `group -> type` 后可以恢复到 `ready`。按操作域判断，5.29 暂归入 `28`，不另开新的操作号。
- `48` 导入后的任务、记录、归档联动：当前明确对应 5.28。一类北交互联真实详情页会被整页 skip，只落空白 `skipped` 记录，无法进入后续映射或导出主链

如果后续还要继续扩展，重点已从“操作闭环是否成立”转向两类更深水位工作：

- 更大样本量的数据质量验证，尤其是 parser / postprocess 组合下的字段正确性。
- 已确认缺陷修复后的回归验证。

这两类都属于下一阶段的质量推进，不再属于本节“未闭环操作编号”的范畴。

## 11. 2026-03-24 并行补充筛查

### 11.1 本轮筛查矩阵与分工

本轮没有再按“哪里看起来可疑就查哪里”的方式扩散，而是先按产品边界拆成 5 个彼此相对独立的排查子域，再用 5 个只读 subagent 并行筛查，最后由主线程做去重和复核，只把已确认的问题继续并入本报告。

- 子域 A：启动 / 就绪 / backend restart / 前后端失联语义
- 子域 B：任务系统 / job / job_events / latest_progress
- 子域 C：映射治理 / 待补入口 / 批量草稿 / 回刷
- 子域 D：记录列表 / 筛选 / 分页 / 导出语义
- 子域 E：手动导入 / 失败记录 / 配置与恢复边界

本轮新增条目主要来自 B、C、D、E 四个子域；A 子域有相邻控制流问题，但本轮没有把它们转成新的 `5.x` 编号。

### 11.2 本轮新增已确认问题（编号续接第 5 节）

### 5.30 P2: 导出入口与记录页筛选语义断裂，当前记录视图不能按同一范围导出

#### 现象

记录页查询明确带有：

- `state`
- `project_type`
- `keyword`
- `date_from / date_to`

但前端“导出 Excel”入口当前只提交：

- `date_from`
- `date_to`

因此用户即使刚在记录页查看的是某个业务类型或关键字子集，点击导出后，后端仍会按“该日期范围下全部 `ready` 记录”导出，而不是按当前记录视图导出。

#### 已复现

我在隔离服务里插入了同一天的两条 `ready` 记录：

- `股权转让` 1 条
- `实物资产` 1 条

随后：

- `list_records(project_type=equity_transfer, date_from=2026-03-21, date_to=2026-03-21)` 返回 `1` 条
- `run_export(date_from=2026-03-21, date_to=2026-03-21)` 返回 `new_records=2`，并生成 `2` 个导出文件

这说明“当前记录页只看见 1 条”和“导出实际导出 2 条”已经可以在真实服务层直接同时成立。

#### 根因

- 记录页查询构造器会把 `project_type`、`keyword` 等条件写入请求：`desktop_app/renderer/records.mjs:6-30`
- `loadRecords()` 也会把这些条件传给 `/api/records`：`desktop_app/renderer.js:1087-1106`
- 但 `runExport()` 只把日期塞进 `/api/exports`：`desktop_app/renderer.js:1210-1218`
- 后端 `run_export()` 虽然支持 `business_types`，但前端从未提交：`desktop_backend/app_service.py:1517-1525`
- 导出核心逻辑在 `business_types` 为空时默认导出全部业务类型：`peap/streaming_export.py:151-167`

#### 影响

- 记录页所见范围和导出结果范围不一致
- 用户容易把导出结果误当成“当前筛选结果的 Excel”
- 一旦记录页带关键字或业务类型筛选，导出就会发生静默扩张

### 5.31 P2: 失败/跳过记录的真实错误原因在记录页投影里丢失

#### 现象

`records` 主表本身保存了：

- `last_error_type`
- `last_error_message`

同时，记录页状态详情构造函数也明确依赖这两个字段：

- `parse_failed / postprocess_failed` 直接取 `last_error_message`
- `skipped` 优先取 `last_error_message`，没有才退回通用文案

但列表查询路径实际没有把这两个字段读出来，于是失败记录在 `/api/records` 里会稳定丢失真实错误原因，`skipped` 记录则会被压平成“当前网页按规则跳过，不进入录入”。

#### 已复现

我直接插入了一条：

- `state = parse_failed`
- `error_message = parser exploded`

随后真实调用 `list_records()`，返回结果中对应行的 `status_detail` 为空字符串，而不是 `parser exploded`。

#### 根因

- `records` 表定义里有 `last_error_type / last_error_message`：`peap/streaming_store.py:45-60`
- `upsert_failed_record()` 也确实会把这两个字段写进主表：`peap/streaming_store.py:889-947`
- 但 `iter_latest_records()` 的 `SELECT` 并没有取这两列：`peap/streaming_store.py:1346-1394`
- `_record_status_detail()` 却继续依赖它们来生成详情：`desktop_backend/app_service.py:277-304`
- `list_records()` 最终把这个空详情直接投影给前端：`desktop_backend/app_service.py:832-847`

#### 影响

- `parse_failed / postprocess_failed` 记录在记录页里失去可诊断错误原因
- `skipped` 记录会被系统性压平到通用提示，真实跳过原因消失
- 失败记录虽然入库了，但用户无法从主列表判断到底为什么失败

### 5.32 P2: `mapping_refresh` 在 0 条实际修复成功时仍会落账为 `success_with_warnings`

#### 现象

当前 `mapping_refresh` 任务的终态判定并不是“是否真的修复成功了至少一条记录”，而是“是否曾经进入过非异常返回路径”。因此只要 `reprocess_record()` 返回了结果对象，即使返回状态是：

- `parse_failed`
- `postprocess_failed`
- 其他非 `ready / pending_mapping / conflict / skipped`

任务仍可能落成 `success_with_warnings`，而不是 `failed`。

#### 已复现

我在隔离服务里构造了一条待回刷记录，并把 `reprocess_record()` 临时替换为固定返回：

```json
{"state": "parse_failed"}
```

随后真实执行 `_run_mapping_refresh_job()`，最终账本结果是：

- `status = success_with_warnings`
- `summary.failed_count = 1`
- `summary.refreshed_count = 1`

也就是“0 条真正修复成功、1 条失败”仍被记成成功类终态。

#### 根因

- 每次非异常返回都会先执行 `refreshed += 1`：`desktop_backend/app_service.py:1185`
- 随后才把非 `ready / conflict / pending_mapping / skipped` 的状态记为失败：`desktop_backend/app_service.py:1186-1197`
- 终态判定又使用了 `failed > 0` 且 `refreshed > 0` 就降级成 `success_with_warnings` 的规则：`desktop_backend/app_service.py:1213-1217`

#### 影响

- 映射回刷会把“全部失败”伪装成“已完成，但有待处理项”
- 用户无法从任务终态区分“部分成功”与“全量失败”
- 与 `5.15` 的手动导入假成功形成了同类任务语义裂缝

### 5.33 P2: 批量保存草稿会被首条规则触发的 `mapping_refresh` 自锁，第二条开始稳定失败

#### 现象

前端批量保存草稿并不是真正的“整批提交”，而是顺序逐条调用单条保存。一旦第一条规则命中了记录，后端会立刻启动后台 `mapping_refresh`，并在该线程结束前一直持有全局互斥；于是第二条草稿紧接着保存时，就会被第一条规则自己触发的回刷任务反锁。

#### 已复现

我在隔离服务里准备了两条不同来源的待补记录，并把 `reprocess_record()` 人为放慢。随后连续调用两次 `upsert_mapping()`：

- 第一次正常返回 `job_id`
- 第二次立即抛出：`RuntimeError: 已有执行中的任务：映射回刷`

这说明当前“批量保存已填写规则”在第一条真的触发回刷时，会从第二条开始稳定失败。

#### 根因

- 前端批量保存是顺序循环单条 `saveMapping`：`desktop_app/renderer/mappings.mjs:120-158`
- `upsert_mapping()` 进入时先占用全局 `mapping_refresh` 互斥：`desktop_backend/app_service.py:1437-1440`
- 若命中记录，则 `_launch_mapping_refresh_job()` 启动后台线程，并把锁延迟到线程结束后才释放：`desktop_backend/app_service.py:1229-1266`
- 全局互斥本身不区分“批量保存调用栈里的下一条规则”与“外部并发请求”：`desktop_backend/app_service.py:1037-1056`

#### 影响

- 草稿区“批量保存”在真实命中场景下退化成“首条成功，后续被自己锁死”
- 批量维护规则时会产生不稳定的半成功状态
- 用户会把这类失败误解成偶发并发冲突，而不是产品路径本身不可连续执行

### 5.34 P2: 草稿区对“同源同维度不同目标值”不做冲突归并，会把一次覆盖误报成两条成功保存

#### 现象

草稿区当前的批量去重键包含 `target_value`。这意味着两条草稿只要目标值不同，就会被视为两条独立规则；但后端存储层的唯一键只看：

- `match_field`
- `target_field`
- `source_name`

因此对同一“规则槽位”的连续两次保存，前端会报告“保存了两条规则”，而存储层最终只会留下后一条。

#### 已复现

我直接运行了前端模块 `runBatchMappingUpsertFlow()`，输入两条草稿：

- `group_type / 华润 / 央企`
- `group_type / 华润 / 地方国企`

返回结果是：

- `savedCount = 2`
- `refreshJobs = 2`

同时 `saveMapping()` 收到了两次调用。但存储层 `upsert_mapping_entry()` 的唯一键并不包含 `target_value`，因此这两次保存最终只能落成同一个 `entry_id`，后一条覆盖前一条。

#### 根因

- 批量去重键包含 `target_value`：`desktop_app/renderer/mappings.mjs:107-127`
- `savedCount` 也是按这个粒度累计：`desktop_app/renderer/mappings.mjs:145-153`
- 存储层 `entry_id` 只由 `match_field + target_field + normalized source_name` 生成：`peap/streaming_store.py:641-684`

#### 影响

- 前端会把“一次覆盖”投影成“两条规则都保存成功”
- `savedCount` 与真实入库槽位数不一致
- 用户无法从批量保存反馈里看出哪条规则其实已经被后一条覆盖

### 5.35 P3: 不存在的任务在 `/api/jobs/:id/events` 路径上会被伪装成 `200 + []`

#### 现象

当前任务资源在两条读取路径上语义不一致：

- `GET /api/jobs/:id` 对不存在任务返回 `404`
- `GET /api/jobs/:id/events` 对不存在任务返回 `200`，且 `events = []`

这会把“任务不存在”和“任务存在但暂时没有事件”压平成同一个响应形态。

#### 已复现

我直接在隔离服务里调用：

```python
service.get_job_events("no-such-id")
```

返回结果就是：

```python
[]
```

#### 根因

- `get_job()` 会调用 `store.get_job()`，不存在时抛 `KeyError`：`desktop_backend/app_service.py:1013-1017` + `peap/streaming_store.py:977-991`
- 但 `get_job_events()` 只直接调用 `store.list_job_events()`，没有任何存在性校验：`desktop_backend/app_service.py:1019-1021`
- HTTP handler 也会把这个空数组直接包装成 `200 OK`：`desktop_backend/app_backend.py:94-99`

#### 影响

- 资源语义分裂：同一任务资源的详情路径和事件路径不一致
- 前端无法区分“任务不存在”与“任务存在但没有事件”
- 出现错误 `job_id` 时，系统会把真正的资源缺失静默吞成空白明细

### 11.3 本轮关键复现摘录（编号续接第 7 节）

### 7.37 记录页筛选与导出范围断裂

真实隔离复现结果：

```text
records_total= 1 project_type= 股权转让
export_new_records= 2 artifacts= 2 job_metadata_types= []
```

这说明记录页当前只显示 `1` 条股权转让记录时，导出仍然会把同日期下的另一条 `实物资产` 一并导出。

### 7.38 `parse_failed` 记录在记录页丢失错误详情

真实隔离复现结果：

```text
parse_failed_status_detail=
```

状态详情被投影成空字符串，而不是入库时的真实错误消息 `parser exploded`。

### 7.39 `mapping_refresh` 全失败仍记为 `success_with_warnings`

真实隔离复现结果：

```text
mapping_refresh_status= success_with_warnings summary= {'failed_count': 1, 'pending_mapping_count': 0, 'refreshed_count': 1, 'skipped_count': 0}
```

这条证据说明“1 条失败、0 条实际修复成功”仍然会落成成功类终态。

### 7.40 批量保存草稿被首条 `mapping_refresh` 自锁

真实隔离复现结果：

```text
first_mapping_job= c6c1831356fb4d2b890fb1a03b3202d8
second_mapping_result= RuntimeError: 已有执行中的任务：映射回刷
```

第一条规则一旦触发真实回刷，第二条规则立刻被前一条自己占住的互斥锁挡住。

### 7.41 草稿区会把一次覆盖误报成两条成功保存

真实前端模块复现结果：

```json
{
  "result": {
    "savedCount": 2,
    "skippedOverwriteCount": 0,
    "failedCount": 0,
    "refreshJobs": [
      { "job_id": "job-1", "affected_count": 1 },
      { "job_id": "job-2", "affected_count": 1 }
    ]
  },
  "calls": [
    {
      "source_name": "华润",
      "target_value": "央企",
      "match_field": "group",
      "target_field": "source_type",
      "notes": ""
    },
    {
      "source_name": "华润",
      "target_value": "地方国企",
      "match_field": "group",
      "target_field": "source_type",
      "notes": ""
    }
  ]
}
```

前端把同一规则槽位上的两次不同目标值保存都算作独立成功。

### 7.42 不存在 `job_id` 的事件查询会被吞成空列表

真实隔离复现结果：

```python
[]
```

这说明缺失任务不会被事件接口按 `not_found` 处理，而是被静默压平成“没有事件”。

### 11.4 本轮去重后未另立项的结果

本轮并行筛查里有两类结果没有再单独开新的 `5.x` 编号：

- `待补映射列表 200 条硬上限` 已经被现有 `5.11` 覆盖，不再重复立项
- “失败记录错误详情丢失”分别被记录/导入两个子域重复发现，本轮统一并入 `5.31`

### 11.5 第 1 段补查新增已确认问题（编号续接第 5 节）

### 5.36 P2: `parse_failed` 无编号记录也会在读路径被匿名归档修复，失败证据文件身份被静默改写

#### 现象

我用真实 `AppService + StreamingStore` 复现了一条最小失败记录：

- 初始入库时：`state = "parse_failed"`
- `source_file` 指向原始失败 HTML
- `archive_path = ""`
- `project_code / project_name / listing_date` 全空

随后只做一次真实读路径调用：

- `overview()`

结果这条记录立即被修成：

- `source_file = .../submission/unknown_month/unknown.html`
- `archive_path = .../submission/unknown_month/unknown.html`

同时，失败态候选标识仍然保留在底层：

- `page_url:https://example.test/fail/no-code`
- `project_id:FAILNOCODE001`

这说明“失败记录修复”并不是停留在原始失败现场，而是会被读路径主动搬运到匿名归档名下。

#### 根因

`_repair_missing_archives_once()` 当前按最新记录全表遍历，不区分 `ready / skipped / parse_failed / postprocess_failed`：

- `desktop_backend/app_service.py:353-421`

只要发现：

- `archive_path` 不存在
- `source_file` 仍存在

它就会直接调用：

- `copy_snapshot_to_archive(...)`

而失败记录在这个场景下往往同时缺：

- `project_code`
- `project_name`
- `listing_date`

于是归档目标会退化成：

- `unknown_month/unknown.html`

对应命名回退逻辑在：

- `peap/streaming_ingest.py:95-110`
- `peap/submission_layout.py:34-51`

#### 问题本质

这不是 7.31 / 7.33 已确认那类 `skipped` 页面专属的退化路径，`parse_failed` 失败样本也会走同样的匿名归档化。也就是说，读路径并没有把“失败证据保留在原始现场”和“成功归档进入 submission 规范路径”区分开。

底层候选 token 仍然知道它是谁，但 UI 可见文件身份已经被改写成匿名 `unknown*`。结合现有 `5.31`，用户最终会同时失去两件事：

- 看不到真实失败原因
- 也看不到带业务语义的失败文件名

#### 影响

- 失败证据会被静默搬运到匿名 submission 路径
- 多条无编号失败页会继续挤压成 `unknown__conflictN.html`
- 去重层仍按真实 `page_url / project_id` 识别旧页，但用户界面只能看到匿名文件身份
- “失败记录”和“匿名归档修复”当前已经连成同一个语义裂缝，而不是两条互不相干的边角

### 5.37 P2: `interrupted` 终态在首页和任务面板会被混投成“活动失败态”，`failed` 终态摘要也仍然偏粗糙

#### 现象

我用真实后端重启复现了一条被中断的 `manual_import` 任务。后端返回的终态是：

- `job.status = "interrupted"`
- `overview.latest_progress.phase_code = "interrupted"`
- `overview.latest_progress.phase_label = "已中断"`

但同一份终态里仍然保留：

- `current_task_label = "137.html"`
- `task_index = 44`
- `task_total = 300`
- `archive_pending_count = 32`

同时，任务事件最新一条又是：

- `stage = "failed"`
- `status = "interrupted"`
- `payload.label = "任务已中断"`

这意味着后端本身已经在同一任务上同时暴露了“中断”和“失败阶段”两套语义。

前端继续把这套混合语义放大：

- 首页进度条是否保持活动由 `progressPreset()` 决定，但终态白名单里没有 `interrupted`
- 首页 `renderProgress()` 没有 `interrupted` 专门分支，因此元信息会落回通用的“已保存网页 / 已存档 / 异常”模板
- 首页提示文案也没有 `interrupted` 分支，会回退到默认的“暂无任务时，可以直接选择日期范围后执行一键任务”
- 任务面板事件标题按 `stageLabel(event.stage)` 生成，因此 `stage="failed" + status="interrupted"` 会被渲染成“处理失败”，而不是“任务已中断”

对应实现位于：

- `peap/streaming_store.py:293-346`
- `desktop_app/renderer.js:288-315`
- `desktop_app/renderer.js:358-428`
- `desktop_app/renderer.js:431-466`
- `desktop_app/renderer.js:557-604`

#### 问题本质

当前产品不是简单地“某处文案不够好”，而是首页、任务列表、任务事件三处终态投影没有共用同一套终态模型：

- 任务列表标题看的是 `job.status`，所以显示“已中断”
- 首页主状态看的是 `latest_progress.phase_label`，所以也显示“已中断”
- 任务事件标题看的是 `event.stage`，所以同一条中断事件又显示成“处理失败”

此外，`failed` 虽然比 `interrupted` 多了一条专门 hint，但首页元信息仍没有失败专属终态摘要，依旧回落到通用计数句式，导致失败原因只能去任务事件里猜。

#### 影响

- 用户执行“强制停止”后，首页进度条仍可能保持活动态
- 同一任务会同时被解释成“已中断”和“处理失败”
- 首页终态摘要无法稳定表达“中断 / 失败 / 成功但有警告”的边界
- 任务面板不再是可信的终态解释层

### 11.6 第 1 段关键复现摘录（编号续接第 7 节）

### 7.43 `parse_failed` 无编号记录在第一次读路径时也会被匿名归档化

真实隔离复现结果：

```json
{
  "before": {
    "state": "parse_failed",
    "source_file": ".../raw/failed-no-code.html",
    "archive_path": ""
  },
  "after": {
    "state": "parse_failed",
    "source_file": ".../submission/unknown_month/unknown.html",
    "archive_path": ".../submission/unknown_month/unknown.html"
  },
  "tokens": [
    "page_url:https://example.test/fail/no-code",
    "project_id:FAILNOCODE001"
  ]
}
```

这条证据把 7.31 / 7.33 的“匿名归档化”从 `skipped` 页面扩到了真实 `parse_failed` 失败页。

### 7.44 被中断任务的后端终态已经同时携带 `interrupted` 与 `failed` 两套事件语义

真实隔离复现结果：

```json
{
  "overview": {
    "phase_code": "interrupted",
    "phase_label": "已中断",
    "current_task_label": "137.html",
    "task_total": 300,
    "archive_pending_count": 32
  },
  "latest_event": {
    "stage": "failed",
    "status": "interrupted",
    "payload": { "label": "任务已中断" }
  }
}
```

后端已经给出这组互相交叠的终态信号，前端再按 `phase_code / job.status / event.stage` 分头渲染后，就会自然出现“首页已中断、事件标题处理失败、进度条仍像在动”的混投。

### 7.45 `5.30` 的实际一致性边界：只有 `ready + 同业务类型 + 无关键字` 时，记录页视图才近似等于导出集合

真实隔离复现结果：

```json
{
  "records_all": ["EQ-ALPHA", "EQ-PENDING"],
  "records_ready": ["EQ-ALPHA"],
  "records_keyword": ["EQ-ALPHA"],
  "export_default": ["EQ-ALPHA", "PH-BETA"],
  "export_eq": ["EQ-ALPHA"]
}
```

这组最小样本把 `5.30` 的边界收口成了四条明确条件：

- 记录页若停在 `state=all`，会看到 `pending_mapping`，但导出永远只取 `ready`
- 导出若不显式传 `business_types`，会把同日期下的其他业务类型一并带走
- 记录页关键词筛选不会进入导出集合
- 只有 `records_ready + export_eq` 这组才近似命中同一批记录

也就是说，当前桌面前端并不存在“按记录页当前视图直接导出”的真实产品边界；用户最多只能偶然落在这个交集里。

### 11.7 第 1 段去重与范围收口

- `/api/jobs/:id/events` 的资源不存在语义仍按现有 `5.35` 处理，本轮没有再另立新号；这一项与 `5.23` 一起，已经覆盖“资源存在性 + 截断上限”两层不一致
- “导出与记录页当前视图的一致性边界”本轮只做边界量化，不再重复开 `5.x`；新增证据已经并回现有 `5.30`

### 11.8 第 1 段补查新增已确认问题（编号续接第 5 节）

### 5.38 P1: 托管 `raw` 中的无编号失败页会在第一次读路径被搬离原始失败现场，并删除原始 HTML 与 `_files`

#### 现象

我用真实 `AppService` 构造了一条来自托管下载目录的最小失败样本：

- 记录状态：`parse_failed`
- `source_file` 位于 `DATA_ROOT/raw/auto/...`
- `archive_path = ""`
- `project_code / project_name / listing_date` 全空

第一次只调用：

- `overview()`

之后立刻出现四个结果：

- 记录被改写成 `source_file = archive_path = .../submission/unknown_month/unknown.html`
- 原始 `raw/.../failed-no-code.html` 被删除
- 原始同名 `_files` 目录被删除
- 归档目录里生成新的匿名 `unknown.html + unknown_files`

也就是说，这不是“查看后顺便补一个链接”，而是第一次读路径就把失败现场整体搬家了。

#### 根因

`_repair_missing_archives_once()` 当前对所有记录统一执行：

- 先 `copy_snapshot_to_archive(...)`
- 再 `update_record_archive_path(...)`
- 再 `update_record_source_file(...)`
- 如果原文件位于托管 `managed_raw_root` 下，再直接 `os.remove(source_file)` 并删除对应 `_files`

对应实现位于：

- `desktop_backend/app_service.py:365-418`

这段逻辑没有排除：

- `parse_failed`
- `postprocess_failed`
- 其他失败态记录

因此对无编号失败页，第一次读路径会直接走“匿名归档 + 删除原现场”。

#### 问题本质

`overview()` / `list_records()` 这类本应承担“读取现状”的入口，现在会对失败证据做破坏性重排。虽然内容副本还在，但：

- 原始抓取路径没了
- 原始 `_files` 目录没了
- 剩下的是脱离业务身份的 `unknown*`

这比 `5.36` 更严重：`5.36` 还是“匿名归档化”，这里已经升级成“匿名归档化 + 删除原始失败现场”。

#### 影响

- 读路径第一次命中就会改变失败证据的物理位置
- 原始抓取现场无法再按原路径复核
- 失败页静态资源目录会一起消失，前后差分与溯源更困难
- 对需要保留失败现场做 parser / asset / 引用链排查的场景，这是实质性的取证破坏

### 5.39 P2: 手动导入的无编号 `parse_failed` 页面在匿名归档后没有任何稳定身份锚点

#### 现象

我用真实 `launch_manual_import()` 导入一个普通占位 HTML，得到一条：

- `job.status = "success_with_warnings"`
- `record.state = "parse_failed"`
- `record.source_file = record.archive_path = .../submission/unknown_month/unknown.html`

同时继续检查三层身份信号：

- 任务事件里只剩 `payload.source_file = 原始 bad.html`
- 记录顶层可操作路径已经变成匿名 `unknown.html`
- `list_existing_candidate_tokens(states=["parse_failed"]) = []`

也就是说，这条失败页在匿名归档之后，底层已经没有任何：

- `project_code`
- `page_url`
- `project_id`

可继续作为稳定身份锚点。

#### 根因

手动导入入口只传：

- `ItemSavedPayload(source_file=...)`

对应：

- `desktop_backend/app_service.py:1304-1308`

而 `StreamingIngestRunner.ingest()` 在 `parse_failed` 分支里写入失败记录时，payload 也只有：

- `source_file`
- `project_code`

对应：

- `peap/streaming_ingest.py:248-275`

随后候选 token 聚合逻辑只会从三类来源取身份：

- `project_code`
- `parser/postprocess payload` 里的 `project_id / page_url`
- `downloaded` 事件里的 `project_code / project_id / page_url`

对应：

- `peap/streaming_store.py:1074-1138`

而手动导入失败页在这个场景下三类都没有，所以最终 token 集为空。

#### 问题本质

`5.36` 说明失败页会被匿名归档化；这里进一步确认，手动导入失败页不是“UI 上看起来匿名，但底层还知道它是谁”，而是连底层 identity token 也不存在。

这会把同一条失败页拆成两套互不闭合的身份：

- 事件里保留的是原始导入路径 `bad.html`
- 记录操作按钮指向的是匿名 `unknown.html`

而系统内部又没有 `project_code / page_url / project_id` 去把这两层重新接起来。

#### 影响

- 手动导入失败页在刷新后很难稳定追溯回原始对象
- 多条同类失败页会进一步堆叠成 `unknown__conflictN.html`，但系统内部没有稳定 token 可区分它们
- 记录层、任务层和去重层对同一失败对象不再共享同一身份坐标
- “失败记录与匿名归档修复”在手动导入链路上已经形成不可恢复的身份塌缩

### 11.9 第 1 段关键复现摘录（编号续接第 7 节）

### 7.46 托管 `raw` 下的失败页会在第一次读路径后失去原始现场

真实隔离复现结果：

```json
{
  "before_exists": true,
  "before_assets": true,
  "after_source_exists": false,
  "after_assets_exists": false,
  "after_archive_exists": true,
  "after_archive_assets_exists": true,
  "after_record": {
    "state": "parse_failed",
    "source_file": ".../submission/unknown_month/unknown.html",
    "archive_path": ".../submission/unknown_month/unknown.html"
  },
  "tokens": [
    "page_url:https://example.test/fail/raw-no-code",
    "project_id:RAWFAIL001"
  ]
}
```

这条证据说明：对托管下载目录里的失败页，第一次读路径不只是“补个匿名归档”，而是会把原始失败 HTML 与 `_files` 一并删掉，只留下 `unknown*` 副本。

### 7.47 手动导入失败页在匿名归档后，底层 candidate token 直接为空

真实隔离复现结果：

```json
{
  "job_status": "success_with_warnings",
  "record_state": "parse_failed",
  "record_source_file": ".../submission/unknown_month/unknown.html",
  "record_values_source_file": ".../manual/bad.html",
  "tokens": []
}
```

和 7.43 / 7.46 的 one-click 失败页不同，这里不是“UI 已经匿名化，但底层还能靠 `page_url/project_id` 记住对象”，而是连底层 token 也没有了。

### 11.10 第 1 段去重与范围收口

- `5.36` 现在可明确理解成“失败页也会匿名归档化”的一般问题；`5.38` 是其中更重的托管 `raw` 破坏性子情形，不与 `5.36` 重复
- `5.39` 是手动导入特有的身份塌缩问题，不与现有 `5.15`、`5.31` 重复；前两者讲的是任务落账和错误文案，这里讲的是失败对象在记录/事件/内部 token 三层彻底失去统一身份

### 11.11 第 1 段补查新增已确认问题（编号续接第 5 节）

### 5.40 P2: 失败页一旦被匿名归档，后续单条重处理会直接改读匿名副本，恢复入口不再指向原始失败对象

#### 现象

我用真实 `AppService` 做了一个最小注入式复现，只把 `_build_ingest_runner()` 临时换成假 runner，用来抓 `reprocess_record()` 最终喂进去的 `source_file`：

- 同一条手动导入的 `parse_failed` 无编号失败页，在第一次 `overview()` 之前，单条重处理读取的是原始 `manual/bad.html`
- 只调用一次 `overview()` 之后，这条记录会先被 `5.36` 的匿名归档修复改成 `submission/unknown_month/unknown.html`
- 再次调用同一个 `reprocess_record()`，runner 实际读入的已经变成匿名 `unknown.html`

也就是说，读路径不只是改了记录展示和文件按钮指向，连后续“重处理”真正读取的对象也一起换掉了。

#### 根因

`_repair_missing_archives_once()` 会在读路径里直接改写记录的 `archive_path / source_file`：

- `desktop_backend/app_service.py:353-418`

而 `_reprocess_record()` 又是一个明确的“先用 `archive_path`，没有才回退 `source_file`”策略：

- `desktop_backend/app_service.py:1488-1508`

因此，只要失败页先经过一次匿名归档修复，后续重处理就会稳定切到这个修复后的匿名副本。对 `5.38` 那类托管 `raw` 失败页，这个切换还会进一步叠加“原始 HTML 与 `_files` 已被删除”，从“对象被改写”升级成“原始对象已不在产品路径上可再用”。

#### 问题本质

这条裂缝比 `5.36 / 5.38 / 5.39` 更进一步。前几条证明的是“读路径会把失败证据匿名化、搬离原现场、打断身份锚点”；这里新确认的是：这种改写不会停留在展示层，而是会继续进入恢复链路本身，把后续重处理的输入对象也替换掉。

换句话说，产品当前不是“先读到一份匿名副本，但重处理时仍回到原失败页”，而是“读过一次之后，恢复入口就跟着改吃匿名副本”。

#### 影响

- 失败页在首次被读路径命中后，后续重处理不再针对原始失败现场
- 手动导入场景里，即使原始 `bad.html` 物理上还在，产品主路径也不再使用它
- 托管 `raw` 场景会和 `5.38` 叠加成“匿名副本成为唯一可重处理对象”
- 失败对象的取证路径、复核路径和恢复路径被读路径一次性改写，修复清单里不能再把它当成“只是文件名退化”

### 11.12 第 1 段关键复现摘录（编号续接第 7 节）

### 7.48 匿名归档后的失败页，单条重处理会改读 `unknown*.html`

真实隔离复现结果：

```json
{
  "before": {
    "source_file": ".../manual/bad.html",
    "archive_path": ""
  },
  "after_record": {
    "source_file": ".../submission/unknown_month/unknown.html",
    "archive_path": ".../submission/unknown_month/unknown.html"
  },
  "captured": {
    "before_overview_source": ".../manual/bad.html",
    "after_overview_source": ".../submission/unknown_month/unknown.html"
  },
  "manual_original_exists": true
}
```

这条证据说明：哪怕手动导入原文件物理上还存在，产品在读路径匿名归档之后，单条重处理也已经不会再回到原始失败页。

### 7.49 `5.30` 的另一条边界：记录页当前视图非空，并不意味着导出集合非空

真实隔离复现结果：

```json
{
  "records_total": 1,
  "records_states": ["pending_mapping"],
  "export_status": "empty",
  "export_message": "当前条件下没有可导出的记录；待补映射 1 条"
}
```

这把 `5.30` 的边界又往前收了一步：问题不只是“当前视图比导出集合窄时会静默扩张”，还包括“当前视图明明有记录，导出仍可能直接塌缩成 0”。只要当前视图里的记录不是 `ready`，用户就会在表格里看到真实行数，但点击导出后得到“没有可导出的记录”。

### 11.13 第 1 段去重与范围收口

- `5.40` 不与 `5.36 / 5.38 / 5.39` 重复：前几条停在“失败对象被匿名化/搬运/失去身份锚点”，这一条新增的是“后续重处理的输入对象也被读路径改写”
- `7.49` 只是在 `5.30` 上补齐“当前视图非空但导出为空”的状态维度边界，不再另开新的 `5.x`

### 11.14 并行补查新增已确认问题（编号续接第 5 节）

### 5.41 P2: `manual_import / mapping_refresh` 在完成态也会丢失任务类型语义，首页回退成“网页/归档/导出”通用话术

#### 现象

我用真实 `AppService._build_latest_progress()` 做了两个最小终态复现：

- `manual_import` 成功结束后，`phase_code = completed`，同时仍返回 `archive_completed_count = 1`
- `mapping_refresh` 以 `success_with_warnings` 结束后，`phase_code = completed_with_warnings`，同时返回 `pending_mapping_count = 1`、`archive_completed_count = 1`

这两个任务的 `downloaded / persisted` 在服务层本来分别表示：

- 已处理文件 / 已写入记录
- 已处理记录 / 已写回记录

但终态投影没有保留这层任务类型语义。前端首页只对 `export_excel` 做了完成态专门分支；对其余任务，一旦进入 `completed / completed_with_warnings`，就会统一落回：

- 元信息里的“已保存网页 / 已存档 / 已跳过 / 待补映射 / 异常”
- 提示里的“如需表格，请点击导出 Excel”或“请先处理后再导出 Excel”

也就是说，手动导入和映射回刷在完成态会被继续解释成“下载/归档/导出”一类任务。

#### 根因

后端 `latest_progress` 的终态分支只按 `job.status` 切 `completed / completed_with_warnings / failed`，但不再按 `job_type` 重建任务专属语义：

- `desktop_backend/app_service.py:706-721`

而前端 `renderProgress()` 在完成态里也只给 `export_excel` 留了特判；非导出任务会统一落回通用元信息与提示模板：

- `desktop_app/renderer.js:359-365`
- `desktop_app/renderer.js:416-465`

因此 `manual_import_scan / reprocessing` 这类运行态语义，一到终态就会被抹平成同一套“网页/归档/导出”文案。

#### 问题本质

这不是简单的文案不够细，而是首页终态模型把“任务已经完成”和“任务完成后应该如何解释这组计数”拆开了，前者保留了，后者丢了。

结果就是：运行态还能看出这是手动导入或映射回刷；一旦结束，首页就不再按任务类型解释它，只剩一套跨任务复用、但对这两类任务并不成立的通用模板。

#### 影响

- 手动导入成功后，首页会把“已处理文件 / 已写入记录”误投成“已保存网页 / 已存档”
- 映射回刷完成但仍有待补项时，首页会退回“请先处理后再导出 Excel”这类导出导向提示
- 首页终态卡片不能稳定回答“刚刚完成的到底是什么任务、这些计数分别代表什么”

### 5.42 P2: `failed` 终态在任务事件里仍按过程 `stage` 出标题，会稳定显示成“正在导出 Excel / 正在重处理记录”

#### 现象

我用真实 `StreamingStore + AppService._build_latest_progress()` 做了一个最小失败复现：

- 同一任务在首页 `phase_label = 执行失败`
- 任务列表标题是 `导出 Excel · 执行失败`
- 但任务事件最新一条同时满足：
  - `stage = exporting`
  - `status = failed`

前端事件标题只看 `stageLabel(event.stage)`，因此这条失败事件实际会显示成：

- 标题：`正在导出 Excel`
- 描述：`导出失败`

同一模型也稳定存在于：

- 手动导入异常失败：`stage = reprocessing, status = failed`
- 映射回刷异常失败：`stage = reprocessing, status = failed`

#### 根因

后端在失败事件里保留了过程阶段名，而不是改写成终态阶段：

- 导出失败：`desktop_backend/app_service.py:1560-1568`
- 手动导入异常失败：`desktop_backend/app_service.py:1336-1344`
- 映射回刷异常失败：`desktop_backend/app_service.py:1172-1180`

首页与任务列表则都按 `job.status = failed` 投影成终态失败：

- `desktop_backend/app_service.py:718-720`
- `desktop_app/renderer.js:541`

但任务事件标题生成逻辑除了 `skipped` 以外，只看 `stageLabel(event.stage)`，不看 `status = failed`：

- `desktop_app/renderer.js:581-588`

于是同一条失败事件会被标题层继续保留成“过程进行中”的样子。

#### 问题本质

`interrupted` 那条裂缝是“终态被混投成失败”；这里新增的是反向问题：`failed` 已经在首页和任务列表进入终态，但任务事件标题仍停留在过程态。

换句话说，当前任务事件面板并不是“失败终态的解释层”，而是“最后一个过程阶段名 + 失败描述”的拼接层；用户看到的是一条语义互相打架的事件，而不是统一终态。

#### 影响

- 同一失败任务会同时呈现成“执行失败”和“正在导出 Excel / 正在重处理记录”
- 任务事件面板会把终态失败伪装成仍处于某个过程阶段的最后快照
- 用户难以判断这是“在导出阶段失败”还是“还在导出，只是附了一条失败说明”

### 5.43 P2: 匿名归档后的手动导入失败页，再次导入同一原始 HTML 会生成第二条 `parse_failed` 记录

#### 现象

我用真实 `launch_manual_import()` 对同一目录里的同一个占位 HTML 连续导入两次，之间只插入一次读路径触发的匿名归档修复。最终结果是：

- 第一次导入后，失败记录已经被修成匿名 `unknown.html`
- 第二次再次导入同一个原始 `bad.html` 后，系统没有回到原失败对象
- 记录表里最终稳定出现两条 `parse_failed`：
  - 旧记录：`source_file = archive_path = .../unknown.html`
  - 新记录：`source_file = .../manual/bad.html`，`archive_path = ""`

也就是说，用户对“同一个失败页再试一次导入”的动作，当前不是落在同一失败对象上继续积累，而是直接裂成第二条失败记录。

#### 根因

匿名归档修复会直接把记录的 `source_file` 改成匿名归档路径，并同步重算 `business_key`：

- `desktop_backend/app_service.py:404-405`
- `peap/streaming_store.py:1240-1250`

而无编号失败页的 `business_key` 又本质上就是 `source_file` 的哈希：

- `peap/streaming_store.py:175-180`

手动导入再次扫描目录时，仍会把原始目录里的 `bad.html` 重新喂给 ingest：

- `desktop_backend/app_service.py:1304-1308`

一旦 parser 再次失败，`upsert_failed_record()` 就会按这个原始路径重新计算业务键：

- `peap/streaming_ingest.py:243-275`
- `peap/streaming_store.py:900-907`

而手动导入目录又不会像托管 `raw` 那样在 repair 后删除原文件，所以这条原始路径仍然存在，最终自然生成第二个失败对象。

#### 问题本质

`5.39 / 5.40` 还停在“失败对象被匿名化后，原身份坐标和重处理输入被改写”；这里再往前一步确认：连“再次导入同一个失败页”这种重试动作，也已经不能回到原对象本身。

匿名归档把旧失败对象的身份基准从原始路径切到了 `unknown*.html`；再次手动导入则继续沿原始路径建新对象。于是同一个物理失败页会在系统里分裂成两条互不复用的失败记录。

#### 影响

- 同一失败 HTML 反复导入会不断积累多条失败记录，而不是在同一对象上更新
- 旧匿名失败对象和新原始路径失败对象会并存，失败历史被拆散
- 运营无法把“再次导入”理解成一次重试，只能看到越来越多相似失败记录

### 11.15 并行补查关键复现摘录（编号续接第 7 节）

### 7.50 `manual_import / mapping_refresh` 在完成态仍返回归档型计数

真实隔离复现结果：

```json
{
  "manual_import": {
    "phase_code": "completed",
    "phase_label": "已完成",
    "archive_completed_count": 1,
    "archive_pending_count": 0,
    "downloaded_count": 1,
    "persisted_count": 1
  },
  "mapping_refresh": {
    "phase_code": "completed_with_warnings",
    "phase_label": "已完成，但有待处理项",
    "pending_mapping_count": 1,
    "archive_completed_count": 1,
    "archive_pending_count": 0,
    "downloaded_count": 1,
    "persisted_count": 1
  }
}
```

这说明对这两类任务，完成态 `latest_progress` 仍把记录处理结果包装成一套带 `archive_*` 字段的通用终态。

### 7.51 `failed` 任务会在首页/任务列表显示“执行失败”，但在事件标题里显示成过程态

真实隔离复现结果：

```json
{
  "overview_phase_label": "执行失败",
  "job_list_title": "导出 Excel · 执行失败",
  "event_stage": "exporting",
  "event_status": "failed",
  "event_title": "正在导出 Excel",
  "event_description": "导出失败"
}
```

这条证据说明：`failed` 在首页和任务列表已经是终态，但到了事件面板标题层，又会退回成“最后一个过程阶段名”。

### 7.52 同一手动导入失败页在匿名归档后再次导入，会裂成两条 `parse_failed`

真实隔离复现结果：

```json
{
  "first_job_status": "success_with_warnings",
  "second_job_status": "success_with_warnings",
  "final_count": 2,
  "final": [
    {
      "state": "parse_failed",
      "source_file": ".../submission/unknown_month/unknown.html",
      "archive_path": ".../submission/unknown_month/unknown.html",
      "values_source_file": ".../manual/bad.html"
    },
    {
      "state": "parse_failed",
      "source_file": ".../manual/bad.html",
      "archive_path": "",
      "values_source_file": ".../manual/bad.html"
    }
  ]
}
```

这说明“再次导入同一个失败文件”不会回落到原失败对象，而是把旧匿名对象和新原始路径对象并排留在库里。

### 11.16 并行补查去重与范围收口

- `/api/jobs/:id` 与 `/api/jobs/:id/events` 本轮并行复核后，仍只收敛到既有 `5.23 + 5.35` 两类差异；没有第三类稳定新裂缝
- 匿名归档后的记录页对象一致性，本轮并行复核后仍只落在既有 `7.35 + 5.40` 的延长线上，没有再长出新的“第三对象”
- 导出空态里“待补映射 N 条”会回退到更宽日期范围，而不对应记录页当前视图；这条先并入 `5.30` 的边界候选，本轮不再继续拆新号

### 7.53 `5.30` 的进一步边界：导出空态里的“待补映射 N 条”按导出请求范围重算，不按记录页当前视图计数

我在隔离服务里插入了同一天的两条 `pending_mapping`：

- `股权转让` 1 条
- `实物资产` 1 条

随后只看记录页当前 `project_type=equity_transfer` 视图，再分别调用一次当前前端等价导出请求（只带日期）和一次显式收窄业务类型的导出请求，得到：

```json
{
  "records_view": {
    "total_count": 1,
    "summary_state_counts": {
      "pending_mapping": 1
    },
    "rows": [
      {
        "record_id": "rec-eq",
        "project_type": "股权转让",
        "state": "pending_mapping"
      }
    ]
  },
  "same_date_state_counts": {
    "pending_mapping": 2
  },
  "same_date_equity_state_counts": {
    "pending_mapping": 1
  },
  "export_frontend_scope": {
    "status": "empty",
    "message": "当前条件下没有可导出的记录；待补映射 2 条"
  },
  "export_narrow_scope": {
    "status": "empty",
    "message": "当前条件下没有可导出的记录；待补映射 1 条"
  }
}
```

这说明当前导出空态里的 `待补映射 N 条` 并不是在解释“记录页此刻看到的这 1 条为什么不能导出”，而是在解释“这次导出请求覆盖的集合里还有多少 `pending_mapping`”。当导出请求只带日期、不带业务类型时，提示里的 `N` 会直接回退到同日期下更宽业务集合的计数。

### 11.17 `5.30` 的边界续收口：空导出提示里的阻塞计数也与当前记录页视图脱锚

`7.49` 已经确认，当前记录视图非空时，导出仍可能直接塌缩成空结果。`7.53` 再往前补了一层：就连空结果提示里那句“待补映射 N 条”，也不是按当前记录页视图计算，而是按导出请求自身的范围重算。

记录页这侧，查询构造器会显式携带 `project_type / keyword / date_from / date_to`，服务层 `list_records()` 也会在日期窗口内取数后继续按 `project_type` 和 `keyword` 收窄：`desktop_app/renderer/records.mjs:6-29`、`desktop_backend/app_service.py:794-830`。导出这侧，前端 `runExport()` 只提交 `date_from / date_to`：`desktop_app/renderer.js:1212-1217`。后端 `run_export()` 在空导出时又会重新调用 `count_records_by_state(date_from, date_to, business_types)` 生成提示文案：`desktop_backend/app_service.py:1577-1586`；而这个计数器本身只认识日期和业务类型，不认识当前记录页的 `keyword`，也不会自动继承前端当前 `project_type`：`peap/streaming_store.py:1145-1181`。

因此这条仍不另开新 `5.x`，而是继续并入 `5.30`。问题本质已经不只是“导出集合比当前视图更宽”，而是“连解释导出失败原因的空态计数，也跟着落在那个更宽集合上”。用户在记录页看到的是一张已被筛窄过的表，但导出空态解释的却是另一套更宽的导出范围；在当前前端实现下，这个范围默认就是日期级全集。实际影响是，用户会在表格里只看到 `1` 条待补记录，却被空态提示告知“待补映射 `2` 条”，从而误判当前筛选结果的阻塞规模和清理对象。
