# 桌面主链语义契约重构任务卡包 Implementation Plan

## 中央监督执行状态（2026-03-25）

- Status: 已完成 `Task 0`、`Task 1`、`Task 2`、`Task 3`、`Task 4`、`Task 5`、`Task 6`。
- Supervisor: 主控工作区 `/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform`
- Worktree assignments:
  - `Task 1` -> `/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/task-1-shared-contracts` on `codex-task-1-shared-contracts`
  - `Task 2` -> `/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/task-2-store-contracts` on `codex-task-2-store-contracts`
  - `Task 3` -> `/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/task-3-service-contracts` on `codex-task-3-service-contracts`
  - `Task 4` -> `/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/task-4-http-contracts` on `codex-task-4-http-contracts`
  - `Task 5` -> `/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/task-5-frontend-contracts` on `codex-task-5-frontend-contracts`
- Red lines frozen:
  - 不保留旧兼容语义
  - 不新增页面级 workaround
  - 不把挂牌写死成顶层类别
  - 不在失败对象上继续用路径重算身份
- Dispatch order frozen: `Task 1 -> Task 2 -> Task 3 -> Task 4 -> Task 5 -> Task 6`
- Boundary policy: 任一 worker 越权改文件、私保兼容层、跳过测试、擅改契约字段名，直接打回。
- Verified checkpoints:
  - `Task 1`: `62c3fa0 feat: add shared desktop semantic contracts`
  - `Task 1 review fix`: `cd8ac09 fix: tighten shared contract edge cases`
  - `Task 2`: `3a44648 feat: stabilize store identity and export contracts`
  - `Task 2 review fix`: `ec458a5 fix: close task2 contract review gaps`
  - `Task 3`: `61be917 feat: align desktop service with semantic contracts`
  - `Task 4`: `5a2f1c2 feat: normalize desktop http contracts`
  - `Task 5`: `35d2494 feat: modularize desktop renderer semantic contracts`
  - `Task 5 review fix`: `79bad66 fix: restore task5 semantic contracts`
  - `Task 1` verification: `python3 -m unittest tests.test_source_registry tests.test_record_scope tests.test_progress_contract tests.test_record_identity tests.test_http_contract -v`
  - `Task 2` verification: `python3 -m unittest tests.test_source_registry tests.test_record_scope tests.test_progress_contract tests.test_record_identity tests.test_http_contract tests.test_streaming_store tests.test_streaming_export -v`
  - `Task 3` verification: `python3 -m unittest tests.test_app_service -v`
  - `Task 4` verification: `python3 -m unittest tests.test_app_backend -v`
  - `Task 5` verification: `node --test desktop_app/renderer/records.test.js desktop_app/renderer/exports.test.js desktop_app/renderer/tasks.test.js`
  - `Task 5 layout regression`: `node --test desktop_app/layout_contract.test.js`
  - `Task 5 merge proof`: `git merge --ff-only codex-task-5-frontend-contracts`
  - `Task 6` verification: `python3 -m unittest tests.test_source_registry tests.test_record_scope tests.test_progress_contract tests.test_record_identity tests.test_http_contract tests.test_streaming_store tests.test_streaming_export tests.test_app_service tests.test_app_backend -v`
  - `Task 6` frontend verification: `node --test desktop_app/layout_contract.test.js desktop_app/renderer/records.test.js desktop_app/renderer/exports.test.js desktop_app/renderer/tasks.test.js`

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把桌面主链从“围绕挂牌记录打补丁”的实现形态，重构为围绕显式语义契约运转的实现形态，并把这项工作拆成可以安全分发给低智能 agent 的任务卡。

**Architecture:** 本轮采用“契约先行、一次性切换、listing 先交付”的方式。先冻结四类稳定契约: `record_family` 共享骨架、任务语义契约、范围契约、对象身份契约、HTTP fallback/cap 契约，再按文件所有权把实现拆成低冲突卡片。未来扩展更多站点和 `deal` 记录族时，必须沿同一契约扩展，不允许继续往 `app_service.py` / `renderer.js` 打 if/else 补丁。

**Tech Stack:** Python, unittest, SQLite, vanilla JS modules, Node test runner, desktop HTTP handler, Markdown

---

## 使用说明

这不是给单个高智能 agent 的“自由发挥型计划”，而是一包可以直接分发给多个低智能 agent 的任务卡。每张卡都必须只在自己被授权的文件集合内工作，超出文件边界就立刻停下并回报中央监督者，不允许“顺手一起改”。

本轮只交付 `listing` 的用户可见能力，但系统内部现在就要显式引入 `record_family`。唯一允许的前端默认值是 `listing`。本轮不做 `deal` 页面、不接 `public_resource_deals`，但任何新代码都不得把“挂牌记录”硬编码成系统唯一顶层类别。

