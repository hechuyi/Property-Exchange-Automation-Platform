# 桌面端 ingest 对齐与手动导入设计

## 背景

当前桌面端的主要结构偏差有三处。

第一，补充映射后的刷新逻辑只会重处理当前选中或当前草稿带到的记录，无法把同一条规则影响到的历史记录统一回刷，导致数据库状态与映射规则不一致。

第二，桌面端没有真正完成“手动导入解析”。现有代码只有 `manual_import` 任务类型占位和“手动导入目录”展示，但没有后台任务入口来递归扫描本地 HTML/MHTML 并走入库流程。

第三，下载链路仍是“先保存原始快照，再复制到归档目录”的双写模型。这会带来重复 I/O、状态投影混乱，以及用户感知中的“为什么还要 archive 一次”问题。

## 目标

1. 保存映射规则后，自动批量回刷所有可能受影响的最新记录，而不是只刷新一条。
2. 增加正式的“手动导入解析”任务，复用 CLI 的文件发现与 parser 能力，把本地 HTML/HTM/MHTML 导入桌面端数据库。
3. 下载阶段直接保存到最终业务归档路径，不再进行 raw -> archive 的二次复制。
4. 小区间下载在用户未显式指定页数时，默认限制扫描页数，避免无意义地扫描全部列表页。
5. 保持边界清晰：下载器负责确定最终文件落点，ingest 负责 parse/postprocess/store，服务层负责任务编排，前端只消费任务视图模型。

## 非目标

1. 不新增 downloader 站点适配。
2. 不重写 CLI parser / PPE 的主体实现。
3. 不引入新的前端框架。

## 设计决策

### 1. 映射规则保存后的批量回刷

新增一个后台任务类型 `mapping_refresh`。保存映射规则后，服务层不再只对显式选中的 `record_id` 调单条 `reprocess`，而是根据新增或更新的规则生成受影响记录集合，并启动后台批量回刷任务。

候选记录集合的判定规则：

- `match_field=transferor` 时，检查最新 revision 的 `parser_payload` / `postprocess_payload` 中转让方相关字段；
- `match_field=group` 时，检查最新 revision 中集团相关字段；
- 不只回刷 `pending_mapping`，也回刷 `ready`，因为类型/集团字段可能发生变化；
- `skipped`、`parse_failed` 不因映射规则变化而重跑。

后台任务逐条调用统一的 `StreamingIngestRunner.ingest()`，并更新：

- `records`
- `record_revisions`
- `mapping_pending.resolved_at`
- `jobs` / `job_events`

前端只显示任务进度和影响条数，不在浏览器端自行推导哪些记录要刷新。

### 2. 手动导入解析

补齐一个正式的 `manual_import` 任务入口。用户选择目录后，后端递归发现：

- `*.html`
- `*.htm`
- `*.mhtml`

文件发现逻辑复用 CLI parser pipeline 的规则，保持与现有 parser 兼容站点一致。解析本身仍走 `StreamingIngestRunner`，因此手动导入和下载导入共享同一条：

- parser dispatch
- postprocess
- mapping
- SQLite upsert
- 导出

手动导入分两种路径：

- 若源文件已在工作区根目录内，直接原位 ingest；
- 若源文件在工作区外，先物化到工作区 canonical 路径，再 ingest。

这样工作区内始终只有一份受管理文件，数据库也只引用这一份 canonical 文件。

### 3. 取消下载后的独立 archive copy

下载器负责在保存前确定最终归档路径，而不是先写 raw 再由 ingest 复制到 archive。

规则如下：

- 对列表阶段已知 `project_code` / `project_name` 的站点，直接用这些字段决定最终路径；
- 对只有详情页才能确定最终编号的站点，在详情页渲染完成后先抽取最终编号，再一次性保存到最终路径；
- 保存完成后，`item_saved_callback` 直接回传 canonical `source_file`。

`StreamingIngestRunner` 不再调用 `copy_snapshot_to_archive()` 复制文件，只把现有 `source_file` 作为记录文件路径写入数据库。若发生命名冲突，由下载保存层解决，而不是 ingest 层解决。

### 4. 小区间默认页数上限

当用户没有显式填写 `max_pages` 且日期跨度属于“非大区间”时，下载任务默认 `max_pages=10`。当前先定义“非大区间”为 `<= 7` 个自然日。

该默认值只作为桌面端默认策略：

- 用户显式设置页数时，用户值优先；
- CLI 行为不受影响；
- 站点适配层保留覆盖空间，便于未来按 source 调整。

### 5. 模块边界

- 下载器：决定最终文件路径并保存页面；
- ingest runner：parse/postprocess/store，不复制归档；
- 服务层：创建 `one_click` / `manual_import` / `mapping_refresh` 任务并投影任务状态；
- 前端：只负责发起任务、显示任务、展示记录与映射状态。

## 受影响模块

- `desktop_backend/app_service.py`
- `desktop_backend/app_backend.py`
- `peap/streaming_ingest.py`
- `peap/streaming_daily_pipeline.py`
- `peap/streaming_store.py`
- `peap/download_oneclick.py`
- `peap/download_runner.py`
- `peap/downloaders/*.py`
- `desktop_app/index.html`
- `desktop_app/renderer.js`
- `desktop_app/styles.css`
- `tests/test_app_service.py`
- `tests/test_streaming_ingest.py`
- `tests/test_streaming_store.py`
- `tests/test_download_oneclick.py`

## 验证策略

1. 用服务层测试锁定映射保存后会启动批量回刷，而不是只刷新单条记录。
2. 用 store / ingest 测试锁定回刷后 `pending_mapping` 能被统一 resolve。
3. 用手动导入测试锁定目录递归发现 HTML/HTM/MHTML 并进入统一 ingest。
4. 用下载器/编排测试锁定 canonical path 直接保存与小区间默认 `max_pages=10`。
5. 用桌面端 UI 检查确认手动导入入口、任务侧栏和映射回刷反馈符合业务语义。
