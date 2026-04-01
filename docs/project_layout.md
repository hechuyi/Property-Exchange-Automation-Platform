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
├─ peap_core/                   # 共享 runtime 契约、快照/记录身份契约、source catalog
├─ peap/                        # 下载、流水线、组装、规范化、导出等引擎逻辑
├─ peap_parsers/                # 各交易所页面解析器与 parser family runtime
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

- `peap_core/snapshot_contracts.py` 定义 `SnapshotEnvelope` / `DecodedDocument`，把快照与解码结果提升为一等输入。
- `peap_core/page_parse_contracts.py` 定义 `SourceMatch` / `PageParseResult`，页级解析与分类结果不再直接耦合 store/export。
- `peap_core/record_contracts.py` 定义 `AssembledRecordCandidate` / `CanonicalRecord`，跨页组装与规范化输出有稳定契约。
- `peap_core/record_identity.py` 承载失败对象身份、成功记录 source identity、证据路径选择等共享契约，`peap/` 与 `desktop_backend/` 都从这里取用。
- `peap_core/source_catalog.py` 是唯一 canonical source metadata 目录，统一提供 source code、显示标签、别名解析与 downloader/backend 共享元数据。
- `peap/parsing.py` 现在只是 compatibility facade；真正的 parser runtime 由 `peap/parser_subsystem.py`、`peap_parsers/parser_registry.py`、`peap_parsers/family_runtime.py` 组合完成。
- `peap/record_assembler.py`、`peap/record_normalizer.py`、`peap/policy_engine.py` 分别承担组装、规范化、policy 决策，导出与 UI 不再直接依赖 parser 原始 payload 语义。
- `peap/streaming_ingest.py`、`peap/streaming_store.py`、`peap/streaming_export.py` 已开始以 canonical record / canonical projection 作为主数据，并保留 compat 投影给旧调用方。
- `peap/parse_cache.py` 与 `peap/pipeline.py` 的缓存签名已包含 decoder/classifier/family/variant/assembler/normalizer/policy 版本，避免只靠旧 parser 文件签名失效。
- `desktop_backend/app_service.py` 的 replay/reprocess 路径会复用已存储的 `source_url`、snapshot id / digest 等快照元数据，而不只依赖 parser payload。
- `peap/streaming_store_maintenance.py` 仍是 legacy store normalization 的显式入口；ordinary read paths 不再偷偷修复记录状态或 listing_date。

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
