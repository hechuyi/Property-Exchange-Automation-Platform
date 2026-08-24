# 业务网页注册规范

本文是 PEAP 中"注册一个新业务网页"的强制规范——后续无论新增哪个交易所 / 哪个业务页 / 哪个记录族，都应先按本文完成注册设计，再落代码。

注册不是只把页面地址接进下载器，而是把它完整纳入产品主链：source metadata、product profile、download task、downloader / parser adapter、canonical record contract、postprocess / mapping scope、desktop query scope、export contract、tests 与 operator-visible documentation。

## 1. 适用范围

适用：

- 新增某交易所的新业务网页解析路径
- 为现有交易所新增新的列表页或详情页变体
- 为现有 source 新增新的 `business_id` 业务子类
- 新增新的 `record_family`
- 为某个 source 新增新的 operator-facing 产品接入面

不适用：

- 纯前端样式调整
- 单纯修复已有 parser 的字段 bug
- 不改变运行时注册边界的测试夹具补充
- 发布排期 / 分阶段 rollout / 工时估算

## 2. 规范词

- **MUST**：强制要求，不满足视为不合格
- **SHOULD**：推荐要求，不满足时提交说明必须解释原因
- **MAY**：可选项

## 3. 注册对象模型

注册新业务网页时，必须先把以下维度拆开，不允许混写：

- `source_id`：交易场所或站点身份（例如 `sse`、`cbex`）
- `record_family`：记录族（例如 `listing`、`deal`）
- `business_id`：canonical business 标识；必须在 business catalog 内接受 family-aware 校验。`record_family` 仍是路由真相；为避免历史 listing/deal 业务名冲突，允许使用 family-scoped 或前缀化 token（例如当前 `deal_*`），但调用方不得只靠 `business_id` 推断 `record_family`，也不得依赖 frontend/backend 各自的常量 truth table
- `project_type`：仅限 listing 族历史兼容层仍可能出现的旧字段名；新注册不得当成运行时 canonical 业务维度
- `product_profile`：对操作员暴露的产品切片（例如当前的 `desktop_listing`）
- `job_type`：运行任务类型（例如 `download_ingest`）
- `adapter_key` / `downloader_key`：解析与下载实现绑定点

禁止做法：

- 用历史兼容 `project_type` 偷偷编码 `record_family`
- 调用方只靠 `business_id` 反推 `record_family`，绕过 catalog 的 family-aware 校验
- 用历史兼容 `project_type` 表达"挂牌业务"与"成交业务"的族差异
- 用 parser 分支名替代 source identity
- 用 UI 分支逻辑代替 parser 规范化
- 在运行时动态注册 source
- 在 frontend/backend 分别维护业务路由常量 truth table

## 4. 总体原则

### 4.1 Catalog 不可运行时变异

所有 source / family / business 元数据 MUST 收敛到共享 catalog：

- `peap_core/source_catalog.py`：source identity 与真实 family 支持
- `peap_core/family_catalog.py`：family identity、默认 profile、`family.source_ids`
- `peap_core/business_catalog.py`：business identity、别名与 family membership
- `peap/source_registry.py` 只是 facade，不是注册入口

禁止 `register_source(...)` 运行时追加 source。新 source 或能力变更必须改 catalog。任何业务路由 truth table 都必须收敛到共享 catalog，不允许 frontend/backend 复制一份常量映射表作为稳态依据。

`/api/catalog` 的 `visible_families` 是 source-backed visibility——backend 先从启用 source 的 `supported_record_families` 计算候选 family，再与 family catalog 和 business catalog 交叉。单独新增 `FamilyDescriptor`、单独填写 `family.source_ids`、单独注册 business descriptor、或只在前端加选项，都不能让 family 进入当前可操作产品面。`support_matrix` 与 `surface_source_matrix` 也只能发布 source-backed family。

### 4.2 Canonical contract 优先于页面差异

只要两个页面属于同一业务语义，parser MUST 先把它们收敛到同一规范化输出，再让 ingest/store/UI/export 消费。

- 不允许为单一页面变体在 UI 层硬编码字段分支
- 不允许 export 直接吃 raw parser payload
- 不允许把"页面差异"直接暴露为 operator-facing contract

