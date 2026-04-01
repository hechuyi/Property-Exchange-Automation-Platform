# 解析规则风险报告

## 风险边界

解析层仍然是桌面产品最敏感的变化面，因为页面结构波动、站点兼容差异、组装/规范化规则调整和后处理策略更新都会直接影响记录可导出状态。

## 当前主要风险

- 交易所页面结构变更导致 decoder/classifier/parser family 产出字段缺失。
- OTC 或特殊页面的身份字段不足，导致记录进入 typed failure / pending mapping，而不是 opaque parser exception。
- 跨页关联线索（`project_id`、`page_url`、candidate tokens、outgoing refs）不足时，assembler 可能只能得到 `partial` / `blocked` / `conflicted` 结果。
- canonical normalizer 或 policy engine 版本变化会改变导出前主数据，若 cache/replay 版本签名不完整，旧缓存可能污染新运行时。
- 解析成功但 canonical projection / compat projection 不匹配，最终会体现在 `record_state`、`findings`、导出结果或 replay 差异上。
- replay/reprocess 如果丢失已存储的 snapshot metadata（如 `source_url`、snapshot id/digest），后续定位和审计会退化为仅凭当前文件路径推断。

## 控制措施

- 通过 `tests/` 中的 snapshot、page-result、record-contract、registry、assembler、normalizer、policy、ingest、store、export、pipeline、service 契约测试锁定主链行为。
- 所有默认规则包、policy 版本和 parser runtime 版本签名都必须进入 repo，保证 cache invalidation 与 replay 可审计。
- 页面兼容与失败语义优先写入公共状态契约和 typed diagnostics，而不是零散地在页面逻辑中打补丁。
- `streaming_store` 持久化 source identity、canonical record、canonical projection，导出优先读取 canonical revision，而不是重新依赖 raw payload merge。
- replay/reprocess 必须优先复用已存储的证据路径与 snapshot metadata，避免因为当前路径变化或临时文件移动丢失来源上下文。
- 高风险改动后执行 targeted regression：parser contracts → ingest/store/export → download/replay/cache → app service replay/export。
