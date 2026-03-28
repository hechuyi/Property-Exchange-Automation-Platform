# 本地桌面自动化工具产品交付任务卡总计划

## Summary

目标是把当前 `main@79bad66` 推进到**可真实交付给业务人员使用**的本地桌面产品，而不是继续做零散修补。执行方式沿用“中央监督 + 独立工作树 + 低智能子代理任务卡”的方法，但新一轮以**交付产品**为终点，内部先完成一小段架构收口，再清掉发布信任 blocker，最后做真实产品验收。

核心原则固定为：**本地单机、稳定内核、外层可插拔**。内核稳定层只包含任务账本、记录契约、对象身份、范围语义、运行时配置和工作区模型；可插拔层只开放下载器/站点扫描器、解析器、后处理规则包、导出模板。之前 6 个 task 视为 Phase 0 地基，后续只允许必要返修，不推倒重来。

## Important Interface Decisions

- 新增一个**产品 profile** 内核模块，默认且唯一交付 `desktop_listing`。它固定 `record_family`、source 集合、parser compat、postprocess profile、export profile、readiness policy，后续新站点通过注册 profile/adapter 扩展，而不是修改主链。
- 新增一个**产品错误**内核模块，统一建模可预期错误：
  - `400`：用户输入错误，例如无效目录、非法日期范围
  - `409`：当前状态不允许，例如互斥任务冲突
  - `503`：运行依赖未就绪或 backend 尚未 ready
  - `404`：资源不存在
- `GET /api/mappings` 不再只返回裸 `pending` 列表，改为显式携带截断语义：`pending: { items, returned_count, total_count, truncated }`
- 映射回刷相关响应统一补齐容量语义：`affected_returned_count`、`affected_total_count`、`truncated`
- `POST /api/jobs/one-click` 只有在**真实创建 job 成功**时才返回 `202 + job_id`；不再允许空 `job_id` 假成功。
- `run_export(mode="rebuild")` 的定义固定为：**基于当前显式 scope 重建该范围内的导出结果**，不得复用增量语义。

## Task Cards

### Task 0: 中央监督卡 A - 冻结新一轮产品交付底稿
- 作用：把这份总计划整理成新的任务卡包底稿，给每张卡分配唯一 worktree、唯一写集、唯一依赖顺序。
- 写集：新计划文档本身；不改产品代码。
- 产出：分支/工树命名、并行规则、回包模板、红线。
- 验证：能明确说出每张卡的允许写集与可并行关系。

### Task 1: 内核骨架卡 - `desktop_listing` profile + 产品错误模型
- 作用：先把“产品怎么装配、错误怎么分型”固定下来，后续所有卡只能沿这两个内核扩展。
- 写集：`peap/product_profile.py`、`desktop_backend/product_errors.py`、`desktop_backend/app_service.py`、`desktop_backend/app_backend.py` 及对应测试。
- 产出：唯一 shipped profile `desktop_listing`；统一错误类型到 HTTP 映射。
- 验证：新增 profile/错误模型测试；既有 `app_service`/`app_backend` 测试仍通过。
- 依赖：Task 0。
- 并行：后续所有实现卡都依赖它，不能并行启动。

### Task 2: 后端任务可信卡 - 一键执行/手动导入/运行依赖前置校验
- 作用：清掉 `5.1`、`5.4`、`5.12`。
- 写集：`desktop_backend/app_service.py`、`peap/streaming_daily_pipeline.py`、`tests/test_app_service.py`、`tests/test_streaming_daily_pipeline.py`、必要时 `tests/test_app_backend.py`。
- 产出：一键执行只有真实 `job_id` 才返回 accepted；无效手动导入目录返回 `400`；浏览器未就绪返回 `503` 而不是偷偷启动。
- 验证：目标测试覆盖空 job、无效目录、未就绪启动三条路径。
- 依赖：Task 1。
- 并行：可与 Task 3、Task 5、Task 6 并行。

### Task 3: 桌面启动可信卡 - Electron 只在 backend 真 ready 后进入主窗口
- 作用：清掉 `5.24`。
- 写集：`desktop_app/main.js`、`desktop_app/backend_ready.js`、对应桌面启动测试。
- 产出：backend ready 前退出、ready 超时、spawn 失败都应转成明确 fatal startup；不得先展示主窗口再失败。
- 验证：桌面启动测试补齐 `early-exit / ready-timeout / success` 三态。
- 依赖：Task 1。
- 并行：可与 Task 2、Task 5、Task 6 并行。

### Task 4: 导出重建语义卡 - `rebuild` 真重建
- 作用：清掉 `5.13`，并把导出继续绑定到显式 scope/profile。
- 写集：`peap/streaming_export.py`、`desktop_backend/app_service.py`、`tests/test_streaming_export.py`、`tests/test_app_service.py`。
- 产出：`rebuild` 不再复用增量标记；导出结果、空导出解释、scope 都以当前显式范围为准。
- 验证：补“连续两次 rebuild 仍按全范围输出”的回归测试。
- 依赖：Task 1、Task 2。
- 并行：不与其他改 `app_service.py` 的卡并行。