### 4.3 增量式

注册新业务网页时 MUST 保持已上线业务面稳定：

- 新能力优先通过显式新增 descriptor / profile / branch 落地
- 不允许为接入新 family 隐式改变现有 listing 行为
- listing 已稳定的导出头、筛选语义、状态语义不能被旁路破坏

### 4.4 普通读路径保持无副作用

- 历史数据修复走 `peap/streaming_store_maintenance.py`
- 查询接口只读
- 兼容修复必须有显式维护入口和可审计 summary

### 4.5 不支持必须显式失败

某业务网页已支持下载/入库但尚未支持导出或 UI 展示时，MUST 返回显式、可归因的 unsupported contract：

- 不允许静默回退到 listing 逻辑
- 不允许伪造 listing 列名兼容非 listing 记录
- 不允许通过空结果掩盖未接通路径

## 5. 注册判定规则

### 5.1 何时复用现有 `source_id`

同时满足：

- 仍属于同一交易场所身份
- 仍属于同一 `record_family`
- 只是新增路由、DOM 结构或列表/详情页变体
- 下载生命周期与任务归组方式没有本质变化

通常只需扩展 `peap/download_tasks.py`、对应 downloader、对应 parser adapter / fixture。

### 5.2 何时新增 `source_id`

满足任一条件 MUST 评估新增（默认倾向新增）：

- 站点身份或业务标识语义已变化
- 与现有 source 共享名称，但任务生命周期完全不同
- parser / downloader 绑定面已不适合作为同一 descriptor 管理
- 支持的 `record_family` 集合明显不同

新增 source 的首要改动 MUST 是 `peap_core/source_catalog.py`。

### 5.3 何时新增 `record_family`

满足任一条件 MUST 定义独立 family，不能伪装成新 `business_id`：

- 记录核心业务语义已不是"挂牌类"
- 规范化字段集合与 readiness 规则显著不同
- UI 主列、筛选语义、导出表头不再适用 listing 体系
- 后处理规则适用范围与 listing 差异显著

简单说：`股权转让 / 实物资产 / 增资扩股 / 预披露` 都是 `listing` 族内部的 `business_id`，不是未来成交业务的上位分类；后续若新增成交业务，默认先从 `record_family` 维度建模。

### 5.4 何时新增 `product_profile`

满足任一条件 SHOULD 新增：

- 新业务面将作为独立 operator-facing 产品入口暴露
- 新 family 的默认 source 集合 / 导出策略 / 后处理策略不同
- 继续复用现有 profile 会让 listing 与新 family 语义混杂

## 6. 强制注册项

### 6.1 业务准入单（必须）

提交说明 MUST 先给一份业务准入单，至少包含：

- 页面名称
- 所属交易所 / 站点
- 申请复用还是新增 `source_id`
- 申请复用还是新增 `record_family`
- 所属 `business_id` 或新业务子类
- operator-facing `product_profile`
- 列表页入口、详情页路由、分页方式
- 稳定业务主键字段
- 页面上可提取的日期 / 价格 / 主体字段
- 是否存在验证码、登录态或反爬前提
- 成功路径、跳过路径、失败路径的显式语义
- 是否要求导出，若不导出则 unsupported contract 是什么

无准入单的注册视为不合格。

### 6.2 Source 层注册

MUST：

- 在 `peap_core/source_catalog.py` 声明 `source_id`
- 声明 `canonical_label`、`site_label`、`aliases`
- 声明真实已接通的 `supported_record_families`
- 与 `peap_core/family_catalog.py` 中对应 `family.source_ids` 完全一致
- 通过 `peap/business_runtime.py` 绑定 source / family / business 的可执行下载能力

约束：

- `supported_record_families` 必须是事实能力，不允许预告未来
- 不允许把尚未接通的 family 先写进 catalog 占位
- `aliases` 只做 identity 解析，不做业务逻辑开关
- 仅改一边（family 或 source）都是不完整注册

### 6.3 Product profile 注册

MUST 明确：

