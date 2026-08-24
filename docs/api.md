# API 契约

PEAP HTTP API 的唯一人类可读契约镜像。代码真相源：

- `desktop_backend/request_contract.py`
- `desktop_backend/settings_contract.py`
- `desktop_backend/runtime_contract.py`
- `desktop_backend/http_contract.py`
- `desktop_backend/error_codes.py`

## 1. Transport Envelope

成功响应：

```json
{
  "ok": true,
  "data": {},
  "meta": {}
}
```

- 顶层只允许 `ok` / `data` / 可选 `meta`
- adapter / page 不得回退到 raw payload

失败响应：

```json
{
  "ok": false,
  "error": {
    "code": "invalid_request",
    "message": "Request is not allowed in current business context.",
    "details": {}
  }
}
```

- 顶层只允许 `ok` 与 `error`
- `error` 只允许 `code` / `message` / 可选 `details`
- 扁平 `message`、`error_code`、根级 `details` 不属于公开 contract

## 2. Active Routes

| Method | Route | Meaning |
|---|---|---|
| `GET` | `/api/ready` | readiness |
| `GET` | `/api/health` | health |
| `GET` | `/api/catalog` | visible families, support matrix, default scope |
| `GET` | `/api/overview` | overview resource |
| `GET` | `/api/overview/stream` | server-sent overview + job event stream |
| `GET` | `/api/jobs?limit=N` | job list |
| `GET` | `/api/jobs/{job_id}` | job detail |
| `GET` | `/api/jobs/{job_id}/events?limit=N` | job events |
| `GET` | `/api/records?...` | records list |
| `GET` | `/api/review-problems?...` | read-only review-problem projection |
| `GET` | `/api/mappings` | mappings backlog resource |
| `GET` | `/api/settings/basic` | basic settings |
| `GET` | `/api/settings/advanced` | advanced settings |
| `GET` | `/api/runtime/dependencies` | runtime dependencies |
| `POST` | `/api/jobs/one-click` | launch one-click job |
| `POST` | `/api/jobs/download-ingest` | launch historical download ingest |
| `POST` | `/api/jobs/manual-import` | launch manual import |
| `POST` | `/api/jobs/archive-reprocess` | reprocess archived records |
| `POST` | `/api/jobs/{job_id}/retry` | retry a retryable job from its stored request payload |
| `POST` | `/api/exports` | launch export |
| `GET` | `/api/exports/history?limit=N` | export history list |
| `GET` | `/api/exports/history/{export_id}` | export history detail |
| `POST` | `/api/exports/history/{export_id}/open` | open retained export artifact |
| `POST` | `/api/exports/history/{export_id}/download` | copy retained export artifact to output dir |
| `POST` | `/api/mappings` | save mapping rule |
| `PUT` | `/api/mappings/{entry_id}` | update mapping rule |
| `DELETE` | `/api/mappings/{entry_id}` | delete mapping rule |
| `POST` | `/api/mappings/preview` | preview mapping impact |
| `POST` | `/api/mappings/resolve-conflict` | resolve mapping conflict |
| `POST` | `/api/mappings/reprocess-pending` | mapping-refresh queue |
| `POST` | `/api/mappings/re-evaluate-business` | hidden/internal legacy `business_re_evaluation` compatibility queue |
| `POST` | `/api/mappings/undo` | undo latest same-startup mapping mutation |
| `POST` | `/api/system/select-path` | local chooser |
| `POST` | `/api/system/open-path` | open local path |
| `POST` | `/api/records/{record_id}/reprocess` | reprocess record |
| `POST` | `/api/records/{record_id}/reveal-folder` | reveal record artifact |
| `POST` | `/api/records/{record_id}/field-missing/acknowledge` | persist field_missing noise acknowledgement |
| `POST` | `/api/settings/basic` | save basic settings |
| `POST` | `/api/settings/advanced` | save advanced settings |
| `POST` | `/api/runtime/install-browser` | install browser runtime |

`/api/ready` 与 `/api/health` 区别：`/api/health` 只检查 backend 进程存活（极轻量）；`/api/ready` 检查依赖（DB、catalog、settings）已加载完成可服务请求。

## 3. Shared Scope And Default Truth

### 3.1 Records / Export scope

records query 与 export request 共享同一套 canonical scope 字段：

- `record_family`
- `business_id`
- `exchange`
- `state`
- `keyword`
- `date_from`
- `date_to`
- `page`
- `page_size`

`project_type` 不属于公开 request contract；维护适配层、历史回放或旧快照仍出现它，那是 maintenance-only historical residue。

语义边界：

