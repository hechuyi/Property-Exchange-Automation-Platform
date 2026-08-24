# 运维与排障

PEAP 桌面产品的日常运行、操作、排障、提交归档与 PPE 引擎调试。当前唯一产品入口是 `frontend/ + desktop_backend/`。

## 1. 启动

```bash
bash scripts/bootstrap_desktop_env.sh
bash start.sh
```

首次初始化需要 `uv`、Node.js 18+ 和 npm。初始化脚本会按 `.python-version`、`uv.lock`、
`frontend/package-lock.json` 准备 Python/前端依赖，并把 Chromium 放入工作区缓存；缺少任一
宿主工具或锁文件时会直接失败，不会启动半成品服务。

或分步：

```bash
uv run python -m desktop_backend.app_backend --host 127.0.0.1 --port 42679
cd frontend && npm run dev
```

首次部署到新机器时，桌面启动会自动按 `~/Documents/PEAP` 建出工作区目录树，并合并旧布局数据（详见 `storage.md`）。如需自定义 workspace root，启动前导出 `PEAP_WORKSPACE_ROOT=/path/to/workspace`。

## 2. 五个主页面

### 总览

- 一键执行
- 历史区间任务
- 手动导入
- 导出
- 最新任务 / 进度摘要
- runtime readiness

### 任务

- 最近任务列表
- 归一化 meta / badge 展示

### 记录

- 状态 / 业务 / 交易所 / 关键词 / 日期筛选
- 分页
- 从当前 scope 导出
- 定位本地网页文件
- shared actionable default scope stale / missing 时，记录页仍可浏览（走 `records browse runtime`）

### 映射

- 保存映射规则
- 预览影响范围
- 查看 `entries`
- 查看 backlog `sections`
- 在当前 backend 启动会话内撤销最近一次映射规则新增、更新或删除
- 对 `mapping_gap_resolution` 执行回刷
- 对 `mapping_conflict_resolution` 执行人工裁决
- 在“待复核”页只读查看待人工复核原因；该页不修改记录、不补映射、不触发重跑

### 设置

- 保存默认交易所
- 保存默认业务范围
- 保存并发数与路径
- 安装浏览器

## 3. 操作边界

- 默认范围是 backend-owned shared actionable truth；依赖它的 action surface 只有**一键执行、历史区间和总览页导出**，不包括记录页"导出 Excel"
- `records browse runtime` 是独立 read model，可公开 `listing/all/all` 的 browse truth；这不等价于动作也已可执行
- family / business 下拉选项必须来自 backend `/api/catalog`；当前可操作 family 包括“挂牌业务”和已接入 source/runtime 的“成交业务”，未被 source 支撑的新 family 不应出现在可操作选项里
- `mapping_gap_resolution` / `mapping_conflict_resolution` / review-problem projection 是不同边界，不能互相伪装替代；legacy `business_re_evaluation` 只作内部兼容
- 历史截图、旧客户端或维护脚本仍出现 `project_type`、旧 `pending` backlog 或过期 summary 文案——那些是 historical / evidence context，不是当前产品 contract

## 4. 典型操作闭环

### 首次配置共享默认范围

设置页保存 `default_exchange`、共享默认范围和路径。**只有 shared actionable default scope 进入 ready 后**，总览页的一键执行、历史区间、依赖动作 scope 的操作才合法。如果只看到 stale / unsupported 提示，不要把"记录页还能打开"误判成动作也可执行。

### 记录浏览与导出

观察现有记录、确认状态分布、缩小业务范围或从当前筛选结果导出时，先去记录页。记录页在默认范围 stale 时仍可浏览（走 `records browse runtime`）；记录页里的"导出 Excel"直接消费当前 records browse scope。它**不会**反向修复 shared actionable default scope，因此一键执行 / 历史区间 / 总览页导出仍可能继续 stale / unsupported。

### 手动导入

如果导入目录中的文件已经明确属于某 `record_family + business_id + exchange`，可以显式携带 scope，让记录直接进入对应语义路径。如果来源业务未知 / 混杂 / 需要后续判定，应只提交目录，让系统保留 unknown truth，并在“待复核”页查看阻断原因；不要用默认范围硬覆盖。

### 映射页与待复核页处置分工

