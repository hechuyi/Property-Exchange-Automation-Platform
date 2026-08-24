# 存储与工作区布局

PEAP 持久化分两层：**SQLite 业务数据库** + **文件系统工作区**（HTML 证据 / 归档 / 导出 / 缓存）。代码真相在 `peap/streaming_store.py`（schema）与 `desktop_backend/app_config.py`（path 解析）。

## 1. 工作区根目录

`AppConfig.from_env()` 按以下优先级解析 workspace root：

1. `PEAP_WORKSPACE_ROOT`
2. `PEAP_APP_HOME`
3. `PEAP_DOCUMENTS_HOME`
4. 平台默认 `~/Documents/PEAP`（macOS / Linux）或 `%USERPROFILE%\Documents\PEAP`（Windows）

## 2. 默认目录布局

```
<workspace_root>/
├── data/streaming_ingest.sqlite3   # 业务数据库
├── manual/                          # 手动导入默认目录
├── archive/                         # 自动归档根（也是 PEAP_AUTO_HTML_ROOT 默认）
├── exports/                         # 导出输出
├── logs/                            # 运行日志
└── cache/
    ├── download_chunks/             # 下载分片状态
    └── ms-playwright/               # Playwright 浏览器缓存
```

ba1c3cb 之前的旧布局曾用 `submission/`、`data/raw/manual/`，已被当前 `archive/` / `manual/` 取代。`PEAP_AUTO_HTML_ROOT` 仍可单独覆盖自动归档目录，但默认值就是 `archive_root`，不再依赖第二棵原始下载树。

## 3. AppConfig 的副作用与读路径

`AppConfig.from_env()` 默认调用：

- `ensure_directories()`：创建上述所有目录
- `migrate_legacy_layout()`：把旧布局数据 additive、non-destructive 搬运到当前布局

这两步是**桌面启动行为**，不是路径解析的固有动作。维护脚本（典型例子是 `scripts/_paths.py`）只想读取路径解析结果时，应传：

```python
AppConfig.from_env(ensure_dirs=False, migrate_legacy=False)
```

把「建目录、搬旧数据」的所有权留在桌面启动这一处，避免 cleanup 脚本意外建出整套工作区。

## 4. 环境变量覆盖

13 个 `PEAP_*` 覆盖项，按层级分类：

| 层级 | 变量 |
|---|---|
| Workspace 根 | `PEAP_WORKSPACE_ROOT` / `PEAP_APP_HOME` / `PEAP_DOCUMENTS_HOME` |
| 数据/缓存/日志 | `PEAP_DATA_ROOT` / `PEAP_CACHE_DIR` / `PEAP_LOG_DIR` |
| HTML 树 | `PEAP_MANUAL_HTML_ROOT` / `PEAP_AUTO_HTML_ROOT` / `PEAP_ARCHIVE_ROOT` / `PEAP_EXPORT_ROOT` |
| 实例文件 | `PEAP_STREAMING_DB_PATH` / `PEAP_DOWNLOAD_CHUNK_STATE_DIR` / `PEAP_PLAYWRIGHT_BROWSERS_PATH` |

产品设置页：basic settings 暴露 `workspace_root`、`archive_root`、`export_root`；advanced settings 暴露 `raw_manual_root`。其余覆盖项主要给调试/迁移使用，不应写进日常 SOP。

## 5. SQLite 表

| Table | 角色 |
|---|---|
| `records` | latest row per business key；当前筛选/展示列、当前状态、`latest_revision_id` |
| `record_revisions` | revision snapshots：payloads、findings、canonical data、projection、revision-local state |
| `mapping_entries` | 当前映射真相表 |
| `mapping_pending` | unresolved mapping backlog（按 record/revision 去重到最新项） |
| `rulepacks` | rulepack registry——schema 已建，主产品路径尚未消费 |
| `settings` | product settings latest truth |
| `settings_revisions` | settings append-only 历史快照 |
| `exports`, `export_cursor_records` | export runs + `requested_export_mode` full/incremental、稳定 `cursor_id`、cursor value watermark，以及保留 artifact 可打开 / retention tombstone 不可打开不可重建的状态 |
| `jobs`, `job_events` | 后台任务生命周期、计数器、item-level progress |
| `audit_log` | maintenance repair / manual action / runtime install 审计轨迹 |

## 6. records 表关键列

`records` 是 authoritative latest-row 表（不是壳表），列分四组：

- 行身份：`record_id`、`business_key`、`record_family`、`identity_anchor`
- 业务路由：`business_id`、`exchange`
- 兼容展示：project / business label、日期、价格、状态等
- legacy display label：`project_type`（listing 兼容列，非当前公开路由真相）
- 证据与文件：`source_identity_json`、`source_file`、`archive_path`
- 当前状态、错误元数据、`latest_revision_id`

`StreamingStore.iter_latest_records()` 直接依赖这些 top-level 列做 list/query，因此 `records` 是当前查询面的真实入口。

## 7. revision 行为：content-sensitive

revision 不是盲目 append-only，也不只看 `revision_hash`：

- `revision_hash` 是 ingest 侧基于 `postprocess_payload` 计算的内容 digest
- `revision_hash` 改变，或 latest revision 中的 `parser_payload` / `postprocess_payload` / `canonical_record` / `canonical_projection` / `source_file` 序列化内容与本次 ingest 不一致 → 新增 `record_revisions` 行，推进 `records.latest_revision_id`
- 只有 revision payload/canonical/source_file 未变化时，才原位刷新当前 latest revision 的 `findings` / `state`

`refresh_postprocess()`（ingest 内部接口）复用持久化 `parser_payload`，只重跑 postprocess 与 canonical assembly；是否新增 revision 仍由上述内容比较决定。`reprocess_record()`（app-service 公开操作）是真正的全链 ingest：重新选择证据文件，再走完整 parse/postprocess。

## 8. canonical_projection 是 derived

`StreamingStore.upsert_record()` 不信任传入的 projection——会从 `canonical_record` 重算 `canonical_projection_json`。projection 是 derived cache，不是独立真相源；与 canonical 冲突时以 canonical 为准。

## 9. failed records

`upsert_failed_record()` 用 failed identity namespace（`failed:{identity_anchor}`）创建/更新记录，把 failure metadata 写进 `records`，把 failure payload/findings 写进 `record_revisions`。即便失败，`source_identity_json` 仍重要——`reprocess_record()` 靠它定位原始证据。

## 10. export cursor 是 sticky 的

`export_cursor_records` 故意保持 sticky：某条记录变成 non-ready 时不会从 cursor 里默默消失。删除 / 撤回 / removal 候选语义要靠当前 ready-set 与旧 cursor 对比推导。

## 11. settings 与 revisions

`settings` 保存当前生效 latest；`settings_revisions` 保存每次 `set_setting()` 的历史快照。读当前走 `settings`，看变更证据走 `settings_revisions`。settings 侧无公开 revision 查询接口，revision 主要给审计 / 排障用。

## 12. maintenance normalization

`run_streaming_store_maintenance()` 是 live semantics 的一部分，不是归档清扫脚本。会在服务启动、导出前、部分 mutation 路径前运行：

- 规范旧状态
- 修正 `listing_date`
- 让旧记录对齐当前 mapping-review/backlog 语义
- 双向协调 `mapping_pending`

读路径不强制每次请求做 maintenance——可能短暂观察到 maintenance 未触发的旧行。排障时先分清是写链问题、maintenance 未触发，还是 API contract 问题。