## 全体 worker 共享约束

- 只修改自己卡片列出的文件。未列出的文件一律只读。
- 不保留旧语义兼容层，除非卡片明确要求。允许净化接口，禁止“双轨语义并存”。
- 测试先写，至少先补一条会失败的最小回归，再写实现。
- 不把多个不变量揉成一个测试。一个测试只锁一个业务语义裂缝。
- 不把 `project_type` 当成顶层记录类别。顶层类别是 `record_family`，`project_type` 只是 `listing` 家族内部细分。
- 不把 `archive_path` 当身份锚点。失败对象的身份必须稳定且可追溯。
- 不引入“临时魔法默认值”。任何会改变业务语义的 fallback，都必须进入契约模块和测试。
- 不在 [`desktop_backend/app_service.py`](/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/desktop_backend/app_service.py) 或 [`desktop_app/renderer.js`](/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/desktop_app/renderer.js) 继续堆积未抽象的业务分支。

## 中央监督的固定分发模板

每次只给 worker 发三类内容，不要把整仓库上下文一股脑塞过去:

1. 该 worker 对应的任务卡全文。
2. 该卡允许修改的文件当前内容。
3. 该卡依赖的上游卡实际产出结果。

## worker 回包模板

每个 worker 回来时，必须按下面的顺序汇报:

1. 修改文件列表。
2. 新增或调整的公共契约。
3. 实际运行过的命令。
4. 每条命令的结果是 PASS / FAIL。
5. 尚未解决的阻塞或风险。

## 依赖与并行规则

- `Task 0` 完成前，不分发任何实现卡。
- `Task 1` 完成前，不允许动 store / service / frontend / HTTP 的正式实现。
- `Task 2` 与 `Task 3` 可以串行执行，建议先 `Task 2` 再 `Task 3`，因为 service 依赖 store 的身份与事件语义。
- `Task 4` 依赖 `Task 3`，因为 HTTP 契约应当直接暴露已经净化过的 service 契约。
- `Task 5` 依赖 `Task 1`、`Task 3`、`Task 4`。如果中央监督者能控制合并顺序，也允许在 `Task 3` 进入稳定状态后提前开工纯函数测试，但不要先做最终 wiring。
- `Task 6` 只能在 `Task 2`、`Task 3`、`Task 4`、`Task 5` 全部合并后执行。

## 冻结的契约形状

以下字段名在本轮不允许自由发明同义词。

### `RecordFamily`

允许值只限:

```python
Literal["listing", "deal"]
```

本轮用户可见路径只能使用 `"listing"`。

### `RecordScope`

标准字段固定为:

```python
{
  "record_family": "listing" | "deal",
  "state": str,
  "project_type": str,
  "keyword": str,
  "date_from": str,
  "date_to": str,
  "page": int,
  "page_size": int,
}
```

### `ProgressView`

标准字段固定为:

```python
{
  "job_id": str,
  "job_type": str,
  "record_family": "listing" | "deal",
  "job_status": str,
  "phase_code": str,
  "phase_label": str,
  "is_terminal": bool,
  "current_item_label": str,
  "current_index": int,
  "current_total": int,
  "metrics": [{"key": str, "label": str, "value": int | str}],
  "latest_stage_code": str,
  "latest_stage_label": str,
  "latest_stage_summary": str,
}
```

### 空导出响应

标准字段至少包含:

```python
{
  "status": "empty",
  "empty_reason_code": str,
  "scope_state_counts": dict,
  "scope": dict,
}
```

### 任务事件接口响应

标准字段固定为:

```python
{
  "events": list,
  "returned_count": int,
  "total_count": int,
  "truncated": bool,
}
```

### HTTP fallback / cap 响应

not-found 响应固定为:

```python
{
  "error": "not_found",
  "resource": str,
  "resource_id": str,
}
```

事件上限与截断语义固定由纯契约函数生成，至少包含:

```python
DEFAULT_JOB_EVENT_LIMIT = 200

{
  "events": list,
  "returned_count": int,
  "total_count": int,
  "truncated": bool,
}
```

约束:
- `returned_count == len(events)`
- `truncated == (total_count > returned_count)`
- not-found 不得退化成 `200 + []`

---

### Task 0: 中央监督卡 A - 冻结合同、文件边界与分发顺序

**Owner:** 中央监督者，仅你自己执行，不分发

**Files:**
- Modify: `docs/superpowers/plans/2026-03-25-desktop-mainline-refactor-task-cards.md`
- Read-only: `docs/superpowers/plans/2026-03-25-semantic-invariant-screening.md`
- Read-only: `docs/real_operation_test_report_2026-03-23.md`