- 看到 `mapping_gap_resolution` → 先补规则再回刷
- 看到 `mapping_conflict_resolution` → 人工裁决，不能指望"全部回刷"自动解决
- “撤销上次规则变更”只在 `GET /api/mappings` 返回 `undo.available = true` 时可用；backend 重启后旧 `startup_session_id` 失效，不能跨启动会话撤销
- 待人工复核问题 → 去“待复核”页查看项目编号、来源文件、原始项目类型、系统证据和建议查看方向；不要在 mappings 页寻找处理按钮

## 5. 提交归档

```bash
uv run python scripts/prepare_submission.py
```

输出：

- 规范命名 HTML 页面
- `_files` 静态资源目录
- `_manifest.json`

记录仍在 review / failed 状态时，应先按当前产品流程处理，再做归档输出。

## 6. 排障

### 6.1 先分层定位

| 症状 | First owner |
|---|---|
| family / business / source 选项不对 | `peap_core/*_catalog.py`、`peap/business_runtime.py`、`AppService.get_catalog()` |
| records scope / export scope 行为不对 | `desktop_backend/request_contract.py`、`desktop_backend/record_scope.py`、`api.md` |
| 记录字段值不对 | `peap/streaming_ingest.py`、`peap/export_projection.py`、`peap/streaming_store.py` |
| 记录状态不对 | `peap_core/record_state_policy.py`、postprocess findings、maintenance |
| mapping backlog / conflict 表现不对 | `desktop_backend/services/mapping_service.py`、`mapping_resource_contract.py`、`peap/streaming_store.py` |
| 浏览器、路径、运行环境问题 | `desktop_backend/app_config.py`、`runtime_service.py`、`runtime_dependencies.py` |

### 6.2 当前 blocker taxonomy

- `business_resolution_required`：业务归属阻断 → `pending_review`
- `mapping_missing` / `mapping_gap` / `mapping_ambiguous`：映射缺口 → `pending_mapping`
- `mapping_conflict`：独立人工裁决路径，不应与普通 gap backlog 混
- `conflict`：另一类记录冲突，不等于 mapping conflict
- `deal_capital_increase_missing_investor_amount`：增资成交专属阻断 → `pending_review`。表示该记录缺少至少一条同时有投资方名称和投资金额的非汇总投资方行；修复方式是重新下载/导入包含完整投资方明细的页面，而非修补映射规则。此 finding 由 postprocess 产出，不会静默进入导出流。

### 6.3 选择修复动作

- parser 输出本身错了 → `reprocess_record`
- parser 没错，映射条目或 optional rule 变了 → `refresh_postprocess` 或 `mapping_refresh`
- business catalog / 业务判断修复后的 legacy 重判 → hidden/internal `business_re_evaluation`
- 导出失败 → 先看 job `result.failure_message`，再看 `/api/jobs/{job_id}/events`
- 同样数据每次读出来都不一样 → 排查是否被 maintenance 或兼容 repair 在写链上改写

### 6.4 常见误判

- 把 `mapping_conflict` 当成普通缺口 backlog
- 把 hidden/internal `business_re_evaluation` 当成 generic reprocess
- 在 UI / adapter / endpoint 层偷偷 repair scope/default，而不是回到 canonical contract
- 看到 top-level row label 正常，就误判 canonical/store 层也正常
- 看到 raw payload 有值，就误判它已进入 canonical truth

### 6.5 后台日志规范

后台日志的目标是让操作员能读懂当前任务在做什么、做到哪里、为什么告警或失败，任务结束后能看到明确的完成状态和业务汇总，同时让开发人员能回到原始上下文诊断问题。不能用"少打日志"替代可读性；也不能把下载器内部参数、巨大 JSON、英文 key-value 串直接作为操作员进度。

日志分两层：

- 操作员可见进度：`job_events`、`/api/jobs/{job_id}/events`、`/api/overview/stream` 是 UI 进度和失败解释的 truth。它必须来自结构化 job event，使用稳定阶段、中文摘要和结构化计数。
- 诊断原始日志：`logs/` 下的文件日志是补充证据，用于还原 CLI、下载器、网络、分页、重试和异常栈。它可以包含更多细节，但必须能通过 `job_id`、阶段、业务和交易所回链到操作员可见事件。

