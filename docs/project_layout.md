# 项目结构

## 当前产品主路径

当前唯一对业务人员开放的产品入口是 `desktop_app/`。Electron 壳层通过 `desktop_backend/` 提供本地 API，运行时仍然会调用 `peap/`、`peap_parsers/`、`peap_postprocess/` 这组引擎模块。

Python 环境统一由仓库根 `pyproject.toml` 和 `uv.lock` 管理。开发态默认使用仓库根 `.venv/`；如需准备浏览器运行时，执行：

```bash
uv sync
bash scripts/bootstrap_desktop_env.sh
```

## 顶层目录

```text
.
├─ desktop_app/                 # Electron 桌面壳层
├─ desktop_backend/             # 本地后端与产品协调层
├─ peap/                        # 下载、流水线、导出等引擎逻辑
├─ peap_parsers/                # 各交易所页面解析器
├─ peap_postprocess/            # 后处理规则与执行器
├─ assets/                      # 静态模板、schema、默认配置
├─ docs/                        # 产品文档、计划和运行说明
├─ scripts/                     # 维护脚本
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

更细的存储规则见 [desktop_storage_layout.md](/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/codex-uv-environment-unification/docs/desktop_storage_layout.md)。

## 提交流程

需要整理归档页面时，统一通过提交准备脚本生成交付目录：

```bash
uv run python scripts/prepare_submission.py
```

输出位于 `<workspace_root>/outputs/submission/`，脚本会重命名页面文件并生成 `_manifest.json`，供后续归档或人工校验使用。
