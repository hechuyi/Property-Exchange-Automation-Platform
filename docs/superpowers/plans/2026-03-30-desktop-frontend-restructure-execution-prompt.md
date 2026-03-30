你现在接手 `/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform` 的桌面端前端重构实现。不要重新做产品判断，不要改目标，不要把计划降级成“顺手修几个点”。按现有计划逐任务执行，保持 TDD 和小步提交。

必须先读这份计划：

`/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/docs/superpowers/plans/2026-03-30-desktop-frontend-restructure.md`

执行约束：

1. 只围绕该计划实现，不额外扩展需求。
2. 优先改 `desktop_app/src/` React/Electron 壳层，不要把注意力放到 legacy `desktop_app/renderer.js`，除非计划明确要求。
3. 每个任务都遵循：
   - 先写或修改失败测试
   - 运行针对性测试确认失败
   - 写最小实现
   - 重新运行针对性测试确认通过
   - 再提交
4. 不要跳过计划里的文件边界；如果确实需要偏离，先在回复里说明原因，再最小化偏离。
5. 对设置页路径交互，优先使用原生 picker；不要把路径字符串输入框继续当成主交互。
6. 对导航重构，最终目标是 `workbench / records / mappings` 三个主目标页，`settings` 为低频入口，`tasks` 不再是一级页面。
7. 对记录状态，不要继续直接暴露后端技术态；UI 只保留用户决策相关的状态表达。
8. 对映射页，必须保证“待补映射队列 + 当前编辑器”首屏同屏可见；不能再是长页面靠滚动串起来。
9. 每完成一个任务，就运行该任务计划里列出的命令，不要用“应该通过”代替验证。
10. 在最终收口前，必须至少运行：
    - `cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/desktop_app && npm test`
    - `cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/desktop_app && npx vitest run`
    - `cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/desktop_app && npm run build`
    - `cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform && uv run python scripts/check_release_gate.py`

额外上下文：

- 最近已经修过一个真实 bug：记录页 `projectType` 前端枚举曾与后端契约不一致，现已改为 `equity_transfer / physical_asset / capital_increase / pre_disclosure`。不要回退。
- 现有设置后端只允许保存很少字段，若要实现真正的目录可编辑，需要同步改 `desktop_backend/app_service.py` 和 `desktop_backend/app_backend.py`。
- `/private/tmp` 下的旧探针报告不可靠，也可能已经被系统清理。不要依赖它们做结论。

工作方式：

- 从计划的 Task 1 开始顺序执行。
- 每个任务结束时简短汇报：改了什么、跑了什么、是否通过。
- 如果中途发现计划本身需要微调，只允许做局部、可解释的修订，不能擅自改产品方向。