### Task 5: 真实导入兼容卡 - 项目类型归属 + OTC 页面兼容
- 作用：清掉 `5.27`、`5.28`。
- 写集：`peap/parsing.py`、`peap/streaming_ingest.py`、`peap/streaming_postprocess.py`、`tests/test_streaming_ingest.py`、`tests/test_parsing_contract.py`。
- 产出：项目类型不再依赖目录名；可恢复的北交互联 OTC 页面不再整页 skip；不能恢复时要给出显式失败语义。
- 验证：真实样本/fixture 回归锁住“未知类型落库”和“整页 skip”两条裂缝。
- 依赖：Task 1。
- 并行：可与 Task 2、Task 3、Task 6 并行。

### Task 6: 默认规则包产品化卡 - shipped postprocess profile 可真实工作
- 作用：清掉 `5.29`。
- 写集：默认规则配置资产、`peap/product_profile.py`、必要的打包/定位测试。
- 产出：桌面产品自带一个有效的默认 postprocess profile；不再引用缺失模板路径。
- 验证：补“默认 profile 可加载并能跑一次真实导入”的测试。
- 依赖：Task 1。
- 并行：可与 Task 2、Task 3、Task 5 并行。

### Task 7: 容量与截断后端契约卡
- 作用：清掉 `5.7`、`5.11`、`5.23` 的后端语义缺口。
- 写集：`desktop_backend/http_contract.py`、`desktop_backend/app_backend.py`、`desktop_backend/app_service.py`、`peap/streaming_store.py` 及对应测试。
- 产出：pending mappings、mapping refresh、job events 都显式返回 `returned_count / total_count / truncated`；不再静默截断。
- 验证：补 3 类 envelope/cap 测试，锁住上限语义。
- 依赖：Task 1、Task 2。
- 并行：后端侧串行执行，不与其他 `app_service.py` 卡并行。

### Task 8: 容量与错误桌面投影卡
- 作用：把 Task 7 的后端容量语义变成业务人员能看懂的桌面提示。
- 写集：`desktop_app/renderer.js`、`desktop_app/renderer/tasks.mjs`、`desktop_app/renderer/mappings.mjs`、对应 Node/layout 测试。
- 产出：待补列表、任务事件、回刷结果都能看见“只显示前 N 条 / 仍有剩余”的提示；错误语义不再是内部异常口吻。
- 验证：Node + layout contract 补齐 truncation/hint 回归。
- 依赖：Task 7。
- 并行：前端卡独占，单独执行。

### Task 9: 中央监督卡 B - 产品验收、交付文档、真实报告收口
- 作用：完成真正交付前的最后收口。
- 写集：真实操作报告、交付说明、运行手册、todo、任务卡状态文档；非必要不改产品代码。
- 产出：全量验证记录、真实 Electron 验收结果、发布阻塞清单、交付文档。
- 验证：
  - Python 全量契约/服务/后端测试
  - Node 全量 renderer/layout 测试
  - Electron 手工 smoke：首启、未就绪、启动失败、one-click、manual-import、export、interrupt、recovery
- 依赖：Task 2 到 Task 8 全部合入。

## Parallel Dispatch Order

- 串行起手：`Task 0 -> Task 1`
- 第一批并行：
  - `Task 2` 后端任务可信
  - `Task 3` 桌面启动可信
  - `Task 5` 真实导入兼容
  - `Task 6` 默认规则包产品化
- 串行收口：
  - `Task 4` 导出重建语义
  - `Task 7` 容量与截断后端契约
  - `Task 8` 容量与错误桌面投影
  - `Task 9` 产品验收与交付文档

中央监督固定规则：
- 每张卡只发任务卡全文、允许写集当前内容、上游实际产出。
- 任一 worker 越权改文件、私保兼容层、跳过测试、擅改契约字段名，直接打回。
- 每卡都要求“先写失败测试，再写实现，再跑目标验证，再提交”。

## Test Plan

- 每张卡只跑自己的目标测试。
- 每批并行卡合并后跑一次相关子集回归。
- 最终验收固定跑：
  - `python3 -m unittest tests.test_source_registry tests.test_record_scope tests.test_progress_contract tests.test_record_identity tests.test_http_contract tests.test_streaming_store tests.test_streaming_export tests.test_app_service tests.test_app_backend -v`
  - `node --test desktop_app/layout_contract.test.js desktop_app/renderer/records.test.js desktop_app/renderer/exports.test.js desktop_app/renderer/tasks.test.js`
- Electron 手工验收必须包含：
  - backend ready 失败
  - runtime dependency 未就绪
  - one-click 创建成功/失败
  - manual-import 有效/无效目录
  - export rebuild
  - 中断与重启恢复

## Assumptions

- 产品继续是本地单机工具，不引入远程服务形态。
- 当前未提交的 3 个文档改动会在正式实施前整理进单独工作树或基线提交，避免污染后续任务分发。
- 本轮不交付多记录族 UI，但所有新骨架默认允许未来扩到 `deal`。
- 低智能子代理只负责单卡执行；跨卡协调、审边界、审契约、合并与最终验收都由中央监督者负责。