任务状态事实以 `jobs` / `job_events` 和 API job/event contract 为准；文本日志不是业务状态、审计结论、导出历史或恢复决策的唯一依据。需要审计的人工动作、维护动作和 runtime install 仍写 `audit_log` 或 operation journal。

后台业务层不得把自由文本 logger、`print()`、ASCII banner 或子进程 stdout/stderr 转储当成用户可见进度来源。downloader / runner 产出结构化事件；CLI、UI 和日志 formatter 是文本渲染层。同一事实只写一次，避免终端、文件日志和 job event 各自生成一套互相漂移的文案。

#### 6.5.1 必备上下文

每条操作员可见事件和每段诊断日志的主要状态行都必须能回答同一组问题：

| 字段 | 含义 |
|---|---|
| `job_id` / `job_type` | 哪个后台任务；一键执行、历史下载、手动导入、导出、重处理等 |
| `phase_code` / `stage` | 当前阶段；例如 `prepare_tasks`、`save_pages`、`parse_documents`、`exporting` |
| `status` | `running`、`done`、`warning`、`failed`、`empty` 等阶段状态 |
| `record_family` / `business_id` / `exchange` | 当前记录族、业务和交易所；`all` 也必须显式出现 |
| `task_label` | 人可读任务标签，例如"上交所 / 实物资产" |
| `date_range` / `scope` | 本次执行范围，例如 `2026-05-27..2026-05-31` |
| `counters` | 当前阶段有意义的计数，如 `listed`、`candidates`、`fetched`、`saved`、`skipped`、`errors` |
| `message` | 中文短句，说明状态变化、进度、告警原因或失败原因 |

事件的 canonical scope 位于 `ItemProgressEvent.payload.scope`，必须同时包含 `record_family`、`business_id`、`business_label` 与 `exchange`；API event view 将其投影为顶层 identity 字段。下载队列的 `downloaded`、`queued_for_parse`、`persisted`、`skipped`、artifact 未就绪和 worker failure 事件均不得省略该 scope；未知业务仍保留空的 `business_id`，不得静默改写为默认业务。启动、失败、中断、映射刷新等没有单条记录 scope 的生命周期事件，必须从父 job metadata 继承单一 scope 或 aggregate `family_scopes` 后再对外发布；父 metadata 也无法证明业务时，公开空 scope，不伪造默认业务。

`ItemProgressEvent.payload` 必须是 JSON object；`payload.summary_payload` 是阶段事件的核心结构，固定承载 `kind`、`task_label`、`task_index`、`task_total`、`phase_percent`、`summary`，可携带 `warning_code` / `warning_message`。`summary` 只发布白名单计数，当前至少包括 `listed`、`pages`、`collected_candidates`、`detail_candidates`、`detail_fetched`、`saved`、`list_date_skipped`、`detail_date_skipped`、`date_missing_skipped`、`resume_skipped`、`errors`、`duplicate_skipped`、`business_filter_skipped`、`missing_xmid_skipped`、`detail_failed`、`list_unaccounted`、`detail_unaccounted`。内部诊断字段可放入原始 payload / debug payload，但不能指望前端展示。

主 `message` 只写人能扫读的一句话。缺字段、映射缺口、导出阻断、source missing 等必须带稳定 `warning_code` 或 `error_type`，不能只写自然语言。记录级事件还应带 `record_id`、`project_code`、输入文件或输出文件中的至少一种可回链标识。

`stage` 必须是可映射阶段码，例如 `prepare_tasks`、`save_pages`、`manual_import_scan`、`archive_reprocess_scan`、`reprocessing`、`exporting`；中文阶段名只由 presenter / label map 渲染。新增阶段必须同步后端 label map、contract 测试和前端 presenter。`status` 是任务/事件状态或记录状态，不是 Python logging level；不得写成 `INFO`、`WARN`、`ERROR`。

#### 6.5.2 用户可见异常

用户必须能在任务页、总览页或任务详情里发现异常信息；异常不能只存在于 raw log、DEBUG、stdout/stderr 或下载器内部日志。任何 warning/error 一旦影响当前任务结果、导出资格、后续处理队列或用户是否需要重试，都必须生成 operator-visible job event，并进入 job summary / progress summary。

