# 开发计划

Last updated: 2026-03-28

## 当前状态

当前 `main` 已不再是旧 CLI/解析器主线，而是**桌面产品主线**。`origin/main` 已被替换为当前仓库结构；旧 CLI 版本保存在 `develop`，不再作为主线维护目标。当前活跃产品入口是 `desktop_app/` + `desktop_backend/`，Python 依赖与测试入口统一到 repo 根的 `uv` 工作流。

桌面主链的关键代码阻断已经关闭。最新严格真实 Electron smoke 已通过，报告在 `docs/desktop_electron_smoke_report_2026-03-28.md`；面向干净源码仓的安装、删链回归与构建验证也已经跑通，包括 `uv sync`、`uv run pytest tests/test_environment_tooling.py tests/test_release_gate.py -q`、`uv run pytest tests/test_environment_tooling.py tests/test_app_backend_entry.py -q`、`uv run python -m desktop_backend.app_backend --help`、`cd desktop_app && node --test ./backend_launch.test.js`、`cd desktop_app && npm test`、`cd desktop_app && npm run build`、`uv run python scripts/check_release_gate.py`。

这意味着主线阶段已经从“前端框架替换与主链打通”切换到“前后端产品完成 + 主线纯净化”。接下来不应再回头维护旧 CLI 路径，也不应把额外发布装配、运行产物、业务数据、AI 交接文档重新带回主仓。

## 已完成基线

1. `main` 远端主线已切换到桌面产品仓结构，顶层目录以 `desktop_app/`、`desktop_backend/`、`peap*`、`docs/`、`scripts/`、`tests/` 为核心。
2. React + TypeScript + Vite renderer 已成为桌面前端正式实现，Electron 主进程、开发态 backend 启动链与 smoke 驱动已对齐到同一套桌面产品链。
3. 最新真实 Electron smoke 主路径已闭环；此前的 export、force-stop、interrupt restart 等关键阻断已经修复，并保持显式错误暴露。
4. Python 环境管理、CI 与本地开发入口已经统一到 `uv`。
5. 已补上最小安装元数据修复：`pyproject.toml` 现在导出 `desktop_backend*`，解决“离开 repo root 的已安装环境里导入 `peap.streaming_store` 失败”的问题。
6. `README.md`、`docs/release_gate.md`、`docs/desktop_product_runbook_2026-03-26.md`、`docs/project_layout.md` 已按当前桌面主线对齐，明确写出 `uv` / Node 前置、首次联网下载前置、repo-root 开发态耦合，以及 `docs/superpowers/` 不属于 release 文档集合。
7. 额外发布装配脚本、附带运行时入口与已提交 `dist*` 产物已从主仓移除，主线只保留 repo-root 开发态产品路径。

## 当前阶段目标

当前阶段目标是把仓库推进到**纯净的产品开发主线**，而不是继续做基础设施级重构或假性发布收口。这里至少意味着：

1. 主线文档准确描述当前产品结构与安装/运行前提；
2. 主线 gate 与实际验证命令一致；
3. 真实桌面主路径有可追溯 smoke 证据；
4. 主线不重新混入构建产物、运行数据库、业务输出或 AI 交接垃圾；
5. 所有新增错误路径都保持显式化，不允许静默 fallback 掩盖失败。

## 剩余工作包

### 1. 发布文档一致性收口（首轮已完成）

以下核心文档已经完成首轮对齐：

- `README.md`
- `docs/release_gate.md`
- `docs/desktop_product_runbook_2026-03-26.md`
- `docs/project_layout.md`

后续只需要在新的验证事实出现时做增量维护，重点维持：

- release gate 中的验证命令、通过标准、文档引用持续与当前主线一致；
- 文档继续明确 Node/npm 前置、`uv` 前置、联网下载前置与 repo-root 耦合；
- 当前主仓不把 `docs/superpowers/` 一类 AI 交接物重新表述成产品交付文档。

### 2. interrupt / cancel 发布语义定稿

当前真实 smoke 已通过，但仍需要决定**发布语义**到底接受哪一种标准：

- 方案 A：接受当前 strict smoke 结果，将其视为发布足够证据，只在 release 文档中说明现有 `interrupt_restart` 语义；
- 方案 B：为字面意义上的“可中断长任务”补一个更慢、更稳定的真实 smoke 场景。

这不是基础功能阻断，而是 release gate 语义问题。除非明确要把“字面 interrupted 终态”写进门槛，否则不应再为此重开大范围排查。

### 3. 共享契约结构清理

当前最小补丁虽然修掉了安装元数据问题，但结构上仍有一处待清理的依赖反向：

- `peap/streaming_store.py` 依赖 `desktop_backend.record_identity`

这说明共享领域契约仍挂在 `desktop_backend/` 名下。后续应单开小范围重构，把 `record_identity` 下沉到共享层（优先 `peap_core/`），并让 `peap` 与 `desktop_backend` 都只依赖共享模块。该任务必须带回归验证，证明脱离 repo root 后仍可正常导入相关模块。

### 4. 主线纯净化（已完成）

以下内容已经退出主线，并且不应重新回流到主仓：

- 独立桌面发布脚本与 workflow
- 额外挂载的桌面运行时入口
- 已提交的 `dist*` 等发布产物

## 非目标

以下事项不应进入当前主线：

- 把 `develop` 中的旧 CLI 体系恢复回 `main`
- 重新引入旧解析器/旧运行入口作为对外主路径
- 推送 `data/`、`submission/`、`logs/`、`exports/`、`desktop_app/dist*`、`desktop_app/build`、`node_modules`、`.venv`、`.cache`、`.worktrees`
- 把 `docs/superpowers/` 等 AI 过程文档重新当作最终产品组成部分上传

## 执行优先级

下一轮默认优先级如下：

1. 文档与 release gate 一致性收口
2. interrupt / cancel 发布语义定稿
3. 共享契约结构清理
4. 新版前后端缺口补齐

如果没有用户新指令，后续开发都应围绕这四项推进，而不是重新扩散任务边界。
