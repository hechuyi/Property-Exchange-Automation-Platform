# 桌面端单一工作区与单指令执行模型设计

## 背景

当前桌面端的主要问题不是某几个控件文案，而是三层结构同时失真。

第一，业务数据被拆到了两个根目录：数据库、raw、缓存、日志在 `App Support`，归档和导出在 `Documents`。这直接导致“归档目录空了但数据还在另一处”的错误感知，也让用户无法把系统理解成一个完整工作区。

第二，`一键执行` 的后端仍然建立在旧的 split-plan / refresh-backfill 编排上。用户看到的是一条业务动作，但后端实际会拆成多阶段甚至多轮扫描，这既浪费时间，也让状态展示天然失真。

第三，桌面端 UI 目前仍在直接消费工程事件。`renderer.js` 同时承担 API 调用、轮询、状态翻译、视图渲染和事件绑定，导致“当前处理状态”和“真实后端状态”经常漂移。

## 目标

1. 所有业务数据和运行文件默认都落在同一个工作区根目录下，macOS 与 Windows 都以用户文档目录为默认根。
2. 首页只保留一个日期范围驱动的一键执行入口，不再保留独立下载网页入口，也不再保留“昨日遗漏”的独立后台语义。
3. 下载链路改成“先完成任务收集，再统一执行下载与入库”，不再使用 split-plan / refresh-backfill。
4. 总览、最近任务、数据记录、导出、待补映射全部基于同一套任务账本和业务状态模型。
5. 前端按职责拆分状态与渲染边界，避免轮询覆盖输入、状态拼接错误和页面联动混乱。

## 非目标

1. 不新增交易所站点或新的解析规则。
2. 不重写 downloader 站点适配实现本身。
3. 不引入新的前端框架；仍使用 Electron + vanilla JS。

## 设计决策

### 1. 单一工作区根目录

桌面端默认工作区根目录统一为用户文档目录下的 `PEAP`：

- macOS: `~/Documents/PEAP`
- Windows: `<Documents>\\PEAP`
- Linux: `~/Documents/PEAP`

根目录下统一放置：

- `data/streaming_ingest.sqlite3`
- `data/raw/auto`
- `data/raw/manual`
- `submission`
- `exports`
- `logs`
- `cache`
- `cache/ms-playwright`

`App Support` / `LocalAppData` 不再作为默认业务根目录。若旧版本数据仍在老路径，应用启动时做一次性迁移合并：优先迁移数据库、raw、日志、缓存；归档和导出目录若已在新根则直接复用，不重复复制。

### 2. 单一执行入口

首页只保留一个“一键执行”动作，参数为：

- 日期区间
- 交易所
- 业务类型
- 并发

默认日期区间初始化为当天到当天，并在界面上显式展示“今天是 YYYY-MM-DD”。用户修改日期范围后，后台只执行该范围，不再附加“昨日遗漏”“refresh backfill”或独立补录阶段。

### 3. 单指令执行模型

`一键执行` 的内部流程固定为四段：

1. 收集任务
2. 下载并保存网页
3. 解析入库并归档
4. 汇总结果

其中第 1 段必须先对所有匹配任务完成 list-only 收集，拿到候选条目集合后，再进入第 2 段统一下载；不允许边扫描边跨任务下载，也不允许按天自动拆块重扫。split-plan、refresh-backfill 和 chunk-state 仍可保留给 CLI，但桌面端主路径不再依赖它们。

### 4. 统一状态账本

任务账本继续使用 `jobs + job_events + records` 三张核心表，但阶段事件必须改成稳定的业务语义：

- `prepare_tasks`: 正在扫描网页
- `save_pages`: 正在保存网页
- `archive_pending`: 正在存档
- `exporting`: 正在导出 Excel
- `skipped`: 已跳过
- `failed`: 处理失败

收集阶段事件需要携带：

- 当前对象标签，例如“北交所 - 挂牌房屋土地”
- 当前任务序号 / 总任务数
- 当前阶段百分比
- 已发现候选数

下载阶段事件需要携带：

- 当前对象标签
- 当前任务序号 / 总任务数
- 当前阶段百分比
- 累计已保存网页数
- 累计详情抓取数

归档阶段不单独起 worker 事件流，而由 `downloaded_count / persisted_count / skipped_count / exception_count` 反推出待归档数。任务结束后状态立即冻结，不允许保留旧的 running 事件误导总览。

### 5. 导出与记录视图

手动导出面向业务人员，默认按区间重建导出结果，不再默认走“增量游标”。无可导出记录时返回中文阻断原因，例如：

- 当前条件下没有可导出的记录
- 当前条件下没有可导出的记录；待补映射 N 条
- 当前条件下没有可导出的记录；已跳过 N 条

数据记录页先按业务类型切换，再展示对应字段列，交易所统一显示为“北交所 / 上交所 / 天交所 / 重交所”。记录、导出和总览都共享同一套归一化日期和字段口径。

### 6. 映射补录

映射页维持三块：

- 待补映射
- 导入后的批量草稿
- 已保存规则

“一键导入待补项”只生成长草稿列表，用户编辑后统一保存；保存前做去重和冲突说明。四类规则继续显式区分：

- 转让方 -> 集团
- 转让方 -> 类型
- 集团 -> 集团
- 集团 -> 类型

### 7. 前端职责边界

前端保持 vanilla JS，但按职责拆分为：

- API 访问层
- 轮询与内存态存储
- 业务 view-model 组装
- 面板渲染器
- 事件绑定

不引入新框架，但必须把“状态计算”和“DOM 拼接”分开，避免继续由一个千行脚本同时负责所有行为。

## 迁移策略

1. 读取当前默认新根目录。
2. 若检测到旧 `App Support` / `LocalAppData` 根目录中的 `data`、`cache`、`logs`，则合并迁移到新根。
3. 若旧 `Documents/PEAP` 已存在 `submission` 或 `exports`，直接沿用。
4. 若目标目录已存在同名内容，以目标为准，跳过覆盖。
5. 迁移结果写入 audit log，便于追查。

## 受影响模块

- `desktop_backend/app_config.py`
- `desktop_backend/app_service.py`
- `desktop_backend/app_backend.py`
- `peap/download_oneclick.py`
- `peap/download_runner.py`
- `peap/streaming_daily_pipeline.py`
- `peap/streaming_store.py`
- `desktop_app/main.js`
- `desktop_app/backend_launch.js`
- `desktop_app/index.html`
- `desktop_app/renderer.js`
- `desktop_app/styles.css`
- `tests/test_app_config.py`
- `tests/test_app_service.py`
- `tests/test_download_oneclick.py`
- `tests/test_streaming_daily_pipeline.py`

## 验证策略

1. 用配置测试锁定单一工作区路径与旧目录迁移。
2. 用下载编排测试锁定“先收集、后执行”的单指令模型，不再出现 stage 3 refresh。
3. 用服务层测试锁定一键执行不再自动补录昨日遗漏，也不再自动导出。
4. 用总览测试锁定扫描对象、阶段百分比和归档待处理计数。
5. 用 Electron 真机手工验收确认工作区目录、归档图片、导出文件和映射交互符合预期。
