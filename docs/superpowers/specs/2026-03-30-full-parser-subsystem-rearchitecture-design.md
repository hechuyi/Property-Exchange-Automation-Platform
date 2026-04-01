# 全量解析子系统重构设计

## 背景

当前仓库里的解析主链仍然是“保存快照文件 -> 识别交易所 -> 选 parser -> 站点内抽字段 -> fallback / postprocess -> ingest / store / export”。这条链路在功能上能跑，但结构上把三类本应独立演化的复杂性压扁到了同一层里：页面结构理解、跨页业务对象组装、以及面向下游的兼容投影。

这种耦合已经在代码里有了明确表征。`peap/parsing.py` 同时持有来源识别、parser 路由、guard、路径兜底和后置补救入口；`peap_parsers/base.py` 的输出契约只有松散的 payload，缺少 provenance、typed diagnostics 与 recoverability；`peap/streaming_ingest.py` 仍是单快照 ingest 链路；`peap/streaming_postprocess.py` 则在记录级合并和规则修正之间来回穿透。只要这几个边界不拆开，新增站点、模板分叉、补充公告页、多页拼装、导出变体都会继续把条件分支摊平到 orchestrator、postprocess、store 和 export。

激进版方案的目标不是“把 parser 写得更优雅”，而是把解析从一个函数链升级成一个独立子系统，让不同层面的变化在不同层里闭合。

## 目标

1. 把 crawler/frontier、单页解析、跨页组装、规范化输出和下游投影拆成独立边界。
2. 让“快照”成为一等公民，使解析结果可回放、可缓存、可审计、可重算。
3. 用强类型契约替代 parser 直接吐散装 dict 的模式，让事实、诊断、证据、恢复能力和版本信息一起进入主链。
4. 正式引入 `Record Assembler / Correlator`，把跨页业务对象拼装从外围系统的隐式职责变成显式层。
5. 建立 `Normalizer / Anti-Corruption Layer`，保证外部站点语义不会直接渗透到数据库、导出和 UI。
6. 把所有“fallback”收紧成显式 `policy / enricher / recovery`，禁止无审计的静默覆写。
7. 为后续站点扩张、模板变更和政策规则演化提供稳定演进点。

## 非目标

1. 首轮重构不新增新交易所适配，也不扩展新的业务类型站点。
2. 不在第一阶段追求全量高保真 DOM 子树级 provenance 存储；默认只要求轻量证据引用。
3. 不把 parser family 变成可由外部配置文件完全驱动的通用规则引擎。
4. 不要求一次性删除所有旧入口；可以保留兼容 facade，但 facade 只能做适配，不能继续长新逻辑。
5. 不把 crawler/frontier 的每个策略细节都抽象成框架；首轮只做稳定契约和运行时接缝。

## 顶层架构

全量解析子系统的目标拓扑如下：

```text
Crawler / Frontier
  -> Snapshot Store
  -> Document Decoder
  -> Source Classifier
  -> Page Parser Family
  -> Record Assembler / Correlator
  -> Normalizer / Anti-Corruption Layer
  -> Policy Engine
  -> Sinks
```

这里有三条不可退让的硬边界。

第一，抓取与解析分离。crawler/frontier 负责 URL 发现、排队、调度、保存快照和维护抓取元信息；它不理解 DOM，也不负责字段抽取。它输出的是快照及其抓取上下文，而不是业务记录。

第二，页级解析与对象级组装分离。page parser 只回答“这一页说了什么”，assembler 才回答“这些页合起来是不是同一个业务对象、现在是否足够产出候选记录”。跨页拼装不能继续寄生在 store 主键、人工补丁或导出前 merge 上。

第三，内部对象与外部契约分离。normalizer/ACL 负责把来自异构页面的事实收敛到稳定 canonical schema；Excel、SQLite、HTTP API、前端表格都只能依赖这个稳定 schema 或其显式 projection，而不能再绕回原始 parser 字段。

## 层职责

### 1. Crawler / Frontier

该层负责：

- URL 发现与去重
- 请求调度、优先级和重试
- 列表页到详情页、详情页到附件页的后续页面发现
- 原始响应保存与抓取元信息记录

该层输出 `SnapshotEnvelope`，至少包含：

- `snapshot_id`
- `captured_at`
- `source_url`
- `referrer_url`
- `content_type`
- `http_status`
- `storage_path`
- `digest`
- `fetch_metadata`

`SnapshotEnvelope` 是后续所有可回放、可缓存、可审计能力的锚点。以后系统里任何“重新解析”“重建记录”“导出差异追踪”都应该回到 `snapshot_id`，而不是只依赖当前文件路径。