运行中出现异常时，后台必须维持一份可展示的异常摘要，至少包含：异常级别、业务/交易所/阶段、影响数量、用户语义原因、下一步动作或查看入口。多条同类异常可以聚合，但不能被吞掉；聚合后仍要保留代表样例和总数。任务最终状态为 `success_with_warnings`、`failed`、`interrupted` 或 `empty` 时，任务列表和总览不能只显示"完成"，必须显示对应的业务结果，例如"已完成，有 3 条待映射"、"导出被字段缺失阻断"、"未发现符合条件的项目"、"下载失败，可重试"。

可预期但会影响用户判断的情况也要可见：无列表项、全部候选被日期过滤、全部记录重复跳过、无可导出记录、字段缺失阻断、待复核/待映射/映射冲突、source missing、部分交易所失败。只有纯内部诊断噪声才留在 DEBUG 或 raw log。

#### 6.5.3 级别语义

`INFO` 只记录有业务意义的状态变化：任务开始、阶段开始/结束、进度里程碑、最终摘要、产物路径。长任务进度必须聚合或节流；同一阶段不应每保存一条都刷一行，除非处于 DEBUG 诊断模式。

`WARNING` 表示任务仍可继续但需要操作员或开发人员注意的异常状态。每条 warning 必须包含作用域、原因、影响和下一步诊断入口；例如"某交易所某业务未发现列表项，可能是筛选无结果或页面结构变化，保留 URL 到 details"。

`ERROR` 表示阶段失败、任务失败或产物不可用。每条 error 必须包含 `failure_code` / `error_type`、失败作用域、是否可重试、应查看的 job event 或日志文件位置。不能只打印异常字符串。

`DEBUG` 才允许出现完整请求参数、原始 URL、响应片段、底层重试细节、栈跟踪上下文和下载器内部变量。未开启 verbose 时，这些内容不得进入操作员可见进度。`logger.exception(...)` 只用于未知异常或代码缺陷；已建模的业务阻断不应滥用异常栈。

#### 6.5.4 可读性规则

操作员可见日志必须以中文动词短句开头，先说业务事实，再放结构化细节。禁止把 `type=... start_date=... max_pages=...` 这类内部 key-value 串作为主文案。

启动参数不得在 INFO 中整块打印为 `Run args: {...}`。INFO 只保留任务摘要，例如"一键执行：挂牌业务 / 全部业务 / 全部交易所，日期 2026-05-27..2026-05-31，最多 10 页，并发 4"；完整参数放入 DEBUG 文件日志或结构化 `details`。

进度行必须能横向比较：统一使用 `当前/总数`、`已保存`、`跳过`、`异常`、`速率`、`预计剩余`。速率和 ETA 只有在样本足够、估算稳定时显示；否则省略，不输出误导性精确值。

跨交易所 / 跨业务任务必须明确当前子任务和总任务序号，例如 `任务 3/12：上交所 / 实物资产`。不能让多条下载器日志交错后只剩时间戳和 INFO。

路径、URL、异常栈、原始请求体属于诊断细节。操作员主文案中只显示必要短路径或"见日志文件"，完整值进入结构化 details 或 DEBUG/file-only 日志。

逐记录、逐页面、逐候选的成功日志不得无限刷屏；默认只记录阶段级计数、周期性进度和末尾摘要。重复 warning 应按 `(warning_code, exchange, task_id, phase_code, normalized_reason)` 或等价 key 聚合：首次输出代表样例，阶段结束输出总数和最多 N 条样例标识。

错误集合不得以 markdown bullet、多行自由文本或 `Top errors:` 直接混入日志流。批量失败应字段化为 `errors: [{code, message, scope, sample_id}]` 或等价结构，展示层负责折叠和摘要。

#### 6.5.5 收尾汇总

每个后台任务都必须产生 terminal summary event 和 job result summary。即使无结果、部分失败、被字段缺失阻断、被用户中断，也必须有最终摘要；不能让最后一条日志停在某个中间进度。

最终汇总面向用户任务，而不是面向程序内部计数。主摘要优先回答：