**Do not touch:**
- 任何产品代码文件

- [ ] **Step 1: 把本任务卡包作为唯一分发底稿**

只允许从这份文件复制卡片给其他 agent，不要口头转述，不要二次简化。

- [ ] **Step 2: 给每张卡标注唯一负责人和唯一工作树**

建议分支前缀: `codex/task-<id>-<slug>`。同一时刻不允许两个 worker 改同一文件。

- [ ] **Step 3: 冻结四条红线**

红线是: 不保留旧兼容语义、不新增页面级 workaround、不把挂牌写死成顶层类别、不在失败对象上继续用路径重算身份。

- [ ] **Step 4: 只按依赖顺序分发**

`Task 1 -> Task 2 -> Task 3 -> Task 4 -> Task 5 -> Task 6`。如果你要并行，必须先确认写集不冲突。

- [ ] **Step 5: 对每个 worker 的回包做边界审查**

只要发现越权改文件、私自保留兼容层、跳过测试、私改契约字段名，就直接打回。

**Acceptance for supervisor:**
- 能明确说出每张卡的允许写集和依赖。
- 没有任何一张卡把 `app_service.py` 或 `renderer.js` 的改动和别的卡重叠。

---

### Task 1: 共享契约骨架卡

**Owner intent:** 只负责建立共享词汇表和纯契约模块，不接业务 wiring。

**Files:**
- Modify: `peap/streaming_models.py`
- Create: `peap/source_registry.py`
- Create: `desktop_backend/record_scope.py`
- Create: `desktop_backend/progress_contract.py`
- Create: `desktop_backend/record_identity.py`
- Create: `desktop_backend/http_contract.py`
- Create: `tests/test_source_registry.py`
- Create: `tests/test_record_scope.py`
- Create: `tests/test_progress_contract.py`
- Create: `tests/test_record_identity.py`
- Create: `tests/test_http_contract.py`

**Do not touch:**
- `peap/streaming_store.py`
- `desktop_backend/app_service.py`
- `desktop_backend/app_backend.py`
- `desktop_app/renderer.js`
- 任何现有 report / todo 文档

- [ ] **Step 1: 先写纯契约测试**

至少覆盖以下断言:

```python
from peap.streaming_models import RecordFamily

def test_record_family_literal_allows_listing_and_deal_only(): ...
def test_record_scope_defaults_to_listing_all_and_pagination_defaults(): ...
def test_progress_contract_clears_running_context_on_terminal_states(): ...
def test_record_identity_marks_failed_states_and_prefers_original_evidence(): ...
def test_source_registry_rejects_unknown_source_lookup(): ...
def test_http_contract_builds_not_found_and_event_envelope_without_implicit_fallbacks(): ...
```

- [ ] **Step 2: 在 `peap/streaming_models.py` 中引入 `RecordFamily`**

最少把 `record_family` 纳入下列契约对象:

```python
ItemProgressEvent
IngestedRecord
ExportRequest
```

要求:
- 默认值允许缺省到 `"listing"`，但字段必须显式存在。
- 不新增“挂牌专用”顶层类型别名。

- [ ] **Step 3: 创建 `desktop_backend/record_scope.py`**

该文件只做纯 scope 归一化，不访问 store/service。至少提供:

```python
RecordScope  # dataclass
normalize_record_scope(payload: dict | None) -> RecordScope
record_scope_to_dict(scope: RecordScope) -> dict
resolve_listing_business_types(scope: RecordScope) -> list[str]
```

硬要求:
- 默认 `record_family="listing"`
- 默认 `project_type="all"`
- 统一处理 `page/page_size`
- 不在这里塞导出副作用逻辑

- [ ] **Step 4: 创建 `desktop_backend/progress_contract.py`**

该文件只做纯任务视图投影，不访问数据库。至少提供:

```python
TERMINAL_JOB_STATUSES
is_terminal_job_status(status: str) -> bool
sanitize_terminal_progress(raw_progress: dict) -> dict
build_progress_view(*, job: dict | None, raw_progress: dict, summary: dict | None = None) -> dict
```

硬要求:
- 终态必须清空 `current_item_label/current_index/current_total`
- `metrics` 必须是列表，不允许继续输出通用 `archive_*_count` 顶层字段给新视图

- [ ] **Step 5: 创建 `desktop_backend/record_identity.py`**

该文件只做身份锚点与原始证据定位的纯逻辑。至少提供:

