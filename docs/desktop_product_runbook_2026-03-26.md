# 本地桌面产品运行手册

日期：2026-03-26

## 1. 产品入口与边界

本轮唯一对业务人员暴露的产品入口是 `desktop_app/` 对应的 Electron 桌面壳层。`desktop_backend/` 是它的本地后端；`peap/`、`peap_parsers/`、`peap_postprocess/` 仍然属于运行链路的一部分，但不再作为独立产品入口对外说明。

唯一 shipped profile 是 `desktop_listing`。它已经固定记录族、source 集合、解析兼容层、默认 postprocess profile、导出 profile 与 readiness policy。本轮不交付多记录族 UI。

## 2. 本地启动

开发态启动顺序：

```bash
uv sync
bash scripts/bootstrap_desktop_env.sh
cd desktop_app
npm install
npm start
```

如果需要显式指定 Python 解释器，可覆盖：

```bash
PEAP_DESKTOP_PYTHON=/abs/path/to/.venv/bin/python npm start
```

这会被 Electron 主进程用于拉起 `desktop_backend.app_backend`。如果该解释器路径不存在或不可执行，桌面端会在 backend ready 之前直接 fatal startup，不会先展示主窗口。

## 3. 工作区模型

桌面产品默认只认一个工作区根目录。优先级如下：

1. `PEAP_APP_HOME`
2. `PEAP_WORKSPACE_ROOT`
3. `PEAP_DOCUMENTS_HOME`
4. 默认 `~/Documents/PEAP`

主要目录布局：

- 数据库：`<workspace_root>/data/streaming_ingest.sqlite3`
- 自动归档页面：`<workspace_root>/submission/`
- 手动导入暂存：`<workspace_root>/data/raw/manual/`
- 导出目录：`<workspace_root>/exports/`
- 日志：`<workspace_root>/logs/`
- 浏览器缓存：`<workspace_root>/cache/ms-playwright/`

## 4. 业务主路径

首页主动作只有三类：

- `一键执行`
- `导出 Excel`
- `手动导入解析`

关键产品语义如下：

- `一键执行` 只有在真实创建 job 成功时才会返回可追踪的 `job_id`
- 浏览器运行依赖未就绪时，下载类任务在后端直接拒绝并返回 `503`
- `手动导入解析` 对无效目录返回用户输入错误，而不是内部 `500`
- `导出 Excel` 默认使用 `rebuild` 模式，并且严格绑定当前显式 scope
- 任务事件、待补映射列表、映射回刷结果都显式携带容量 envelope，不再静默截断

## 5. 故障定位

首看两类日志：

- 主进程日志：`~/Documents/PEAP/logs/desktop-app-main.log`
- 后端日志：`~/Documents/PEAP/logs/desktop-backend.log`

常见故障面：

- `startup_fatal`：通常是 backend launch target 无法解析、解释器不存在、backend 提前退出或 ready 超时
- `503` 产品未就绪：通常是 Chromium/browser runtime 缺失
- 导出为空：先检查当前 scope 是否真的命中 `ready` 记录，再看 `empty_reason_code`
- 映射列表只显示前 N 条：这是显式容量 envelope，不是列表丢失

## 6. 发布前人工检查口径

发布前至少要补齐以下人工操作：

- 首启成功
- backend ready 失败时的 fatal startup
- runtime dependency 未就绪时的引导提示
- one-click 成功与失败
- manual-import 有效目录与无效目录
- export rebuild
- 中断与重启恢复

这些步骤的自动化语义已被后端和 renderer 测试覆盖，但发布结论仍应要求一次真实 Electron 操作闭环。
