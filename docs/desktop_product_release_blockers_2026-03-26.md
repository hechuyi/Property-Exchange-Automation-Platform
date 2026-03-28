# 本地桌面产品发布阻塞清单

日期：2026-03-26

## 1. 当前仍阻塞发布的项目

### 1.1 开发态默认 Python 运行时断裂

现象：

- `desktop_app/main.js` 解析开发态 backend launch 时默认指向仓库根 `.venv-desktop/bin/python`
- 当前机器上的该路径是一个断裂的符号链接：`.venv-desktop/bin/python -> /Users/rtoc/.pyenv/versions/3.11.9/bin/python`
- 真实 `npm start` 会直接触发 `backend_launch_invalid` 和 `startup_fatal`

影响：

- 开发态首启不可直接复现
- Task 9 的真实启动成功必须依赖显式覆盖 `PEAP_DESKTOP_PYTHON`

阻塞等级：P1

### 1.2 Electron operator 主路径手工 smoke 未闭环

未完成项目：

- runtime dependency 未就绪
- one-click 成功/失败
- manual-import 有效/无效目录
- export rebuild
- interrupt 与 recovery

影响：

- 自动化已经证明契约语义成立，但还缺少桌面端同一会话下的人工操作证据
- 因此当前只能宣称“交付候选”，不能宣称“发布完成”

阻塞等级：P1

## 2. 当前不再视为发布阻塞的已关闭问题

以下问题已在 Task 1 至 Task 8 中得到代码级修复和自动化锁定，不再保留为发布 blocker：

- 一键执行在未创建任务时返回空 `job_id` 假成功
- 无效手动导入目录返回 `500`
- backend 未 ready 时桌面仍先展示主窗口
- `rebuild` 导出复用增量语义
- 项目类型依赖目录名、OTC 页面整页 skip
- 默认 shipped postprocess profile 指向缺失模板
- pending mappings、mapping refresh、job events 的容量边界静默丢失
- 前端把后端内部异常原文直接暴露给操作者

## 3. 发布口径

在关闭 1.1 与 1.2 之前，推荐的产品状态标签应为：

- `release_candidate`

不推荐使用：

- `generally_available`
- `final_release`
