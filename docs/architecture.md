# 架构

PEAP 是面向产权交易网页的本地自动化处理平台：下载证据 → 解析 → 后处理 → canonical 入库 → 浏览/筛选/映射处置 → 导出归档。本文描述系统结构、数据流与边界。

## 1. 仓库布局

```
.
├── frontend/             桌面前端（Vite，仅消费 backend 已发布的 contract）
├── desktop_backend/      本地 HTTP adapter、service slice、resource/action contract
├── peap/                 下载 / 导入 / 后处理编排 / canonical 化 / store / 导出
├── peap_core/            共享 contract、catalog、状态机、跨层不可变语义
├── peap_parsers/         页面解析与 fixture
├── peap_postprocess/     PPE 引擎、规则、默认配置、CLI
├── docs/                 正式文档树（活跃文件由 release-gate.md 注册）
├── scripts/              发布、门禁、提交归档、维护脚本
├── tests/                contract / regression / smoke / architecture 约束
└── pyproject.toml uv.lock
```

`docs/` 根目录只允许放索引或本文这一类首层 doc——不允许重新引入 `PLAN.md` / `SPEC.md` / `todo.md` 这类过程性文件。运行时数据（工作区、缓存、归档、导出）不进仓库，详见 `storage.md`。

## 2. 系统层次

```
frontend  →  desktop_backend  →  peap*  →  catalog / runtime binding
                                     ↓
                              SQLite + 文件系统工作区
```

每层只承担一类真相：

- **catalog 层**只发布 source / family / business 身份与能力事实
- **runtime binding 层**只把 source / family / business 绑到可执行下载能力
- **ingest / store 层**只负责 canonical 数据链与持久化
- **backend contract 层**只发布前端可消费资源
- **frontend** 只消费 backend contract，不反向推断业务真相

把业务常量表复制进 panel / fixture / presenter / action helper / 脚本会直接破坏这条边界。

## 3. Catalog / Profile / Runtime Binding

family-aware 注册链：

- `peap_core/source_catalog.py`：source identity 与 source-backed family 能力
- `peap_core/family_catalog.py`：family identity、`family.source_ids`、默认 product profile
- `peap_core/business_catalog.py`：business identity、别名、family membership
- `peap/product_profile.py`：operator-facing profile、默认 postprocess/export/readiness 绑定
- `peap/business_runtime.py`：source / family / business 到 downloader 的可执行绑定
- `peap/download_tasks.py`：task registry 组装

`/api/catalog` 是 frontend 获取 family / business / source 选项的唯一运行时入口。`visible_families` 只能来自启用 source 的 `supported_record_families`，并且必须与 family catalog、business catalog 交叉后发布；runtime binding 决定 one-click 等 surface 是否可执行，不单独让 metadata-only family 变得可见。catalog metadata 写了某 family 但无真实 source 支撑时，不得出现在 `visible_families`、`support_matrix`、`surface_source_matrix` 或可操作选项里。业务展示 label 的 truth source 是 business catalog，经 `/api/catalog` 下发到前端；frontend/backend 本地常量只能作为 legacy fallback。

公共资源网是“一键执行”和“历史区间”中的独立成交归档阶段，但不是第 8 个交易所，也不进入 source/family catalog 或交易所 runtime binding。该阶段复用任务日期区间和进度，保存搜索与详情证据、MHTML、清单及独立 Excel；结果不静默覆盖交易所成交记录，也不生成现代 ingest 的 `ready` 记录。

产品级运行入口只有桌面 backend 的 streaming 链路；公开 `peap.cli` 仅提供 `data-health` 与 `repair-failures` 管理命令。`peap.parser_runner` / `peap.pipeline` 是历史 parser-to-workbook 兼容 helper，不能由公开 CLI 或产品编排调用，也不构成产品数据流。

当前产品能力的唯一计数口径是已实现的 `(record_family, source_id, business_id)` runtime binding；矩阵共有 **32** 条，分别为 22 条 `listing` 与 10 条 `deal`。下表是该口径的完整枚举；任何 catalog、downloader、parser 或 export 变更都必须保持它与 `peap/business_runtime.py` 一致。

来源范围与成交范围是两个不同维度：`listing` 当前覆盖 7 个来源（SSE、CBEX、TPRE、CQUAE、山东、广东、深圳）；`deal` 当前覆盖其中 4 个（SSE、CBEX、TPRE、CQUAE）。山东、广东、深圳的成交绑定尚未实现，因此不会出现在成交 catalog、下载任务或导出能力中；这不是将 7 个挂牌来源缩减为 4 个。