```python
FAILED_RECORD_STATES
is_failed_record_state(state: str) -> bool
build_source_identity_payload(
    *,
    record_family: str,
    source_file: str,
    source_url: str = "",
    project_code: str = "",
    project_name: str = "",
    exchange: str = "",
    listing_date: str = "",
    candidate_tokens: list[str] | None = None,
) -> dict
build_identity_anchor(*, record_state: str, source_identity: dict) -> str
pick_reprocess_evidence_path(record: dict) -> str
```

硬要求:
- 对失败对象的身份锚点不能依赖“当前 `source_file` 路径”
- `pick_reprocess_evidence_path()` 必须优先使用原始证据路径
- `build_source_identity_payload()` 的返回结构至少包含:

```python
{
  "record_family": str,
  "original_source_file": str,
  "source_url": str,
  "project_code": str,
  "project_name": str,
  "exchange": str,
  "listing_date": str,
  "candidate_tokens": list[str],
}
```

- [ ] **Step 6: 创建 `peap/source_registry.py`**

只做注册表，不做下载执行。至少提供:

```python
SourceCapability  # dataclass
register_source(capability: SourceCapability) -> None
get_source(source_id: str) -> SourceCapability
list_sources(record_family: str | None = None) -> list[SourceCapability]
```

硬要求:
- `SourceCapability` 字段固定为:

```python
{
  "source_id": str,
  "site_label": str,
  "supported_record_families": tuple[str, ...],
  "supported_job_types": tuple[str, ...],
  "downloader_key": str,
  "adapter_key": str,
  "enabled": bool,
}
```

- 当前 source 一律声明 `supported_record_families=("listing",)`
- 不为 `deal` 伪造实现，只保留可扩展骨架
- `list_sources(record_family=None)` 返回全部 enabled source
- `list_sources(record_family="listing")` 返回所有支持 listing 的 enabled source
- `list_sources(record_family="deal")` 本轮允许返回空列表，但语义必须正确

- [ ] **Step 7: 创建 `desktop_backend/http_contract.py`**

该文件只做 HTTP fallback / cap 纯契约，不访问 handler/service。至少提供:

```python
DEFAULT_JOB_EVENT_LIMIT = 200
normalize_job_event_limit(raw_value: object) -> int
build_job_events_envelope(events: list[dict], *, total_count: int) -> dict
build_not_found_payload(*, resource: str, resource_id: str = "") -> dict
```

硬要求:
- `normalize_job_event_limit()` 不得产生 `100/200` 双上限语义
- `build_job_events_envelope()` 必须显式给出 `returned_count/total_count/truncated`
- `build_not_found_payload()` 必须与 HTTP 层最终输出完全一致

- [ ] **Step 8: 跑纯契约测试**

Run: `python3 -m unittest tests.test_source_registry tests.test_record_scope tests.test_progress_contract tests.test_record_identity tests.test_http_contract -v`

Expected: 全绿，且没有 import 现有 service/store 的副作用失败。

- [ ] **Step 9: 提交**

```bash
git add peap/streaming_models.py peap/source_registry.py desktop_backend/record_scope.py desktop_backend/progress_contract.py desktop_backend/record_identity.py desktop_backend/http_contract.py tests/test_source_registry.py tests/test_record_scope.py tests/test_progress_contract.py tests/test_record_identity.py tests/test_http_contract.py
git commit -m "feat: add shared desktop semantic contracts"
```

**Definition of done:**
- 新模块都是纯逻辑模块。
- 共享字段名已经冻结，后续卡片不需要再发明新命名。

---

### Task 2: Store 契约卡 - 身份锚点、事件语义、导出底座

**Owner intent:** 只负责 `StreamingStore` 和导出底座，不改 service / HTTP / frontend。

**Files:**
- Modify: `peap/streaming_store.py`
- Modify: `peap/streaming_export.py`
- Modify: `tests/test_streaming_store.py`
- Modify: `tests/test_streaming_export.py`

**Depends on:**
- `Task 1`

**Do not touch:**
- `desktop_backend/app_service.py`
- `desktop_backend/app_backend.py`
- `desktop_app/renderer.js`
- `tests/test_app_service.py`
- `tests/test_app_backend.py`

- [ ] **Step 1: 先在 store 层补失败对象身份测试**

至少覆盖以下场景:

```python
def test_failed_record_identity_anchor_does_not_change_when_source_file_changes(): ...
def test_reimport_same_failed_source_reuses_same_record_and_adds_revision(): ...
def test_failed_record_candidate_tokens_remain_visible_after_source_file_update(): ...
def test_list_job_events_raises_key_error_for_missing_job(): ...
def test_job_event_count_can_report_total_count_separately_from_returned_rows(): ...
```

- [ ] **Step 2: 用增量 schema 迁移，不要破坏现有表**

在 [`peap/streaming_store.py`](/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/peap/streaming_store.py) 的 schema 初始化路径中补齐 `records` 表新增列:

