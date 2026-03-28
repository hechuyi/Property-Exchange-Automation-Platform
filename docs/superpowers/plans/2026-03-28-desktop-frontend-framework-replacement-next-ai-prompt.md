这是一个**窄边界收口任务**，不是重新大范围审仓库。主 AI 的目标是：**用尽量少的 token，借助 subagent，把真实 Electron smoke 的 `manual_import` 阻断查清并修掉**。

上个 AI 已把最新交接更新到：

- `/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/codex-desktop-frontend-framework-replacement/docs/superpowers/plans/2026-03-28-desktop-frontend-framework-replacement-handoff.md:1`

你先只读上面这 1 个文件；先不要重新大范围翻仓库，也不要回根仓库 `main`。只用：

- `/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/codex-desktop-frontend-framework-replacement`

这次不是再做 A/B/C/D 的大面收口。那四块已经基本做完，并且以下验证已经通过：

- `cd desktop_app && npm test` → `66/66`
- `cd desktop_app && npx vitest run` → `44/44`
- `cd desktop_app && npm run build` → pass，最大 chunk 约 `375.80 kB`
- `cd desktop_app && node --test ./smoke_driver.test.js` → `6/6`
- `cd desktop_app && node --test ./layout_contract.test.js` → `14/14`

剩下的唯一阻断是：**真实 Electron smoke 仍在 `manual_import` 步骤超时**。

已知证据，别重复浪费 token：

- `uv sync` 已经在该 worktree 跑过，`.venv` 已存在
- 真实 smoke 已多次复跑，当前 dated 报告在：
  - `/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/codex-desktop-frontend-framework-replacement/docs/desktop_electron_smoke_report_2026-03-28.md:1`
- `pickDirectory` 在真实 smoke 中确实被调用，而且主进程日志已经记录到正确 fixture 路径
- 同一 fixture 路径直接 POST 到 `/api/jobs/manual-import` 时，后端能立刻创建 `manual_import` job
- 所以现在的主嫌疑不是 backend service，也不是 fixture 路径，而是 renderer manual-import mutation / desktop http path / smoke UI interaction 链之间的边界

主 AI 工作方式要求：

1. **多用 subagent，但要有意节省 token**
   - 主 AI 先只读 handoff；读完就停，不要自己继续横向扫大量文件
   - 然后立刻拆成最少 2 个聚焦 subagent：
     - 一个只查 renderer manual-import 边界（`useOverview.ts` / `http.ts` / `commands.ts`）
     - 一个只查 smoke driver 实际交互链（`smoke_driver.js` / `main.js`）
   - 如果 agent 数量达到上限，先关闭已完成的 agent，再开新的
   - 主 AI 自己不要把大量无关文件重新读一遍
   - 不要让不同 subagent 重复读同一批上下文
   - 主 AI 主要负责：分派、集成、最终验证；不是亲自重做所有排查
   - 不要频繁 `wait_agent` 轮询；让 subagent 工作时，主 AI 做非重叠的轻量集成准备

2. **以真实 Electron smoke 为最终判据**
   - 不是只看 `npm test` / `vitest`
   - 每次改完如果声称 blocker 清掉了，必须重新跑真实 smoke

3. **保持显式状态机和显式错误**
   - 不要用“直接改后端调用替代 UI 路径”来假装 smoke 通过
   - 不要用静默 fallback 掩盖 renderer/manual-import 失败

4. **保持上下文小**
   - 除了 handoff 和 dated smoke report，下一批优先只读：
     - `/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/codex-desktop-frontend-framework-replacement/desktop_app/src/features/overview/useOverview.ts:1`
     - `/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/codex-desktop-frontend-framework-replacement/desktop_app/src/desktop/http.ts:1`
     - `/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/codex-desktop-frontend-framework-replacement/desktop_app/smoke_driver.js:1`
     - `/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/codex-desktop-frontend-framework-replacement/desktop_app/main.js:1`
   - 其余文件只由对应 subagent 按需开
   - 除非某个 subagent 需要该文件来落补丁，否则不要追加阅读范围

真实 smoke 复跑命令模板（下个 AI 可以直接复用）：

```bash
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/codex-desktop-frontend-framework-replacement/desktop_app
REPORT_PATH="/tmp/peap-desktop-smoke-$(date +%s).md"
PICK_DIR="/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/codex-desktop-frontend-framework-replacement/desktop_app/smoke_fixtures/manual_import_equity_transfer"
PICK_QUEUE=$(printf '["%s"]' "$PICK_DIR")
PEAP_DESKTOP_SMOKE_REPORT_PATH="$REPORT_PATH" \
PEAP_DESKTOP_SMOKE_PICK_DIRECTORIES="$PICK_QUEUE" \
./node_modules/.bin/electron .
```

收尾前要更新：

- `/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/codex-desktop-frontend-framework-replacement/docs/desktop_electron_smoke_report_2026-03-28.md:1`
- `/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/.worktrees/codex-desktop-frontend-framework-replacement/docs/superpowers/plans/2026-03-28-desktop-frontend-framework-replacement-handoff.md:1`