- 本次任务是否完成：完成、完成但有待处理、未完成、已中断、无符合条件结果
- 本次处理了什么范围：交易所、业务、日期、导入目录或导出范围
- 用户真正关心的结果：保存了多少网页、入库/更新多少记录、哪些记录可导出、生成了哪些文件
- 需要用户处理什么：待复核、待补映射、映射冲突、字段缺失、下载/解析/导出失败
- 下一步在哪里做：映射页、待复核页、记录页、重试任务、打开导出文件、打开诊断日志

`download_exit_code`、`persisted_count`、`list_unaccounted`、`detail_unaccounted`、内部 stage 名、Python 异常类型、原始参数 JSON 等可以作为 details/debug 字段存在，但不能作为用户主汇总。用户主汇总应使用业务语义标签，例如"已保存网页 30 个"、"新增/更新记录 28 条"、"待补映射 2 条"、"字段缺失阻断导出 3 条"、"生成 Excel 1 个"，而不是只显示机械指标或英文 key。

允许的收尾文案形态：

```text
INFO [job=abc phase=finished scope=listing/all/all] 已完成：扫描 12 个任务，保存网页 30 个，新增/更新记录 28 条，生成 Excel 1 个
WARNING [job=abc phase=finished scope=listing/all/all] 已完成但有待处理：新增/更新记录 28 条；待补映射 2 条、待复核 1 条，可在映射页和待复核页处理
WARNING [job=abc phase=finished scope=listing/all/all] 未生成导出文件：字段缺失阻断 3 条；可在记录页查看缺失字段后重处理
ERROR [job=abc phase=finished scope=listing/physical_asset/sse] 未完成：上交所 / 实物资产详情下载失败，已保存 2/30；可重试任务，诊断见日志文件
INFO [job=abc phase=finished scope=listing/pre_disclosure/cquae] 无符合条件结果：重庆产权 / 预披露在 2026-05-27..2026-05-31 未发现项目
```

#### 6.5.6 推荐文案形态

下面是同一类信息的规范形态；具体实现可以调整字段名，但语义必须完整。

```text
INFO [job=abc phase=prepare_tasks scope=listing/all/all] 扫描任务：全部交易所 / 全部业务，日期 2026-05-27..2026-05-31，最多 10 页，并发 4
INFO [job=abc phase=prepare_tasks scope=listing/capital_increase/cquae] 任务 2/12：重庆产权 / 增资扩股，列表扫描完成，候选 0 条，原因：该任务仅需列表扫描
WARNING [job=abc phase=prepare_tasks scope=listing/pre_disclosure/cquae] 重庆产权 / 预披露未发现列表项：当前筛选无匹配或页面结构变化；诊断见 details.url
INFO [job=abc phase=save_pages scope=listing/physical_asset/sse] 上交所 / 实物资产详情下载：2/30，已保存 2，异常 0，预计剩余 5分52秒
ERROR [job=abc phase=exporting scope=listing/all/all] 导出失败：存在字段缺失阻断，未生成 Excel；failure_code=field_missing_blocked_records
```

对应地，下面这些形态不得作为操作员可见主日志：

```text
Run args: {"auto_split": false, ...}
Start SSE download: type=实物资产 start_date=... max_pages=...
Detail progress: 2/30 saved=2 detail_date_skipped=0 errors=0 speed=4.77/min eta=...
```

#### 6.5.7 与 UI / API 的关系

UI 不直接解析 raw log 文件来判断任务状态；任务页、总览页和通知使用 `job_events` 与 job summary。raw log 文件只能作为"打开诊断日志"的证据入口。

任何终止失败、部分成功、字段缺失阻断、映射阻断或 source missing，都必须在 `/api/jobs/{job_id}/events` 和 job `result.summary` 中有足够信息，使用户不打开 raw log 也能理解失败阶段、影响范围和下一步动作。

raw log 与 job event 不要求逐行一一对应，但阶段开始、阶段结束、warning、error 和最终摘要必须可通过 `job_id + phase_code + scope` 对齐。若某个下载器暂时无法提供结构化事件，wrapper 必须补齐外层 scope 和阶段，不得把内部英文日志直接暴露为唯一进度。