```sql
record_family TEXT NOT NULL DEFAULT 'listing'
identity_anchor TEXT NOT NULL DEFAULT ''
source_identity_json TEXT NOT NULL DEFAULT '{}'
```

硬要求:
- 不允许 drop/recreate `records`
- 不允许新建第二张 records 影子表
- 必须兼容已有数据库文件

- [ ] **Step 3: 调整失败对象建档与更新规则**

具体要求:
- `upsert_failed_record()` 首次写入时生成不可变 `identity_anchor`
- `business_key` 对失败对象不得直接由当前 `source_file` 重算
- `update_record_source_file()` 不得改写失败对象的 `identity_anchor`
- 失败对象的 `source_identity_json` 必须保留原始证据路径和可用 token

- [ ] **Step 4: 统一 job events not-found 与计数能力**

具体要求:
- `get_job(job_id)` 缺失时继续抛 `KeyError`
- `list_job_events(job_id)` 缺失时改为同样抛 `KeyError`
- 为 service/HTTP 准备“返回总事件数”的能力，优先复用现有 `get_job_event_counts()`
- 不在 store 层做静默 `100/200` 截断分叉

- [ ] **Step 5: 更新导出底座以接受显式 `record_family`**

在 [`peap/streaming_export.py`](/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/peap/streaming_export.py) 中:
- 接受 `ExportRequest.record_family`
- 本轮仅支持 `listing`
- 如果收到非 `listing`，显式抛出 `ValueError`
- 导出过滤继续通过业务类型列表工作，但不把业务类型误当顶层家族

- [ ] **Step 6: 运行 store / export 测试**

Run: `python3 -m unittest tests.test_streaming_store tests.test_streaming_export -v`

Expected: 失败对象身份、重复导入、missing-job 语义、export request 合同全部被锁住。

- [ ] **Step 7: 提交**

```bash
git add peap/streaming_store.py peap/streaming_export.py tests/test_streaming_store.py tests/test_streaming_export.py
git commit -m "feat: stabilize store identity and export contracts"
```

**Definition of done:**
- 失败对象拥有稳定身份锚点。
- `list_job_events()` 与 `get_job()` 的 not-found 语义一致。
- store 没有引入破坏性迁移。

---

### Task 3: Service 契约卡 - `RecordScope`、`ProgressView`、失败对象恢复路径

**Owner intent:** 只负责 `AppService` 与 service 级回归，不改 store schema、不改 HTTP handler、不改 frontend。

**Files:**
- Modify: `desktop_backend/app_service.py`
- Modify: `tests/test_app_service.py`

**Depends on:**
- `Task 1`
- `Task 2`

**Do not touch:**
- `peap/streaming_store.py`
- `desktop_backend/app_backend.py`
- `desktop_app/renderer.js`
- `tests/test_app_backend.py`
- `tests/test_streaming_store.py`

- [ ] **Step 1: 先写 service 级最小失败回归**

至少覆盖以下场景:

```python
def test_launch_one_click_requires_real_job_id_before_success_return(): ...
def test_manual_import_all_failed_resolves_to_failed_not_success_with_warnings(): ...
def test_mapping_refresh_zero_actual_repairs_resolves_to_failed(): ...
def test_terminal_progress_clears_current_item_context(): ...
def test_export_progress_uses_export_semantics_not_archive_semantics(): ...
def test_list_records_and_run_export_share_same_scope_contract(): ...
def test_list_records_summary_splits_filtered_counts_and_page_counts(): ...
def test_reprocess_failed_record_uses_original_evidence_path(): ...
def test_overview_and_list_records_do_not_rewrite_failed_record_identity(): ...
```

- [ ] **Step 2: 在 service 层接入 `RecordScope`**

具体要求:
- `list_records(payload)` 入口立即走 `normalize_record_scope()`
- `run_export(payload)` 只接受显式 `payload["scope"]`
- 不再保留“只传日期”的导出语义分支
- 默认 scope 必须是 `record_family=listing`、`project_type=all`

- [ ] **Step 3: 重做 `list_records()` 的 summary 结构**

返回结构必须同时包含:

```python
summary["filtered_state_counts"]
summary["page_state_counts"]
summary["total_count"]
summary["visible_count"]
summary["page"]
summary["page_size"]
summary["page_count"]
```

硬要求:
- 前者表示当前过滤结果全集
- 后者表示当前页
- 不允许继续用单个 `state_counts` 混两层含义

- [ ] **Step 4: 用 `progress_contract.py` 统一 `latest_progress`**