### 2. Snapshot Store

该层负责管理快照物理落点、命名冲突、内容寻址和归档策略。下载器保存页面时不再只想着“给 parser 一个文件路径”，而是要确保：

- 快照文件有稳定身份
- 同一抓取事件的关联文件可追踪
- 改名、归档、冲突解决后仍能回溯到原始抓取上下文

### 3. Document Decoder

decoder 负责把 HTML、MHTML、JSON、嵌入脚本块、附件引用等原始内容统一转成 `DecodedDocument`。它负责：

- 文本解码与编码识别
- DOM 构建
- MHTML 分片解析
- 内嵌 JSON 抽出
- 页面链接、iframe、附件链接解析
- 文档级元信息抽取

它不负责来源识别，也不负责字段级业务语义。

`DecodedDocument` 至少包含：

- `snapshot_id`
- `document_kind`
- `primary_text`
- `dom`
- `embedded_json`
- `links`
- `attachments`
- `metadata`
- `decoder_version`

### 4. Source Classifier

classifier 负责来源识别和页面类型识别。它不返回单个裸字符串，而是输出 `SourceMatch`：

- `source_id`
- `page_kind`
- `confidence`
- `status = matched | ambiguous | unknown`
- `reasons`
- `classifier_version`

这样来源识别就不再是“硬猜一个交易所然后继续往下跑”，而是显式表示“可识别、不可识别、候选冲突”三种状态。

### 5. Page Parser Family

每个 source family 只负责本来源的页面结构理解。它们内部可以保留 `standard / special / deal / mobile` 等 variant，但必须共享统一输出契约。

family 的输入是 `DecodedDocument + SourceMatch + ParserContext`，输出是 `PageParseResult`。该层的职责只有：

- 理解站点 DOM / JSON 结构
- 提取页级语义事实
- 形成页级身份线索
- 产生未来组装需要的外链和引用线索
- 记录 typed diagnostics 与轻量 evidence refs

它不直接产出最终导出 payload，也不直接知道数据库、Excel、前端表头。

### 6. Record Assembler / Correlator

这是激进版最重要的新层。assembler 输入多个 `PageParseResult`，输出 `AssembledRecordCandidate`。它负责：

- 定义对象主键与关联键
- 归并列表页、详情页、公告页、补充附件页
- 维护对象生命周期
- 决定对象何时达到 “sufficient for normalization”
- 产出缺失页面、冲突页面和等待页面的 typed state

assembler 不做 DOM/XPath，不做数值规范化，不做站点专有字符串修理。它是纯对象语义层。

### 7. Normalizer / Anti-Corruption Layer

normalizer 把 assembler 的内部对象转换成稳定的 `CanonicalRecord`。它的职责是：

- 统一日期、金额、状态、项目类型、来源字段
- 消化外部站点差异
- 形成系统内部唯一可依赖的业务对象模型
- 明确哪些字段是系统内部事实，哪些只是来源投影

该层是外部异构页面与内部系统模型之间的反腐层。SQLite、导出、服务层 API 都只能依赖这里的结果。

### 8. Policy Engine

所有“后置补全、规则修正、人工映射、恢复策略、过滤决议”都进入 policy engine。它的执行单位是 canonical record，而不是原始 parser payload。

每个 policy 必须显式声明：

- 适用条件
- 读取字段
- 可写字段
- 优先级
- 冲突策略
- 诊断输出
- patch 审计

默认规则是：禁止静默覆写已确认字段。只有字段为空、原值低置信度，或策略显式允许降级修复时，policy 才能写入。

### 9. Sinks

sinks 负责：

- 写入 SQLite / revision store
- 生成 export artifacts
- 投影为 UI/API 视图模型
- 维护 replay / audit / compare outputs

compat/export 只允许作为 canonical record 的显式投影存在，不能再反向规定 parser 输出形状。

## 核心契约

### SnapshotEnvelope

```text
snapshot_id
captured_at
source_url
referrer_url
content_type
http_status
storage_path
digest
fetch_metadata
```

### DecodedDocument

```text
snapshot_id
document_kind
dom
primary_text
embedded_json[]
links[]
attachments[]
metadata{}
decoder_version
```

### Diagnostic

```text
severity = info | warn | error
type
message
stage = decode | classify | parse | assemble | normalize | policy
evidence_refs[]
recoverability = none | partial | recoverable | unrecoverable
```

### EvidenceRef

```text
snapshot_id
source_kind = dom | json | meta | url | derived
locator
excerpt
transform_ids[]
confidence
```

### PageParseResult