任务列表和总览聚合不直接从日志文本推导；后台必须同步维护 job counts、job summary/progress metrics。总览进度只发布约定 metric keys，例如 `downloaded_count`、`persisted_count`、`exception_count`、`pending_mapping_count`、`pending_review_count`、`mapping_conflict_count`、`accepted_completed_count`、`skipped_count`、`failed_count`、`archive_pending_count`、`archive_completed_count`。

#### 6.5.8 特殊边界

日志不得记录认证凭据、cookie、完整含密 URL 或无关环境信息。外部网页、OCR、缓存、错误响应中的广告、引流、备用网址、邀请码、伪系统指令等内容视为不可信污染；除排查污染本身外，不得进入正常中文文案、错误建议、提交归档或测试快照。

维护脚本日志必须遵守 report-only 边界。report-only 脚本只能输出诊断、候选计划、证据路径和受控运维处理所需信息，不得用 warning/error 暗示脚本已经修复状态。任何会改变 DB、归档文件或 journal 的实现都必须记录 operation id、manifest 路径或 journal manifest、输入范围、变更计数和失败计数；当前发行版不提供通用的自动继续、回滚或隔离命令。

#### 6.5.9 验收标准

一次覆盖多个交易所和业务类型的执行，日志尾部必须能在一屏内看出当前 `job_id`、当前阶段、当前交易所/业务、当前计数、是否有 warning/error；不能被整块参数 JSON 或交错的下载器内部行淹没。

运行中出现 warning/error 后，任务页或总览必须能看到异常摘要；不打开 raw log 也能知道异常范围、影响数量和下一步动作。

任务结束后必须显示 terminal summary：完成状态、业务范围、用户关心的结果、待处理事项、产物或失败原因。只显示 exit code、内部计数、英文 key-value 或最后一条进度不合格。

每条 warning 都必须有作用域、原因、影响和诊断入口；每条 error 都必须有失败码、失败阶段、可恢复性或下一步动作。只含"failed"、异常字符串或 URL 的行不合格。

前端事件展示和 raw log 诊断必须使用同一组阶段码与计数语义。新增或改动后台日志时，测试至少覆盖：

- 后端 job event contract 白名单过滤 raw `run_args` / `args` / `request_payload` / `raw_args_json`，API view 不包含巨大参数哨兵值
- 前端 job event normalizer 不暴露 raw `payload`、`run_args`、`args` 或 `request_payload`
- progress / overview contract 只保留白名单 metrics 与 stage summary
- presenter 文案只由阶段、任务标签、白名单计数、warning/error code/message 渲染，不拼接 raw JSON
- 操作员可见事件能回链 `job_id`、阶段、scope 和关键计数；warning/error 包含稳定 code 与可读 message

## 7. PPE 规则调试

适用对象：需要直接调试 PPE 引擎的运维 / 数据治理 / 开发人员。日常业务操作走桌面产品；本节只覆盖 engine-level 调试。

### 7.1 运行边界

桌面产品里映射真相源只有一套：`mapping_entries` 表，由 `peap/streaming_store.py` 与 `desktop_backend/services/mapping_service.py` 驱动。

因此：

- PPE 运行时**不再消费** legacy transferor/group/type CSV 作为映射规则真相
- 迁移或批量转换旧映射表，只能由受控维护流程显式调用
  `desktop_backend.domain.legacy_mapping_import` / `legacy_mapping_export` helper；这两个
  helper 不属于当前桌面 API、前端页面或运行时真相源，产品不会自动读取或写回 CSV。
- PPE 当前保留的是"非映射 optional rules"的独立调试能力

### 7.2 默认配置与输出目录

推荐：`peap_postprocess/ppe_config/postprocess_external_template.json`。兼容 `postprocess.json` / `postprocess.yaml`。

`PEAP_DATA_ROOT` 决定数据根目录；未设置时与桌面运行时一致，使用 `~/Documents/PEAP/data`。默认模板下输出：

- 处理输出：`<PEAP_DATA_ROOT>/outputs/postprocess/`
- 审计工作簿：`<PEAP_DATA_ROOT>/outputs/postprocess_audit/audit_<run_id>.xlsx`
- 日志目录：`<PEAP_DATA_ROOT>/logs/postprocess/`

### 7.3 当前 shipped optional rules

只启用四条非映射规则：