具体要求:
- `_build_latest_progress()` 只负责收集原始素材，再交给 `build_progress_view()`
- `launch_one_click()` / `_launch_streaming_job()` 在拿不到 `job_id` 时不得返回 accepted success
- `manual_import` / `mapping_refresh` 的成功判定改成“至少一条进入可接受完成态”
- `interrupted` / `failed` / `completed_with_warnings` 必须是干净终态

- [ ] **Step 5: 修复失败对象的读路径与恢复路径**

具体要求:
- `_repair_missing_archives_once()` 不得再匿名重写 `parse_failed/postprocess_failed` 的身份基础
- `reprocess_record()` 对失败对象必须使用 `record_identity.pick_reprocess_evidence_path()`
- 如果原始证据缺失，返回显式错误，不静默切到匿名 archive 副本

- [ ] **Step 6: 结构化空导出响应**

空导出时返回至少包含:

```python
{
  "status": "empty",
  "empty_reason_code": ...,
  "scope_state_counts": ...,
  "scope": ...,
}
```

硬要求:
- blocker 计数必须来自同一 `RecordScope`
- 不允许继续返回一条无法回推 scope 的自由文本当唯一证据

- [ ] **Step 7: 运行 service 测试**

Run: `python3 -m unittest tests.test_app_service -v`

Expected: 任务终态、scope 对齐、失败对象恢复路径、空导出结构化解释全部过关。

- [ ] **Step 8: 提交**

```bash
git add desktop_backend/app_service.py tests/test_app_service.py
git commit -m "feat: align desktop service with semantic contracts"
```

**Definition of done:**
- `AppService` 已经把 shared contracts 用起来。
- 旧的“假成功”“混合 summary”“失败对象读路径改写”都不再存在。

---

### Task 4: HTTP 契约卡 - 纯 dispatcher、404 统一、events envelope

**Owner intent:** 只负责 HTTP handler 合同和无 socket 测试，不改 service/store/frontend。

**Files:**
- Modify: `desktop_backend/app_backend.py`
- Modify: `tests/test_app_backend.py`

**Depends on:**
- `Task 3`

**Do not touch:**
- `desktop_backend/app_service.py`
- `peap/streaming_store.py`
- `desktop_app/renderer.js`

- [ ] **Step 1: 先补 HTTP 合同测试，不再用真实 loopback 绑定**

测试必须直接覆盖:

```python
def test_get_job_returns_summary_without_inline_events(): ...
def test_get_job_events_returns_events_envelope_with_counts_and_truncated_flag(): ...
def test_missing_job_and_missing_job_events_both_return_404(): ...
def test_records_endpoint_parses_record_family_scope_fields(): ...
def test_exports_endpoint_requires_scope_payload(): ...
```

- [ ] **Step 2: 把 handler 路由抽成纯 dispatcher**

在 [`desktop_backend/app_backend.py`](/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/desktop_backend/app_backend.py) 中新增一个可直接测试的纯入口，例如:

```python
dispatch_api_request(service, *, method: str, path: str, query: dict[str, list[str]], headers: dict[str, str], payload: dict[str, object], api_token: str = "") -> tuple[int, dict]
```

要求:
- `build_handler()` 只做 HTTP I/O 包装
- 路由判断和状态码决策全部复用这个 dispatcher
- `not_found` payload 和 events envelope 必须直接复用 [`desktop_backend/http_contract.py`](/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/desktop_backend/http_contract.py)
- 不要再让测试通过 `ThreadingHTTPServer` 走真实 socket

- [ ] **Step 3: 统一 `/api/jobs/:id` 与 `/api/jobs/:id/events` 语义**

具体要求:
- `/api/jobs/:id` 只返回 job summary
- `/api/jobs/:id/events` 返回固定 envelope
- 缺任务时两者都返回 404
- 不保留 `events` 内联回 `get_job()`

- [ ] **Step 4: 对 records / exports 路由做契约对齐**

具体要求:
- `/api/records` 解析 `record_family`
- `/api/exports` 传递显式 `scope`
- 由 service 层返回的结构化空导出响应必须原样透出

- [ ] **Step 5: 运行 HTTP 测试**

Run: `python3 -m unittest tests.test_app_backend -v`

Expected: 全绿，且不再依赖 `127.0.0.1` bind。

- [ ] **Step 6: 提交**

```bash
git add desktop_backend/app_backend.py tests/test_app_backend.py
git commit -m "feat: normalize desktop http contracts"
```

**Definition of done:**
- HTTP 测试不依赖 socket。
- 404 语义、events envelope、scope payload 均已固定。

---

### Task 5: Frontend 契约卡 - `tasks.mjs`、`exports.mjs`、`records.mjs`、`renderer.js`

**Owner intent:** 只负责前端纯函数抽离和 wiring，不改 backend/store。