- `records browse runtime` 是 records 页自己的 canonical browse read-model，不等价于 shared actionable default scope
- shared actionable default scope 缺失或 stale 时，`records browse runtime` 仍可对外公开 `listing/all/all` 这样的广域浏览 truth
- `POST /api/exports` 必须接收一份显式 canonical scope；记录页导出通常传当前 browse scope，总览页导出通常传当前 actionable default scope
- 只有当调用方显式提交的 export scope 本身就是 `listing/all/all` 时，导出 `all/all` 才合法
- export helper / adapter / panel 不得在 scope 缺失时自行合成 `listing/all/all`、`listing/all` 或其他 silent fallback

### 3.2 One-click request

`POST /api/jobs/one-click` 的可执行请求体必须携带 actionable scope：

- `record_family`
- `business_id`
- `exchange`

可选覆盖字段：

- `start_date`、`end_date`、`max_pages`、`concurrency`、`postprocess_config`、`no_resume`、`save_json`、`verbose`
- `include_public_resource`：严格布尔值；桌面“一键执行”和“历史区间”固定提交 `true`，公共资源网使用同一日期区间并在同一个 job 中报告进度和结果

backend-owned settings truth，禁止作为 one-click request body 输入：

- `effective_default_scope`
- `stored_preference`

### 3.3 Manual-import request

`POST /api/jobs/manual-import` 的 canonical request body：

- `input_dir`

可选显式 scope 字段：

- `record_family` / `business_id` / `business_label` / `exchange`

约束：

- `record_family` / `business_id` / `exchange` 只在 request body 显式提供时参与路由
- 显式 scope 启用时，`record_family` 与 `business_id` 必须成对出现；`business_label` / `exchange` 不能脱离这对路由真相单独提交
- 省略显式 scope 时，backend 不得从 `effective_default_scope` / `stored_preference` / catalog default scope 静默继承业务归属
- `business_label` 只作显式 `business_id` 的补充标签，不能单独替代业务路由真相
- 未知业务导入必须保留 unknown truth，进入 `pending_review` / `business_re_evaluation` 语义，不被默认范围重写成具体业务

### 3.4 Settings/basic public resource

`GET /api/settings/basic` 返回：

- `effective_default_scope`
- `stored_preference`
- `stale_default_metadata`
- `default_exchange`
- `default_concurrency`
- `paths.workspace_root`
- `paths.archive_root`
- `paths.export_root`

`POST /api/settings/basic` 只接受：

- `stored_preference`
- `default_exchange`
- `default_concurrency`
- `paths`

语义分工：

- `stored_preference` 是共享 actionable default scope 的持久化来源
- `default_exchange` 是 scalar default，用于 exchange picker / runtime fallback，不等价于共享 scope patch
- 只提交 `default_exchange` 时，不得静默改写 `stored_preference.exchange`，也不得连带改写 `effective_default_scope.exchange`
- 显式提交 `stored_preference: {}` 表示清空 shared actionable default scope；这与"省略 `stored_preference`"的保留当前值语义不同
- `effective_default_scope` / `stored_preference` 只发布 shared actionable default truth；records 页是否可浏览由独立 `records browse runtime` 决定

server-owned public view，不接受回写：

- `effective_default_scope`
- `stale_default_metadata`

## 4. Catalog Resource

`GET /api/catalog` 返回：

- `active_profile`
- `visible_families`
- `sources`
- `support_matrix`
- `surface_source_matrix`
- `source_business_requirements`
- `default_scope`
- `visibility`

`default_scope` 是 family-aware、backend-owned 的 catalog mirror；活跃 consumer 不得在 adapter / modal / panel / export helper 里自行合成 `listing/all`。

`visible_families` 只发布 source-backed family：backend 先读 `peap_core/source_catalog.py` 中启用 source 的 `supported_record_families`，再与 `peap_core/family_catalog.py` / `peap_core/business_catalog.py` 组装业务选项。当前 `deal` family 已有 source/runtime 支撑，前端可见合同为“成交业务”，包含股权转让成交、实物资产成交、增资扩股成交。未来若某 family 只注册 metadata 但无 source 支撑，不得出现在 `visible_families`、`support_matrix`、`surface_source_matrix` 或前端可选项中。业务展示 label 以 business catalog 通过 `/api/catalog` 发布的 label 为准；前端本地 label map 只作 legacy fallback，不得覆盖 catalog label。新增 family / business / source 的接入规范看 `extending.md`。

`source_business_requirements` 是 source/business 特殊执行边界的结构化投影，truth source 是 `peap_core/source_business_contract.py`。该字段只发布 `scope_policy` 与 `required_query_filters`，用于让 frontend、审计和测试知道某个 source/business 的实际采集范围，例如区域交易所央企/部委范围或实物资产最低金额过滤；不得由前端维护第二份规则表。