- `R010_filter_scrap_physical_asset`：筛掉报废/处置类实物资产
- `R012_clear_invalid_group_placeholder`：清空占位/垃圾值集团字段
- `R011_person_transferor_private`：自然人转让方归一到 private ownership 语义
- `R006_derive_listing_times`：从项目编号后缀推导/归一挂牌次数

### 7.4 标准调试流程

```bash
# 先 plan 看审计
uv run python -m peap_postprocess.postprocess_engine.cli run --mode plan

# 调整规则参数后再 plan，确认可接受后 apply
uv run python -m peap_postprocess.postprocess_engine.cli run --mode apply
```

CLI 默认会在 run 结束后导出 unresolved list；只想跑规则本体时加 `--skip-unresolved-list`。

### 7.5 输入选择优先级

配置三个开关：

1. `input_targets`（最高）：非空时只处理这里列的文件 / 模式
2. `scan_recursive`：仅 `input_targets` 为空时生效；`true` 递归扫描 `input_dir`
3. `include_globs`：仅 `input_targets` 为空时生效；默认 `["*.xlsx", "*.xls", "*.csv"]`

shipped template 通过 `input_targets` 固定 4 个根文件——不是 `scan_recursive` 失效，是被覆盖了。

### 7.6 规则配置结构

```json
"R010_filter_scrap_physical_asset": {
  "enabled": true,
  "priority": 5,
  "params": {
    "active": true,
    "severity": "info",
    "search_all_fields": true
  }
}
```

- `enabled`：总开关
- `priority`：执行优先级，越小越早
- `params`：规则私有参数

### 7.7 读审计结果

`<PEAP_DATA_ROOT>/outputs/postprocess_audit/audit_<run_id>.xlsx` 重点工作表：

1. `summary`
2. `changes`
3. `findings_all`
4. `conflicts` / `no_match` / `ambiguous` / `errors`

控制台 + `summary_json` 给同一轮 run 的结构化摘要——调试不要只看一处。

### 7.8 PPE 命令速查

```bash
uv run python -m peap_postprocess.postprocess_engine.cli run --mode plan
uv run python -m peap_postprocess.postprocess_engine.cli run --mode apply
uv run python -m peap_postprocess.postprocess_engine.cli run \
  --config peap_postprocess/ppe_config/postprocess_external_template.json --mode plan
```

## 8. 维护脚本

`scripts/` 下的清理与恢复工具。通用 cleanup/recovery 脚本只提供 consistency check 和证据报告；历史业务误分类使用独立的 revision-locked 修复器。路径默认由应用 workspace contract 解析，CLI 显式路径参数永远最优先。当前发行版不提供通用自动恢复、回滚或隔离 CLI。

| 脚本 | 用途 |
|---|---|
| `cleanup_archive_conflicts.py` | report-only：识别 `__conflictN` 后缀归档快照，输出受控运维处置所需证据 |
| `cleanup_duplicate_source_records.py` | report-only：识别指向其他 record 归档文件的 stale DB 记录 |
| `cleanup_missing_source_records.py` | report-only：识别 source / archive 文件都不存在的记录 |
| `cleanup_sse_bad_snapshots.py` | report-only：识别上交所 SPA 坏 shell 快照与候选记录 |
| `recover_missing_archive_files.py` | report-only：为归档文件缺失的 active 记录生成受控运维候选计划 |
| `refetch_sse_from_cleanup_manifest.py` | report-only：按 cleanup/quarantine manifest 生成 SSE 记录受控运维候选计划（per-run，需 `--manifest` 和当前 DB） |
| `repair_business_classifications.py` | 默认 report-only；显式 `--apply` 后按 revision/hash 证据修复选定范围，持有数据库进程锁，逐记录原子提交并写 operation journal |

调用范式：cleanup/report 脚本只输出诊断报告，不提供 `--apply`，也不直接删除 DB 行、移动快照或建立恢复副本。`repair_business_classifications.py` 是唯一例外：不带 `--apply` 时只读生成计划；带 `--apply` 时重新锁定记录 revision、payload hash、证据文件 hash 和目标记录身份，保留 sticky export cursor，并将 operation id 输出到结果。`peap data-health` 仅用于检查 schema 和 operation journal 健康度。系统不提供用户侧副本管理功能或外置数据保护策略。