| record_family | source_id | business_id |
| --- | --- | --- |
| listing | cbex | physical_asset |
| listing | cbex | equity_transfer |
| listing | cbex | capital_increase |
| listing | cbex | pre_disclosure |
| listing | cquae | physical_asset |
| listing | cquae | equity_transfer |
| listing | cquae | capital_increase |
| listing | cquae | pre_disclosure |
| listing | guangdong | equity_transfer |
| listing | guangdong | capital_increase |
| listing | shandong | equity_transfer |
| listing | shandong | capital_increase |
| listing | shenzhen | equity_transfer |
| listing | shenzhen | capital_increase |
| listing | sse | physical_asset |
| listing | sse | equity_transfer |
| listing | sse | capital_increase |
| listing | sse | pre_disclosure |
| listing | tpre | physical_asset |
| listing | tpre | equity_transfer |
| listing | tpre | capital_increase |
| listing | tpre | pre_disclosure |
| deal | cbex | deal_physical_asset |
| deal | cbex | deal_equity_transfer |
| deal | cbex | deal_capital_increase |
| deal | cquae | deal_equity_transfer |
| deal | cquae | deal_capital_increase |
| deal | sse | deal_physical_asset |
| deal | sse | deal_equity_transfer |
| deal | sse | deal_capital_increase |
| deal | tpre | deal_equity_transfer |
| deal | tpre | deal_capital_increase |

## 4. Frontend 边界

- transport / adapter：`frontend/api.js`、`frontend/src/contracts/*.js`
- presenter：`frontend/src/presenters/*.mjs`
- panel / action consumer：`frontend/src/panels/*.js`、`frontend/src/actions/*.js`
- shared default-scope readiness：`frontend/src/state/defaultScopeRuntime.js`

边界规则：

- 依赖默认动作范围的 action consumer 必须读 backend-owned shared actionable default scope truth
- `records browse runtime` 是独立 read model，因此 records 页可以公开 `listing/all/all` 这样的 browse truth
- one-click / 历史区间 / 总览导出 helper 不能在 actionable scope 缺失时引入 silent fallback；记录页导出只能消费显式 records scope
- `business_re_evaluation` 只保留 hidden/internal legacy compatibility 的 distinct job metrics / copy，不属于活跃 mappings UI、review 页面或 CTA 边界
- family / business 选项只能来自 normalized catalog resource

## 5. Backend 边界

- controller：`desktop_backend/app_backend.py`（HTTP 入口）
- request / response contract：`desktop_backend/request_contract.py`、`http_contract.py`、各 `*_contract.py`
- service slice：`desktop_backend/services/*.py`
- repository：`desktop_backend/repositories/pipeline_repository.py`
- workspace / runtime env：`desktop_backend/app_config.py`

`AppService` 仍是编排最重的边界——统一 request scope、组装 row display payload、协调 job lifecycle，把持久化委托 `StreamingStore`，把 ingest / refresh 委托 `StreamingIngestRunner`，把导出委托 `run_ready_export()`。

scope / default 真相通过代码与 `api.md` 发布：

- records / export routing scope：`record_family + business_id + exchange`
- settings/basic 默认范围 public view 由 backend 发布
- one-click 只接受可执行 scope 与显式覆盖，不接受 server-owned settings truth
- mappings 页面只发布 `mapping_gap_resolution`、`mapping_conflict_resolution`、`audit` sections；待人工复核问题由 `/api/review-problems` 只读投影解释
- mappings 页面的撤销能力由 `GET /api/mappings` 的 `undo` 状态发布，只允许撤销当前 backend startup session 内最近一次规则变更；前端将该会话标识原样提交到 `POST /api/mappings/undo`

listing-facing legacy display label `project_type` 仍可能出现在 ingest / store / export 兼容层，但不是当前 request routing truth；公开 contract 以 `business_id` 为准。

## 6. Canonical 数据流

```
parse → postprocess → canonical_record → canonical_projection → store → API/export
```