- 该网页属于哪个 `product_profile`
- profile 对应哪个 `record_family`
- profile 暴露哪些 `source_ids`
- 使用哪个 `postprocess_profile`
- 使用哪个 `export_profile`
- 使用哪个 `readiness_policy`

若现有 profile 不足，必须修改 `peap/product_profile.py` 显式新增；不允许把多种业务面合并进 `desktop_listing`。

### 6.4 Download task 注册

MUST 先在 `peap/business_runtime.py` 声明 source / family / business binding，再由 `peap/download_tasks.py` 组装任务：

- 任务身份可稳定推导
- manifest list endpoint / detail route 明确
- page size 有显式配置来源
- capability 与真实下载能力一致
- display name 来自 source metadata，不硬编码散落字符串

额外：

- 当前 registry 仍保留 listing-first 历史结构痕迹；引入新 `record_family` MUST 先把 task assembly 改造成 family-aware
- 同一业务网页的 list/detail 抓取职责与 parser 职责必须分离

### 6.5 Downloader / Parser 注册

MUST：

- downloader 负责发现候选项、抓取页面、保留证据路径
- parser 负责把页面内容规范化为稳定字段
- 同一业务语义的多个页面变体，parser 输出键名必须一致
- download task 的 `business_id` 只是在页面事实缺失时使用的路由提示；明确的官方详情路由与 parser 从当前证据页解析出的业务类型优先。归档文件落错旧任务目录时，不得用 task hint 覆盖页面事实
- source classification 模糊时必须显式失败，不能猜测归类
- `parser_payload` 保留证据，不作为 downstream 契约兜底

禁止：

- parser 直接决定 UI 列头
- downloader 直接输出 export-ready payload
- 用页面原字段名作为长期稳定契约而不提供 canonical 对应关系

### 6.6 Canonical / Store 注册

任何新业务网页入库前 MUST 先定义 canonical contract：

- 该记录的 `record_family`
- `canonical_record.business_identity` 由哪些字段组成
- `canonical_record.canonical_fields` 的最小必填集
- `source_identity` 如何保留来源证据
- 哪些字段只保留在 `parser_payload` / `postprocess_payload`

事实边界：

- `peap/streaming_models.py` 中 `RecordFamily` 已有 `listing`、`deal`
- `peap/streaming_export.py` 已支持 `listing` 与 `deal`；`deal` workbook 合同按业务类型和来源区分 sheet 名称与表头，不能把成交导出退化成挂牌导出。成交 workbook 的“备注”列是正式审计列，用于 `collection_date` 补日期等可审计场景，不是临时 UI 字段
- `canonical_record` / `canonical_projection` 是 downstream 唯一可信输出面

新增 family 时 MUST 在提交前写明 family 的 canonical 最小字段集、readiness 条件以及显式 unsupported 范围。

deal family 当前已落地的额外业务合同：

**成交日期字段语义**：`deal_date` 是真实成交日，`collection_date` 是采集/获取日。两者语义独立，不得互相覆盖。导出时若 `deal_date` 缺失（`deal_date_basis == "collection_date"`），允许将 `collection_date` 填入 workbook 成交日期列，并在"备注"列写入审计说明（如"此为采集日期"）；"备注"列是正式审计列，不是临时 UI 字段。下载阶段的日期范围过滤必须只对已知真实 `deal_date` 的记录应用日期窗口过滤；缺失真实成交日的记录不得因 `collection_date`（即运行当日）落在请求窗口之内/之外而被过滤，应保守保留、交由 postprocess/export 审计。

**增资成交项目级字段穿透**：parser 产出的标准英文字段 `capital_company_name`、`total_investment_amount`、`holding_ratio` 经 `STANDARD_TO_COMPAT`（`pipeline_payload_projection.py`）映射为中文 compat 键 `增资企业名称`、`投资总金额（万元）`、`持股占比`，再通过 `BASE_FIELD_CANDIDATES`（`output_contract.py`）进入 deal capital workbook 对应列。新增增资成交项目级字段时，MUST 同步维护这两层映射，不允许只在 parser 产出而在导出端丢失。

