# 本地桌面产品交付验收报告

日期：2026-03-26

## 1. 目的

本报告用于收口 [本地桌面自动化工具产品交付任务卡总计划](/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/task-9-delivery/docs/superpowers/plans/2026-03-25-desktop-product-delivery-task-pack.md) 的 Task 9。验收对象不是单张任务卡，而是已经在集成分支上合流后的桌面产品：`desktop_app/`、`desktop_backend/` 以及仍处于运行链路上的 `peap/`、`peap_parsers/`、`peap_postprocess/`。

本轮只认三类证据：集成分支上的自动化回归结果、真实 Electron 启动行为、以及仍未完成的发布阻塞项。未在当前会话中直接验证的路径，不写成“已通过”。

## 2. 集成内容

当前交付基线是 `codex/task-9-delivery@7381396`，其上已经包含 Task 1 至 Task 8 的产出。其中相对 Task 1 基线 `38affb8` 的后续集成提交为：

- `20f3d79` `feat: harden backend task preflight validation`
- `ce7257b` `feat: rebuild exports from explicit scope`
- `23b136e` `feat: expose backend capacity envelopes explicitly`
- `54fd2aa` `feat: project backend capacity hints into desktop UI`
- `59d5ca6` `feat: gate desktop startup on backend readiness`
- `50ddebc` `feat: preserve ingest type contracts for otc pages`
- `7381396` `feat: ship a working default postprocess profile`

对应的产品语义收口如下：

- `desktop_listing` 成为唯一 shipped profile，产品错误模型与 HTTP 映射固定。
- 一键执行、手动导入、运行依赖检查的可信前置条件已经后端化，不再依赖前端按钮状态。
- `rebuild` 导出语义固定为“基于显式 scope 的重建”，不再暗含增量游标语义。
- OTC 页面兼容、默认后处理 profile、容量 envelope、前端可读截断提示都已经在主链上锁住。

## 3. 自动化验收

### 3.1 Python 集成验收

在 `/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/task-9-delivery` 上直接执行：

```bash
../task-5-ingest-compat/.venv/bin/python -m unittest \
  tests.test_source_registry \
  tests.test_record_scope \
  tests.test_progress_contract \
  tests.test_record_identity \
  tests.test_http_contract \
  tests.test_streaming_store \
  tests.test_streaming_export \
  tests.test_app_service \
  tests.test_app_backend \
  -v
```

结果：

- `Ran 134 tests in 5.022s`
- `OK`

这组测试覆盖了本轮交付要锁住的三条主线：任务语义、范围语义、对象身份；同时也直接覆盖了产品错误、导出 rebuild、容量 envelope、后端 HTTP 契约和服务层主入口。

### 3.2 Node Renderer/Layout 验收

在同一集成分支上直接执行：

```bash
node --test \
  desktop_app/layout_contract.test.js \
  desktop_app/renderer/records.test.js \
  desktop_app/renderer/exports.test.js \
  desktop_app/renderer/tasks.test.js
```

结果：

- `24 pass`
- `0 fail`

覆盖重点包括：

- 记录页 scope/query/summary 语义
- 导出请求与空导出解释
- 任务面板终态、事件文案、容量提示
- 首页和布局契约

## 4. 真实 Electron 验收

### 4.1 已直接验证的路径

本轮在当前机器上执行了真实 `npm start` 启动验证，而不是只看 Node 单测。

路径 A：默认开发环境启动失败收口。

- 启动命令：`npm start`
- 结果：主进程在 backend launch 校验阶段直接进入 fatal startup
- 证据：`/Users/rtoc/Documents/PEAP/logs/desktop-app-main.log` 中存在 `backend_launch_invalid` 与 `startup_fatal`
- 根因：仓库根 `.venv-desktop/bin/python` 指向 `/Users/rtoc/.pyenv/versions/3.11.9/bin/python`，该符号链接目标在当前机器上不存在
- 结论：Task 3 的“backend 未 ready / 启动失败不得先进入主窗口”在真实启动路径上成立

路径 B：覆盖可执行 Python 后成功到达 ready。

- 启动命令：`PEAP_DESKTOP_PYTHON=/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/task-5-ingest-compat/.venv/bin/python npm start`
- 结果：Electron 成功拉起本地 backend，日志出现 `backend_spawned` 与 `backend_ready`
- 证据：`/Users/rtoc/Documents/PEAP/logs/desktop-app-main.log` 在 `2026-03-26T01:26:17Z` 到 `2026-03-26T01:26:18Z` 记录了完整 ready 过程
- 结论：在解释器路径可执行的前提下，桌面壳层可以进入正常启动态

### 4.2 未在本会话直接完成的 smoke

以下 Electron operator 路径尚未在当前会话中完成逐项手工操作，因此不能写成“通过”：

- runtime dependency 未就绪下的页面交互
- one-click 创建成功/失败
- manual-import 有效/无效目录
- export rebuild
- interrupt 与 recovery

这些项目已由后端/渲染层自动化覆盖其关键语义，但仍缺少同一会话中的完整人工操作证据。

## 5. 验收结论

从代码集成和自动化证据看，Task 1 至 Task 8 的交付目标已经在 Task 9 集成分支上落稳；本轮最关键的发布信任问题，包括假成功一键执行、导出 rebuild 语义、OTC 兼容、默认规则包失效、后端容量静默截断、前端内部异常口吻，均已有直接回归锁定。

当前仍不能把本产品标记为“无条件可发布”，原因不是主链实现再次失效，而是发布前验收证据仍有两个缺口：

- 开发态默认 `.venv-desktop` 解释器链接在当前机器上断裂，首启会直接触发 fatal startup。
- Electron operator 主路径的手工 smoke 在本会话内尚未做完。

因此，本轮更准确的结论是：产品代码已达到交付候选状态，自动化验收通过，真实启动可信；但在补齐运行环境与剩余手工 smoke 前，不应宣称“最终发布已完成”。