`active_profile.profile_id` 必须是 `peap/product_profile.py` 中已注册的真实 product profile（当前有效值为 `desktop_listing` 与 `desktop_deal`）。多 family 同时可见时，不得以 `desktop_multi_family` 这类未注册占位字符串作为 `profile_id` 返回；多 family 可见性通过 `visible_families` / `visibility` 字段表达，`active_profile` 只反映当前 operator context 的单一有效 profile。

## 5. Mappings Resource

`GET /api/mappings` 的 canonical top-level shape 是 `entries + sections + summary + undo`，外加容量字段：

```json
{
  "entries": [],
  "sections": [
    {
      "section_id": "mapping_gap_resolution",
      "title": "待映射补全",
      "count": 1,
      "cta_kind": "reprocess_pending",
      "items": []
    },
    {
      "section_id": "mapping_conflict_resolution",
      "title": "映射冲突",
      "count": 1,
      "cta_kind": "",
      "items": []
    },
    {
      "section_id": "audit",
      "title": "审计",
      "count": 0,
      "cta_kind": "read_only",
      "items": []
    }
  ],
  "summary": {
    "actionable_count": 2,
    "mapping_gap_count": 1,
    "mapping_conflict_count": 1,
    "audit_count": 0
  },
  "undo": {
    "available": true,
    "startup_session_id": "current-backend-startup-session",
    "operation_kind": "update"
  },
  "returned_count": 2,
  "total_count": 2,
  "truncated": false
}
```

说明：

- `sections` 是当前 backlog truth；活跃 consumer 不得依赖 legacy `pending`
- `summary` 是 backlog aggregate truth
- `entries` 是已保存映射规则
- `undo.available` 表示当前 backend startup session 是否存在可撤销的最近一次 mapping upsert / update / delete；`operation_kind` 是 `upsert`、`update`、`delete` 或空字符串
- `undo.startup_session_id` 是前端调用 `POST /api/mappings/undo` 时必须原样回传的当前启动会话标识；前端不得缓存后跨 backend 重启复用
- `business_resolution` 不属于 `GET /api/mappings` 页面 sections；`GET /api/mappings` 不发布 `business_resolution_count`，也不发布 `re_evaluate_business` CTA
- `POST /api/mappings/re-evaluate-business` 只作为 hidden/internal legacy compatibility 保留，不属于活跃 mappings UI，不出现在前端导航、mappings section CTA 或 review 页面
- `cta_kind = reprocess_pending` 必须走 `POST /api/mappings/reprocess-pending`
- mapping refresh 只扫描 `pending_mapping` 与 `mapping_conflict`，不得扫描 `pending_review`
- `POST /api/mappings/undo` 只撤销当前 backend startup session 内最新一次 mapping upsert / update / delete；request 必须提交同一次 `GET /api/mappings` 返回的 `undo.startup_session_id`，跨启动会话或 `undo.available = false` 时必须失败
- mappings 规则没有公开 bulk import/export HTTP route；活跃文档不得声明这类路由

## 5.1 Review Problems Resource

`GET /api/review-problems` 是 `pending_review` 与 `field_missing` 的只读解释投影，不是 mappings extension，也不提供修改、重跑或批量处理入口。请求参数：

```text
problem_kind=all|project_type_unresolved|business_family_unresolved|deal_data_incomplete|export_fields_missing|manual_review_unclassified
record_family=all|listing|deal
business_id=all|<business id>
exchange=all|<source id>
state=all|pending_review|field_missing
keyword=<project code/name/source file text>
date_from=YYYY-MM-DD
date_to=YYYY-MM-DD
page=<1-based integer>
page_size=<1..200>
```

响应保持 `summary + rows + returned_count + total_count + truncated`，summary 必须包含五类 problem kind 计数键：`project_type_unresolved_count`、`business_family_unresolved_count`、`deal_data_incomplete_count`、`export_fields_missing_count`、`manual_review_unclassified_count`。`date_from` / `date_to` 按 review problem 的 `updated_at` 日期过滤。`actions` 只为未来兼容保留，当前 UI 不渲染按钮或禁用的未来动作提示。`field_missing` 在本资源中以 `export_fields_missing` 只读显示；字段缺失确认仍由 records API 所有，确认只降低提示噪音，不会补字段，也不会允许导出。

## 6. Jobs / Progress / Overview

`business_re_evaluation` 是 hidden/internal legacy compatibility 的 distinct job type，不能降回 generic mapping-refresh copy；它不属于活跃 mappings UI、review 页面或公开操作入口。

`POST /api/jobs/{job_id}/retry` 只对保存了可重放 request payload 的 retryable job type 生效：`one_click`、`download_ingest`、`manual_import`、`archive_reprocess`，且原 job 必须处于 `failed`、`cancelled`、`canceled`、`aborted`、`interrupted` 或 `error` 终止状态。响应沿用 job launch view，并必须带 `retry_of_job_id`，使前端能把新任务与原任务建立可审计关联。