**增资成交 readiness 判定**：deal capital increase 记录进入 `ready` 状态的前提条件是至少存在一条非汇总（non-summary）投资方记录，且该记录同时包含非空投资方名称和非空投资金额。仅有汇总行名称（如"总计"、"合计"）或仅有名称缺金额，均不满足 readiness 条件，应产生 `deal_capital_increase_missing_investor_amount` finding，进入 `pending_review` 状态，不允许进入导出流。

### 6.7 Postprocess / Mapping 注册

postprocess 与 mapping 规则 MUST 按 family 和业务语义显式划定适用范围：

- listing 专属规则不能自动外溢到新 family
- optional rules 必须可解释、可审计
- 规则命中后引发的 state 变化必须有统一分类语义
- 历史兼容回填走 maintenance，而不是查询时偷偷补算

### 6.8 Desktop / API 注册

新业务网页出现在桌面产品时 MUST 同步定义：

- `/api/catalog` 的 source-backed visibility、`support_matrix` 与 default scope 行为
- `desktop_backend/record_scope.py` 的 family 解析与筛选语义
- `desktop_backend/app_service.py` 的列表列集、状态标签、筛选行为
- frontend 记录页的列定义、筛选项、空态和错误态

约束：

- `business_id="all"` 的含义按 family 解释
- UI 主列不能复用错误 family 的字段语义
- 前端不能通过轮询补偿后端 contract 不完整

### 6.9 Export 注册

要求导出时 MUST 同步定义：

- output kind
- 表头集合
- family-specific required fields
- 导出时的 ready / skipped / unsupported 判定

约束：

- `peap/streaming_export.py` 只能消费 canonical 数据
- 不允许 raw payload fallback
- 非 listing family 不允许套用 listing 表头

若暂不支持导出，MUST 返回 typed unsupported，不允许静默跳过。

### 6.10 测试与文档

每个新业务网页注册 MUST 至少补齐：

- source / family / business catalog alignment 测试
- `peap/business_runtime.py` binding 与 task registry 测试
- downloader / task registry 测试
- parser fixture 测试
- ingest / store 测试
- API scope 测试

涉及 operator-visible 变化时还必须补：

- frontend contract 测试
- export 测试
- failure / unsupported 路径测试

文档：

- 本规范对应的注册单
- operator-facing 能力变化更新 `docs/operations.md`
- 新的 source / family / business / profile 边界更新 `docs/architecture.md`
- 新的公开 route / payload / public resource 更新 `docs/api.md`
- 新的自动化基线或活跃文档集合更新 `docs/release-gate.md`

根目录与正式文档树都不允许重新引入 `PLAN.md`、`todo.md`、过程性 review note、AI handoff、临时调查记录。

## 7. 推荐实施顺序

1. 决定复用还是新增 `source_id` / `record_family` / `business_id`，填注册单
2. 改 `peap_core/source_catalog.py`、`peap_core/family_catalog.py`、`peap_core/business_catalog.py`，再改 `peap/business_runtime.py`
3. 落 downloader / parser / fixture，让页面差异先在 canonical 输入层被吸收
4. 补 canonical / store / postprocess / export / unsupported contract
5. 接 desktop / API / frontend；前端只消费 backend 已发布的 catalog 与 contract，不抢跑
6. 补测试与文档

## 8. 最小交付矩阵

### 8.1 同 source、同 family、仅新增页面变体

通常只改：`peap/download_tasks.py`、对应 downloader、对应 parser / adapter、测试夹具与 ingest 回归。

不改：`peap/product_profile.py`、`desktop_backend/record_scope.py`、`peap/streaming_export.py`（前提是 canonical contract 未变化）。

### 8.2 新 source、已有 family

通常改：`peap_core/source_catalog.py`、`peap/product_profile.py` 中 source 归属、`peap/download_tasks.py`、对应 downloader / parser / tests。

### 8.3 新 `record_family`

必须覆盖：`peap_core/source_catalog.py`、`peap/product_profile.py`、`peap/download_tasks.py`、`peap/streaming_ingest.py`、`peap/streaming_store.py` 与维护逻辑、`desktop_backend/record_scope.py`、`desktop_backend/app_service.py`、frontend records surface、`peap/streaming_export.py` 或显式 unsupported contract、端到端测试。