```text
snapshot_id
source_match
parser_family_id
parser_family_version
variant_id
variant_version
page_identity
facts[]
outgoing_refs[]
diagnostics[]
provenance[]
recoverability
```

`page_identity` 至少包含：

- `page_kind`
- `project_code`
- `project_id`
- `page_url`
- `listing_date`
- `candidate_tokens`

`outgoing_refs` 至少支持：

- `target_kind = detail | announcement | attachment | listing | related`
- `target_url`
- `ref_reason`
- `correlation_hints`

### AssembledRecordCandidate

```text
assembly_id
source_ids[]
page_results[]
entity_keys[]
completion_state = partial | sufficient | conflicted | blocked
missing_requirements[]
assembly_diagnostics[]
raw_business_object
```

### CanonicalRecord

```text
record_id
record_family
source_identity
business_identity
canonical_fields{}
field_provenance{}
diagnostics[]
normalizer_version
policy_state
```

## 失败语义

激进版必须把失败从异常字符串提升为 taxonomy。最少要有以下层级：

- `decode_failed`
- `source_unknown`
- `source_ambiguous`
- `parse_unrecoverable`
- `parse_partial`
- `assembly_blocked`
- `assembly_conflict`
- `normalize_invalid`
- `policy_conflict`

每一类失败都必须说明：

- 发生阶段
- 是否允许后续重试
- 是否需要更多页面
- 是否可由 policy 修复
- 是否应该进入人工队列

## 缓存、可回放与版本

缓存键不再只依赖输入文件时间戳或 parser 代码签名，而是依赖完整运行时：

- `snapshot_digest`
- `decoder_version`
- `classifier_version`
- `parser_family_version`
- `variant_version`
- `assembler_version`
- `normalizer_version`
- `enabled_policy_set_digest`

这样同一张快照在不同运行时下的解析产物才真正可解释。任何行为变化都必须决定自己是否触发重算。

当前实现补充说明：

- 下载/一键流水线回调已经开始把 snapshot 级元数据向 ingest 侧透传，至少覆盖 `source_url` 与 snapshot digest/id 这类 replay 上下文。
- `desktop_backend.app_service` 的 reprocess/replay 入口会优先复用 store 中已持久化的 `source_url`、snapshot id / digest，而不是只从当前 parser payload 反推。
- parse cache 的运行签名现在显式包含 decoder / classifier / family / variant / assembler / normalizer / policy 版本片段；旧式仅靠 parser 文件签名的做法已不足以表达行为变化。

## 兼容与迁移

这次重构不能一口气删除现有入口，因此需要保留两层兼容：

第一，`peap/parsing.py` 暂时保留 `parse_file()` facade，但内部改为调用新子系统，并把 `PageParseResult -> CanonicalRecord -> CompatProjection` 适配回旧返回形状。旧入口只能当适配器，不允许继续附加站点分支。

第二，`streaming_ingest`、`streaming_store`、`streaming_export` 暂时接受兼容投影，但内部尽快改为以 canonical record 和 policy audit 为主数据，兼容 payload 只在落地投影和导出层存在。

## 迁移原则

1. 先立共享契约，再重写运行时。
2. 先把页级结果做强，再引入 assembler。
3. 先保留兼容 facade，再逐步把下游从 compat payload 迁出。
4. 任何一层迁移都要有对应 contract tests、golden fixtures 和 replay tests。
5. 所有默认规则、policy 集和版本签名都必须进入 repo，可审计、可重放、可比较。

## 验证策略

1. decoder 层用 HTML/MHTML/JSON fixture corpus 做 golden tests。
2. classifier 层做 matched / ambiguous / unknown contract tests。
3. parser family 层做 variant selection tests、facts extraction tests、failure taxonomy tests。
4. assembler 层做跨页关联测试、缺页测试、冲突测试、生命周期测试。
5. normalizer 层做 invariants tests，锁定日期、金额、状态和项目类型统一规则。
6. policy 层做 overwrite contract tests，确保默认不静默覆写高置信度字段。
7. 全链路做 replay tests，用固定 snapshot corpus 比较输出记录、diagnostics 与 evidence refs。

## 完成标准

只有以下条件同时成立，激进版重构才算真正收口：

1. 单页解析、跨页组装、规范化输出三条职责链在代码结构上彼此独立。
2. 任何一条记录都能追溯到快照、页面事实、组装决策、规范化和 policy patch。
3. 现有下载 ingest、手动导入、导出链路都已经通过兼容 facade 或直接新接口接到新子系统。
4. parser 新增站点或模板分叉时，只需要修改 classifier / family / assembler 局部，而不需要在 orchestrator、export 或 store 中追加隐藏分支。