**Execution status (2026-03-25):** 已完成。实现 worker: `task5_implementer`；中央监督者完成两轮复核后以 `git merge --ff-only codex-task-5-frontend-contracts` 合入。证明命令：`node --test desktop_app/renderer/records.test.js desktop_app/renderer/exports.test.js desktop_app/renderer/tasks.test.js`、`node --test desktop_app/layout_contract.test.js`。

**Files:**
- Modify: `desktop_app/index.html`
- Modify: `desktop_app/renderer.js`
- Modify: `desktop_app/renderer/records.mjs`
- Modify: `desktop_app/renderer/records.test.js`
- Create: `desktop_app/renderer/exports.mjs`
- Create: `desktop_app/renderer/exports.test.js`
- Create: `desktop_app/renderer/tasks.mjs`
- Create: `desktop_app/renderer/tasks.test.js`

**Depends on:**
- `Task 1`
- `Task 3`
- `Task 4`

**Do not touch:**
- `desktop_backend/app_service.py`
- `desktop_backend/app_backend.py`
- `peap/streaming_store.py`

- [x] **Step 1: 先写 Node 侧纯函数回归**

至少覆盖以下断言:

```javascript
test("buildRecordsQuery defaults to listing + all", ...)
test("formatRecordsSummary prefers filtered_state_counts over page_state_counts for overview copy", ...)
test("buildExportRequestFromView carries current scope instead of date-only payload", ...)
test("formatEmptyExportMessage uses empty_reason_code and scope_state_counts", ...)
test("progressPreset treats interrupted as terminal", ...)
test("eventTitle prefers terminal status semantics over stage semantics", ...)
test("manual_import and mapping_refresh progress copy do not mention archive counts", ...)
```

- [x] **Step 2: 抽出 `records.mjs` 的 scope 纯逻辑**

具体要求:
- 默认 `projectType` 改为 `"all"`
- 默认 query 包含 `record_family=listing`
- `formatRecordsSummary()` 改为读取 `filtered_state_counts`
- 保留 `page_state_counts` 仅作本页辅助，不作为主 summary 文案来源

- [x] **Step 3: 新建 `exports.mjs`**

至少提供:

```javascript
buildExportRequestFromView(viewState)
formatEmptyExportMessage(result)
```

硬要求:
- 导出请求必须发送 `scope`
- `scope` 必须包含 `record_family/state/project_type/keyword/date_from/date_to/page/page_size`
- 空导出提示必须由 `empty_reason_code + scope_state_counts` 生成

- [x] **Step 4: 新建 `tasks.mjs`**

至少提供:

```javascript
progressPreset(progressView)
formatProgressMeta(progressView, latestJob, overview)
formatProgressHint(progressView, latestJob, overview)
formatJobTitle(job)
formatJobMeta(job)
formatEventTitle(event)
```

硬要求:
- `interrupted` 必须视为终态
- 任务标题和事件标题优先使用终态语义，不允许 `stage="failed"` 覆盖 `status="interrupted"`
- `manual_import` / `mapping_refresh` / `export_excel` 各自使用自己的语义文案，不共享“归档中”假通用文案

- [x] **Step 5: 在 `renderer.js` 中只保留 wiring**

具体要求:
- 进度条 copy、任务 copy、导出请求构造、空导出解释都改为调用新模块
- `renderer.js` 不再自己判断 `interrupted` / `failed` / `archive_pending` 的最终 copy
- 当前 UI 仍只展示 `listing`，但状态中必须显式携带 `record_family`

- [x] **Step 6: 调整首屏默认筛选**

在 [`desktop_app/index.html`](/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/desktop_app/index.html) 和前端默认状态里，把记录页业务类型默认值改成“全部”，不要默认锁到股权转让。

- [x] **Step 7: 运行 Node 测试**

Run: `node --test desktop_app/renderer/records.test.js desktop_app/renderer/exports.test.js desktop_app/renderer/tasks.test.js`

Expected: 通过，并且所有语义判断都在纯模块层被锁住。

- [x] **Step 8: 提交**

```bash
git add desktop_app/index.html desktop_app/renderer.js desktop_app/renderer/records.mjs desktop_app/renderer/records.test.js desktop_app/renderer/exports.mjs desktop_app/renderer/exports.test.js desktop_app/renderer/tasks.mjs desktop_app/renderer/tasks.test.js
git commit -m "feat: modularize desktop renderer semantic contracts"
```

**Definition of done:**
- `renderer.js` 的任务是 wiring，不再是语义垃圾场。
- 前端 records / export / task 语义都被纯模块和 Node 测试覆盖。

---

### Task 6: 中央监督卡 B - 集成验收、报告收口、剩余 blocker 归档

