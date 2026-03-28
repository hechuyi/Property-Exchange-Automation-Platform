你接手的是**桌面产品纯净主线阶段**，不是再做“前端替换”、旧 CLI 兼容，或任何已退役运行路径收口。

先确认以下事实，不要凭旧上下文行动：

1. 当前工作目录就是根仓：
   - `/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform`
2. 当前活跃分支就是 `main`
3. `origin/main` 已经被替换为桌面产品仓结构；旧 CLI 版本在 `develop`，不要试图把它搬回 `main`
4. 最近一次主线上传提交是：
   - `8a969271d43e26e10105e6c216c8f176a87fceb5`

你开始时**只读**下面三份文件，别先横向扫整个仓库：

- `/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/docs/development_plan.md`
- `/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/docs/desktop_electron_smoke_report_2026-03-28.md`
- `/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/docs/superpowers/plans/2026-03-28-desktop-frontend-framework-replacement-handoff.md`

当前已经确认通过的基线，不要重复怀疑：

- `uv sync`
- `uv run pytest tests/test_environment_tooling.py tests/test_release_gate.py -q`
- `uv run python -m desktop_backend.app_backend --help`
- `cd desktop_app && npm test` → `67/67`
- `cd desktop_app && npm run build`
- 最新严格真实 Electron smoke 已通过，原始报告在：
  - `/tmp/peap-desktop-smoke-main-1774696290.md`

你接下来的默认目标，不是修一个新的代码 blocker，而是做**纯净主线收口**。默认按这个顺序推进：

1. 文档与 release gate 一致性收口
2. interrupt / cancel 发布语义定稿
3. `record_identity` 共享契约结构清理
4. 新版前后端缺口补齐

第一优先任务，除非用户另行指定：**把 README / release gate / runbook / project layout 对齐到当前真实主线**。重点核对：

- `README.md`
- `docs/release_gate.md`
- `docs/desktop_product_runbook_2026-03-26.md`
- `docs/project_layout.md`

尤其注意这些已知边界：

- README 里不要再保留旧运行路径的活跃叙事；
- Node/npm 前置、`uv` 前置、首次联网下载前置、repo-root 耦合必须写清；
- 所有错误都应显式化，不要用静默 fallback 掩盖失败；
- `docs/superpowers/` 是 AI 交接材料，默认不要再推成“最终产品组成部分”。
- 桌面端现在只保留 repo-root 开发态；不要重新引入 `electron-builder`、`package_desktop.js`、`build_backend_sidecar.js`、`desktop-package.yml` 或任何额外运行时条件分支。

关于结构性待办，当前已做的只是**最小补丁**：

- `pyproject.toml` 已导出 `desktop_backend*`
- 这样修复了“离开 repo root 的已安装环境里导入 `peap.streaming_store` 失败”

但真正的结构问题还在：

- `peap/streaming_store.py` 仍依赖 `desktop_backend.record_identity`

如果用户让你做这件事，正确方向是：

1. 把 `desktop_backend/record_identity.py` 迁到共享层（优先 `peap_core/`）
2. 改 `peap/streaming_store.py`、`desktop_backend/app_service.py` 与对应测试
3. 保留短期兼容 shim
4. 用“脱离 repo root 仍可导入相关模块”的回归验证证明修复成立

工作方式要求：

1. **多用 subagent，但控制上下文**
   - 主 AI 只读最少文件后再分派
   - 不要让多个 subagent 重复读同一批上下文
   - 若 agent 配额满了，先关掉完成的
2. **先验证，再宣称完成**
   - 文档改动至少跑相关测试
   - 代码改动必须跑最小回归 + 必要构建验证
3. **不要重新扩散边界**
   - 不要回头审旧 CLI 大树
   - 不要恢复已删除的旧入口
   - 不要把构建产物、运行数据、业务输出重新带回仓库

如果你需要一个最小验证集，优先跑：

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform
uv run pytest tests/test_environment_tooling.py tests/test_release_gate.py -q
cd desktop_app && npm test
cd desktop_app && npm run build
```

如果你完成了新的收口动作，记得更新：

- `/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/docs/development_plan.md`
- `/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/docs/superpowers/plans/2026-03-28-desktop-frontend-framework-replacement-handoff.md`

除非用户明确要求，否则不要再重写整个仓库历史，也不要再把问题表述成“manual_import 仍是主阻断”——这个阶段已经过去了。