每个 `GET /api/jobs`、`GET /api/jobs/{job_id}` 及 `overview.latest_job` 的 job view 都包含 `actions.retry`。该布尔值由后端根据 job type、终止状态以及 metadata 中是否存在该类型所需的可重放请求字段计算：streaming job 必须保存日期、交易所和 family scope；manual/archive job 必须保存非空输入目录。客户端只能在其严格为 `true` 时显示 retry，缺失、未知或非布尔值必须 fail-closed，不能自行复制 retryable type/status/metadata 规则。

多业务族的一键或下载任务是一个父 job，顶层 `record_family`、`business_id` 保持为空，不伪造某个子任务的身份；其 `scope` 必须公开 `record_families` 与逐项的 `family_scopes`。携带具体子任务 scope 的事件以该 scope 发布 `record_family + business_id + exchange`，不从父 job 的空顶层身份推断。

`POST /api/records/{record_id}/reprocess` 返回 record action result：`record_id`、`state`、`project_code`、`archive_path`、`error_code`、`error_message`。HTTP 200 且 `error_code` 或 `error_message` 非空仍是业务失败，客户端不得按成功路径刷新或掩盖该错误。

公共 metric family：

- `pending_review_count`
- `accepted_completed_count`
- `skipped_count`
- `failed_count`

出现在：

- `job.progress.metrics`
- `job.result.metrics`
- `overview.latest_job.progress.metrics`
- `overview.latest_job.result.metrics`
- `overview.latest_progress.metrics`

`GET /api/overview` 的 `defaults` 当前至少暴露：

- `manual_import_input_dir`
- `default_scope.stored_preference`
- `default_scope.effective_scope`
- `default_scope.stale_resolution`

`defaults.manual_import_input_dir` 必须反映 `GET /api/settings/advanced` 当前 `ingest_paths.raw_manual_root` 的真实值，不是静态 config 初值。

## 6.1 Export History Resource

`GET /api/exports/history?limit=N` 返回最近导出历史，`GET /api/exports/history/{export_id}` 返回单个导出的详情。history row/detail 的 artifact 状态必须区分 retained artifact 与 retention tombstone：retained artifact 可以 `open` / `download`；tombstone 明确不可打开、不可下载、不可重建。

`POST /api/exports/history/{export_id}/open` 只打开当前仍存在的受管导出 artifact。`POST /api/exports/history/{export_id}/download` 接受可选 `output_dir`；未显式提供时 backend 使用当前 basic settings 的 `export_root`。这两个动作都不得为 tombstone 重新生成 workbook。

## 7. Records Resource

`GET /api/records` 返回：

- `scope`
- `rows`
- `display_columns`
- `total_count`
- `page_count`
- `has_more`
- `summary.filtered_state_counts`
- `summary.page_state_counts`
- `summary.total_count`
- `summary.visible_count`

`rows[*]` 顶层至少暴露：

- `record_id`
- `business_id`
- `business_label`
- `project_code`
- `project_name`
- `project_type_code`
- `project_type_label`
- `exchange_code`
- `exchange_label`
- `listing_date`
- `state`
- `field_missing_acknowledgement`
- `attention`
- `exportable`

listing-family 的 `project_type_code` / `project_type_label` 仍只是 display projection，不是 request routing truth，不能替代 `business_id`。

`field_missing_acknowledgement` 的唯一持久 owner 是 `records.acknowledged_payload_json.field_missing`。确认动作只降低 records/UI 的 attention/noise：它不得修改 `state`，不得把 `field_missing` 变成 `ready`，也不得让该记录进入 export eligibility。`exportable=false` 必须持续反映这一点。

records 页的初始可浏览 scope 可来自 `records browse runtime`，因此允许是 `listing/all/all` 这样的 browse truth；这不是可回写的 shared actionable default scope，也不能被 one-click / 总览导出 helper 拿来伪装成缺省动作路由。记录页导出若要使用 browse truth，必须把它作为显式 records/export scope 传递，不能冒充默认执行范围。

## 8. Error Codes

与 `desktop_backend/error_codes.py` 同步：

| Code | HTTP |
|---|---|
| `invalid_input` | `400` |
| `invalid_request` | `400` |
| `invalid_path_selection_kind` | `400` |
| `local_path_required` | `400` |
| `local_path_picker_failed` | `500` |
| `local_path_open_failed` | `400` |
| `not_found` | `404` |
| `record_artifact_not_found` | `404` |
| `record_artifact_open_failed` | `400` |
| `mutating_job_in_progress` | `409` |
| `browser_runtime_missing` | `409` |
| `manual_import_input_dir_not_found` | `400` |
| `unauthorized` | `401` |
| `internal_error` | `500` |
| `state_conflict` | `409` |
| `dependency_not_ready` | `503` |
| `schema_not_ready` | `503` |
| `product_error` | `500` |