**Owner:** 中央监督者，仅你自己执行，不分发

**Execution status (2026-03-25):** 已完成。执行者：中央监督者。证明命令：`python3 -m unittest tests.test_source_registry tests.test_record_scope tests.test_progress_contract tests.test_record_identity tests.test_http_contract tests.test_streaming_store tests.test_streaming_export tests.test_app_service tests.test_app_backend -v`、`node --test desktop_app/layout_contract.test.js desktop_app/renderer/records.test.js desktop_app/renderer/exports.test.js desktop_app/renderer/tasks.test.js`。

**Files:**
- Modify: `docs/real_operation_test_report_2026-03-23.md`
- Modify: `todo.md`
- Modify: `docs/superpowers/plans/2026-03-25-desktop-mainline-refactor-task-cards.md`

**Depends on:**
- `Task 2`
- `Task 3`
- `Task 4`
- `Task 5`

**Do not touch:**
- 非必要不要再改产品代码；只有在合并冲突修正时才允许最小修补

- [x] **Step 1: 审核每张卡是否真的守住写集**

如果某个 worker 越权改了别的卡文件，先打回，不要直接手工兜底合并。

- [x] **Step 2: 按依赖顺序合并并做全量验证**

Run: `python3 -m unittest tests.test_source_registry tests.test_record_scope tests.test_progress_contract tests.test_record_identity tests.test_http_contract tests.test_streaming_store tests.test_streaming_export tests.test_app_service tests.test_app_backend -v`

Run: `node --test desktop_app/layout_contract.test.js desktop_app/renderer/records.test.js desktop_app/renderer/exports.test.js desktop_app/renderer/tasks.test.js`

Expected: 全绿。如果失败，先定位是契约冲突、实现 bug，还是测试假设过期，不要糊里糊涂回滚。

- [x] **Step 3: 更新真实报告，不写“设计更优雅了”这种无效结论**

在 [`docs/real_operation_test_report_2026-03-23.md`](/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/docs/real_operation_test_report_2026-03-23.md) 中只记录:
- 哪些旧 finding 真关闭了
- 关闭依据是什么测试
- 如果还有 blocker，它属于任务语义、范围语义、对象身份，还是 fallback/cap

- [x] **Step 4: 收紧 `todo.md`**

只保留真正阻断发布信任的项。删掉已经被契约回归覆盖的页面级噪音。

- [x] **Step 5: 回写本卡包状态**

在本文件顶部或每张卡下方标记:
- 是否完成
- 谁完成
- 哪条命令证明完成

**Definition of done:**
- 真正形成“中央监督 + 分卡执行 + 契约回归”的工作方式。
- 文档里剩余 blocker 已被压缩到可管理的少数条目。

---

## 给中央监督者的验收口径

如果某张卡交回来后只是“代码变了”，但没有满足下面任意一条，就不能算完成:

- 新契约字段名固定且被测试覆盖。
- 旧的语义裂缝有明确最小回归能证明它被封死。
- 文件边界没有被突破。
- 新实现没有把未来 `deal` / 多站点扩展写死。

## 不允许出现的常见错误

- 以“兼容旧代码”为名，同时保留旧 `latest_progress` 形状和新 `ProgressView` 形状。
- 以“方便”为名，让 `run_export()` 同时吃 `scope` 和裸日期自由组合。
- 以“修复丢档”为名，继续在读路径下重写失败对象的 `source_file` / `business_key`。
- 以“统一文案”为名，把 `manual_import`、`mapping_refresh`、`export_excel` 都投影成同一种“归档中”提示。
- 以“测试方便”为名，继续用真实 `ThreadingHTTPServer` 绑定 loopback。

## 推荐分发顺序

1. 先发 `Task 1`，并由中央监督者人工审契约字段名。
2. 再发 `Task 2`，因为 store 身份语义是一切失败对象修复的底座。
3. 再发 `Task 3`，让 service 彻底吃进 shared contracts。
4. 再发 `Task 4`，用纯 dispatcher 固化 HTTP 契约。
5. 最后发 `Task 5`，因为 frontend 应该消费稳定后的 service / HTTP 结构。
6. 全部回收后，由你执行 `Task 6`。

## 交付后如何判断这次拆分是否成功

成功标准不是“代码看起来更模块化”，而是下面三件事同时成立:

1. 新 worker 只看自己卡片就能开始干活，不需要读半个仓库。
2. 任意一张卡失败，都能明确知道是哪个契约层失守，而不是又回到页面症状堆里。
3. 未来加站点或加 `deal` 时，首先扩的是 `RecordFamily + source_registry + scope/progress/identity`，而不是再去改页面分支。