任一层仍只保留 listing 历史兼容形态时，新 family 不能宣称"已完成注册"。

## 9. 注册单模板

### 9.1 基本信息

- 页面名称：
- 站点 / 交易所：
- 页面类型：列表页 / 详情页 / 混合页
- 业务目标：

### 9.2 身份与分类

- 复用或新增 `source_id`：
- `record_family`：
- `business_id` 或业务子类：
- `product_profile`：
- `job_type`：

### 9.3 下载与解析

- list endpoint：
- detail route：
- 分页方式：
- downloader class：
- adapter key / parser module：
- 稳定外部主键：
- 页面证据保留策略：

### 9.4 Canonical 契约

- `business_identity` 组成字段：
- `canonical_fields` 最小必填集：
- family-specific readiness 条件：
- 只保留在 raw payload 的字段：

### 9.5 后处理与导出

- 适用的 postprocess profile：
- 适用的 optional rules：
- 是否支持导出：
- 若支持，output kind / 表头：
- 若不支持，typed unsupported contract：

### 9.6 UI 与验证

- records 页面列集：
- 筛选项：
- 状态标签：
- 必要测试：
- 回滚边界：

## 10. 审核清单

注册通过的所有问题必须回答"是"：

- 明确区分了 `source_id` / `record_family` / `business_id` / `product_profile`？
- 避免了运行时动态注册 source？
- 先定义 canonical contract，再接 UI / export？
- 没把新 family 伪装成 listing 子类？
- 没让 export 走 raw payload fallback？
- 没把兼容修复塞回读路径？
- 给出了 typed unsupported 行为？
- 补齐了 task / parser / ingest / API 的最小测试？
- 明确旧有 listing 行为不受影响？

## 11. 拒收条件

任一情况视为不合格：

- 通过运行时 `register_source(...)` 注入 source
- 通过 UI 特判弥补 parser 未规范化输出
- 把非 listing family 导出成 listing 表头
- 调用方只靠 `business_id` 反推出 `record_family`，或把 `record_family` 偷藏进历史兼容 `project_type`
- 让普通查询路径承担历史兼容修复
- 用空结果或静默 fallback 掩盖 unsupported 路径
- 未定义 canonical 最小字段集就直接入库
- 宣称 source 支持某 family，但下载 / 解析 / 查询任一链路未接通

## 12. 当前仓库的直接约束

基于当前主线代码，注册实现还要注意：

- `peap_core/source_catalog.py` 是唯一 canonical source metadata
- `peap_core/family_catalog.py` 与 `peap_core/business_catalog.py` 是 family / business metadata 真相源
- `peap/business_runtime.py` 是 source / family / business 可执行下载绑定层
- `peap/product_profile.py` 当前默认 profile 仍是 `desktop_listing`
- `peap/download_tasks.py` 当前 registry 仍保留 listing-first 历史结构痕迹
- `desktop_backend/record_scope.py` 仍有面向 listing 的兼容解析路径
- `peap/streaming_export.py` 仍有 listing 导出兼容路径

涉及新 `record_family` 的注册不能只改 parser 或下载器，必须把 family-aware 边界一起补齐。

deal family 当前已接通的 scope 边界：

- 股权转让成交（`deal_equity_transfer`）：SSE、CBEX、TPRE、CQUAE 均已接通下载/入库/导出
- 增资扩股成交（`deal_capital_increase`）：SSE、CBEX、TPRE、CQUAE 均已接通下载/入库/导出
- 实物资产成交（`deal_physical_asset`）：SSE、CBEX 已接通；TPRE、CQUAE 实物资产成交**不在当前支持范围**，不得出现在 `support_matrix` / `surface_source_matrix` 的可操作项中，接入前须显式评估并更新 catalog

backend 内部 token（`business_id`、standard field 名）保持英文，保证编码稳定性；前端展示 label 和 workbook 列头严格以 business catalog 通过 `/api/catalog` 发布的 label 为准，不得在 frontend / export 层另维护一套映射常量覆盖 catalog。