| Stage | 写入 | 不写入 | Next consumer |
|---|---|---|---|
| Parse | `parser_payload` | canonical / export cache / latest-row state | postprocess / refresh / audit |
| Postprocess | `postprocess_payload`、findings | authoritative flat export payload | state classifier / canonical builder |
| Canonical 组装 | `canonical_record` | raw-payload merge fallback | store / export projection / API row builder |
| Projection | `canonical_projection` | new business truth | store cache / API-export formatting |
| Store | `records`、`record_revisions`、`mapping_entries`、`mapping_pending`、export cursor、jobs / audit | hidden schema-level truth 在存储 JSON / latest-row 列之外 | app-service / export / maintenance / refresh |
| API / export | `/api/records` rows、ready-export artifacts | raw payload passthrough | frontend / workbook |

不变量：

- `canonical_record` 是 authoritative business truth；影响 export / records display / state / 下游判断的字段必须在这里稳定存在
- `canonical_projection` 是 derived cache（store 会从 canonical 重算），projection-only truth 会被测试拒绝
- `parser_payload` / `postprocess_payload` 只承担证据、诊断与 refresh input
- `source_identity_json` 是重处理与身份锚定证据，不是业务内容
- `records.latest_revision_id` 总指向当前公开的 revision

## 7. Identity / 命名空间

- `StreamingIngestRunner` 构造 candidate record；持久化后 `business_key` 与 `record_id` 由 `StreamingStore` 决定
- success row：`business_key` 优先 `project_code.upper()`；无项目编号时回退 `source:<sha1(source_file)>`
- failed row：独立命名空间 `failed:{identity_anchor}`
- 入库后 latest-row 的 `source_file` 改写成归档路径；原路径保留在 `source_identity_json["original_source_file"]`
- `revision_hash` 基于 `postprocess_payload` 计算；store 实际新增 revision 时还会比较 latest revision 的 parser/postprocess/canonical/projection/source_file 序列化内容，只有这些内容都未变化时才原位刷新 findings/state

## 8. 状态机与三条刷新路径

主线 steady states（统一定义在 `peap_core/record_state_policy.py`）：

- `ready`、`pending_review`、`pending_mapping`、`mapping_conflict`、`conflict`、`parse_failed`、`postprocess_failed`、`skipped`

backlog ownership：

- `pending_mapping` 才拥有 `mapping_pending` backlog 行
- `mapping_conflict` 是独立裁决路径，不与普通 gap backlog 混写
- `pending_review` 属于 review-problem projection，不归 `mapping_pending` 或 mappings 页面 backlog 接管
- `conflict` 是另一类导出阻断，不等于 mapping conflict

三条**容易混淆**的刷新路径：

| 路径 | 入口 | 目标 | 适用对象 |
|---|---|---|---|
| `mapping_refresh` | `POST /api/mappings/reprocess-pending` | 已有业务身份、但有 mapping gap / ambiguity 的记录重跑后处理 | `pending_mapping` / `mapping_conflict`，不扫描 `pending_review` |
| `business_re_evaluation` | `POST /api/mappings/re-evaluate-business` | hidden/internal legacy compatibility；不属于活跃 mappings UI | `pending_review` |
| `refresh_postprocess` | ingest/store 内部，无独立 API | 复用 `parser_payload`，只重跑 postprocess + canonical assembly | 规则变了但不需重 parse |

完整重跑（重选证据文件 → 重 parse → 重新 postprocess）是 `reprocess_record()`：`POST /api/records/{record_id}/reprocess`，不属于上面三条。

选择修复动作：

- parser 错了 → `reprocess_record`
- parser 没错，规则或映射变了 → `mapping_refresh` 或内部 `refresh_postprocess`
- 待人工复核原因需要解释 → `/api/review-problems` 与“待复核”页只读查看
- 旧记录没对齐当前语义 → 先看 maintenance 是否触发

## 9. Maintenance 是 live semantics 的一部分

`run_streaming_store_maintenance()` 不是一次性迁移脚本，会在服务启动 / 导出前 / 部分 mutation 路径前运行：

- 规范旧状态
- 修正 `listing_date`
- 让旧记录对齐当前 mapping-review/backlog 语义
- 双向协调 `mapping_pending`

读路径不强制每个请求做 maintenance——可能短暂观察到 maintenance 未触发的旧行。排障时先分清是写链问题、maintenance 未触发、还是 API contract 问题。

## 10. 扩展顺序

新增 family / business / source 时按以下顺序，避免把同一业务语义拆散到多层各改一半：

1. 收敛 metadata：`peap_core/source_catalog.py`、`peap_core/family_catalog.py`、`peap_core/business_catalog.py`
2. 接 runtime binding：`peap/business_runtime.py`，再让 `peap/download_tasks.py` 消费
3. 落 parser / canonical / postprocess / store / export 语义
4. 发布 backend contract：`desktop_backend/request_contract.py`、`record_scope.py`、各 `*_contract.py`
5. 接 frontend：只消费 `/api/catalog` 与 backend 已发布 contract

