# 项目结构

## 当前产品主路径

当前唯一对业务人员开放的产品入口是 `desktop_app/`。Electron 壳层通过 `desktop_backend/` 提供本地 API；共享 runtime 契约位于 `peap_core/`，业务引擎位于 `peap/`，解析器与后处理仍分别在 `peap_parsers/`、`peap_postprocess/`。

Python 环境统一由仓库根 `pyproject.toml` 和 `uv.lock` 管理。开发态默认使用仓库根 `.venv/`；如需准备浏览器运行时，执行：

```bash
uv sync
bash scripts/bootstrap_desktop_env.sh
```

开发态的 `desktop_app/` 并不是可脱离 repo root 独立运行的子项目：Electron 默认会回到仓库根 `.venv` 启动 `desktop_backend.app_backend`，backend cwd 也默认落在 repo root。

## 顶层目录

```text
.
├─ desktop_app/                 # Electron 桌面壳层
├─ desktop_backend/             # 本地后端与产品协调层
├─ peap_core/                   # 共享 runtime 契约与基础元数据
├─ peap/                        # 下载、流水线、导出等引擎逻辑
├─ peap_parsers/                # 各交易所页面解析器
├─ peap_postprocess/            # 后处理规则与执行器
├─ assets/                      # 静态模板、schema、默认配置
├─ docs/                        # 产品文档、计划和运行说明
├─ scripts/                     # 维护脚本
├─ tests/                       # repo 根 Python 测试
├─ pyproject.toml               # Python 项目元数据
└─ uv.lock                      # uv 锁文件
```

## 工作区与数据根

桌面产品默认把工作区放在 `~/Documents/PEAP/`，也允许通过 `PEAP_APP_HOME`、`PEAP_WORKSPACE_ROOT` 或 `PEAP_DOCUMENTS_HOME` 覆盖。

- 数据库：`<workspace_root>/data/streaming_ingest.sqlite3`
- 自动归档：`<workspace_root>/submission/`
- 手动导入：`<workspace_root>/data/raw/manual/`
- 导出目录：`<workspace_root>/exports/`
- 日志：`<workspace_root>/logs/`
- 浏览器缓存：`<workspace_root>/cache/ms-playwright/`

更细的存储规则见 `docs/desktop_storage_layout.md`。

## runtime 边界

- `peap_core/record_identity.py` 承载失败对象身份与证据路径选择等共享契约，`peap/` 与 `desktop_backend/` 都从这里取用。
- `peap_core/source_catalog.py` 是唯一 canonical source metadata 目录，统一提供 source code、显示标签、别名解析与 downloader/backend 共享元数据。
- `peap/export_projection.py` 定义显式 downstream export projection，导出链路不再把任意 raw parser/postprocess 字段直接带入 writer payload。导出只允许使用 canonical 数据，禁止 raw payload 回退。
- `peap/streaming_store_maintenance.py` 是 legacy store normalization 的显式入口；ordinary read paths 不再偷偷修复记录状态或 listing_date。
- parser-layer 重构仍未纳入上述 runtime 边界，后续需要单独设计。

## 状态机与契约模型

### 显式状态机 (RecordState)

记录在流水线中有明确的状态定义，状态不代表异常而是工作流状态：

- `ready` - 记录已完成，可供导出
- `pending_mapping` - 等待类型映射
- `mapping_conflict` - 映射冲突，需人工审核
- `skipped` - 已跳过
- `conflict` - 冲突状态
- `pending_review` - 等待审核
- `parse_failed` - 解析失败
- `postprocess_failed` - 后处理失败

### Canonical 契约模型

`IngestedRecord` 包含显式的 canonical 数据字段：

- `canonical_record` - 规范化记录，包含 `canonical_fields`（标准字段名）和 `business_identity`
- `canonical_projection` - 已持久化的导出投影
- `parser_payload` / `postprocess_payload` - 原始解析/后处理数据，仅作证据保留

导出只使用 `canonical_record.canonical_fields` 或 `canonical_projection`，不接受任意 raw payload 合并。

### Manifest/Registry 所有权模型

- `peap/download_tasks.py` 中 `DownloadTaskManifest` 定义下载任务清单
- `DownloadTaskRegistrySettings` 管理注册表设置
- 下载器通过 manifest 注册，运行时通过 registry 设置获取配置

## 文档边界

以下文档属于当前主线的活跃产品文档：

- `README.md`
- `docs/release_gate.md`
- `docs/desktop_product_runbook_2026-03-26.md`
- `docs/project_layout.md`
- `docs/submission_guide.md`

`docs/superpowers/` 下的 specs / plans / handoff 用于 AI 过程记录与交接，不属于 release 文档集合。

## 提交流程

需要整理归档页面时，统一通过提交准备脚本生成交付目录：

```bash
uv run python scripts/prepare_submission.py
```

输出位于 `<workspace_root>/outputs/submission/`，脚本会重命名页面文件并生成 `_manifest.json`，供后续归档或人工校验使用。