详细规范见 `extending.md`。

## 11. 当前仍需收缩的耦合面

技术债（可存在，但不应再扩散新旁路真相源）：

- `frontend/app.js` 仍承担 overview / settings 组装，应继续拆薄
- `desktop_backend/app_service.py` 仍保留较重的生命周期协调面，需向 service slice 下沉
- `business_re_evaluation` 必须维持独立语义，不回退成 generic mapping-refresh
- actionable default consumer 必须 fail-closed；允许 `records browse runtime` 暴露 `listing/all/all`，但不允许 one-click / export / helper 在 scope 缺失时静默发明这个 scope
- 一些基础设施函数仍直接依赖 `StreamingStore`，应继续减少跨层直连

## 12. 模块归属（Module Responsibilities）

| Module | Owns | Explicitly does not own |
|---|---|---|
| `peap/streaming_ingest.py` | parse 调用、postprocess 调用、source identity、`canonical_record` 组装、record-state classification | 查询分页、导出 artifact 写盘、长期查询语义 |
| `peap/streaming_store.py` | SQLite schema、latest rows、revisions、mapping entries、mapping backlog、cursor、maintenance normalization | parser 执行、workbook 生成 |
| `peap/export_projection.py` | canonical-to-flat 唯一投影边界 | storage 与 request handling |
| `peap/streaming_export.py` | ready-record 选择、`requested_export_mode` full/incremental 路由、稳定 `cursor_id` 与 cursor watermark 更新、保留 artifact 可打开下载，retention tombstone 明确不可打开且不可重建 | raw parse/postprocess 归一化 |
| `desktop_backend/app_service.py` | request normalization、user-facing summary、job orchestration、mutation routing | canonical 字段 derivation 细则、低层 revision persistence |

## 13. 术语表

| 术语 | 含义 |
|---|---|
| `source_id` | 交易场所或站点身份（`sse`、`cbex`、`tpre`、`cquae`...） |
| `record_family` | 记录族（`listing` / `deal`） |
| `business_id` | family-aware canonical 业务标识；当前 listing 族含 `equity_transfer`、`physical_asset`、`capital_increase`、`pre_disclosure`，deal 族含 `deal_equity_transfer`、`deal_physical_asset`、`deal_capital_increase` |
| `project_type` | listing 族历史兼容显示标签，不是当前 request routing truth |
| `parser_payload` | parser 产出 / `refresh_postprocess` 复用的源面证据快照 |
| `postprocess_payload` | postprocess + payload normalization 之后的源面输出，仍非业务 contract |
| `canonical_record` | 由 `streaming_ingest.py` 组装的 authoritative business truth；`canonical_fields + business_identity + export_extras + diagnostics` |
| `canonical_projection` | 从 `canonical_record` 派生的平面输出缓存（derived cache） |
| `business_identity` | family-aware 业务身份核（`business_id` + 业务标签 + 必要 identity） |
| `export_extras` | 输出 contract 字段，但不适合进 `canonical_fields` 最小核 |
| `records` | SQLite latest-row 表 |
| `record_revisions` | revision 表（payload snapshot、findings、canonical data、projection、revision-local state） |
| `revision_hash` | 基于 `postprocess_payload` 的内容 hash；它是 revision 变化判断的一部分，不是唯一条件 |
| `business_key` | `records` 的稳定主键；优先 `project_code.upper()`，否则 `source:<sha1>` |
| `identity_anchor` | failure 命名空间锚点（`failed:{anchor}`） |
| `shared actionable default scope` | settings/basic 暴露的 backend-owned 默认动作范围；one-click / 历史区间 / 总览导出依赖它 |
| `records browse runtime` | records 页独立 read model，可公开 `listing/all/all`；不等价于 actionable default scope |
| `mapping_refresh` | `pending_mapping` 重跑后处理（`POST /api/mappings/reprocess-pending`） |
| `business_re_evaluation` | `pending_review` hidden/internal legacy compatibility（`POST /api/mappings/re-evaluate-business`） |
| `refresh_postprocess` | ingest 内部接口：复用 `parser_payload` 重跑 postprocess + canonical |
| `reprocess_record` | 公开操作：重选证据文件、走完整 ingest 链 |
