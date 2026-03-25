# 后处理引擎开发计划（重整版）

> 更新时间：2026-02-28
> 目标：在不破坏现有解析产线的前提下，逐步把“解析器里的后处理能力”剥离到 PPE（PostProcess Engine）。

## 1. 项目目标与边界

### 1.1 目标
1. PPE 独立运行：只读输入文件（Excel/CSV），输出处理结果与审计报告。
2. 后处理规则可插拔、可配置、可审计、可回溯。
3. 支持 `plan`（只审计）与 `apply`（落地修改）两种模式。
4. 迁移期间支持双轨：解析器 legacy 与 PPE 并行比对，可回滚。

### 1.2 非目标
1. 不重写各交易所 HTML/DOM 解析器。
2. 不在 PPE 内做网页抓取或模板识别。
3. 不在当前阶段做复杂机器学习匹配。
4. 不把 `-0`（预披露标识）映射为挂牌次数。

---

## 2. 当前状态（已完成）

### 2.1 PPE 骨架（M1）
1. CLI、配置加载（JSON/YAML）、适配器（Excel/CSV）、Canonical 模型已完成。
2. 规则注册器、规则执行管道、审计报告（summary/changes/conflicts/no_match/ambiguous/errors）已完成。
3. 独立入口与脚本已完成：`run_postprocess.py`；source-tree legacy CLI wrapper 已移除。

### 2.2 解析器迁移保障（开发期）
1. 已新增解析器 profile：`full` / `ppe_ready`。
2. 已新增 dual-run compare：可对比两 profile 的字段差异并输出 JSONL 报告。
3. 已验证可用于挂牌次数迁移观测。

### 2.3 规则实现进度
1. 已实现：
   - `R001_group_mapping_fill`
   - `R002_group_conflict_flag`
   - `R003_company_name_normalize`
   - `R004_normalize_values`（多转让方规范化）
   - `R005_normalize_source_type`（表驱动 + 规则推断 + 异常告警）
   - `R006_derive_listing_times`
   - `R007_required_field_check`
   - `R008_project_code_format_check`
   - `R009_consistency_validate`
   - `R010_filter_scrap_physical_asset`（报废实物资产过滤）

---

## 3. 统一数据契约

### 3.1 Canonical Record（最小字段）
1. `file_name`
2. `sheet_name`
3. `row_index`
4. `project_code`
5. `company_name_primary`
6. `group_name`
7. `raw_fields`

### 3.2 Rule Result
1. `patches`: `[{field, old_value, new_value, action, reason}]`
2. `findings`: `[{rule_id, severity, type, message, evidence}]`
3. `stop_processing`（可选）

### 3.3 Severity
1. `info`
2. `warn`
3. `error`

---

## 4. 规则与表单设计

### 4.1 集团与类别相关表单
1. `ppe_config/transferor_group_mapping_template.csv`
   - 表头：`transferor_name,group_name`
   - 用于 `R001/R002`（转让方 -> 集团）
2. `ppe_config/transferor_type_mapping_template.csv`
   - 表头：`transferor_name,source_type,notes`
   - 用于 `R005`（转让方 -> 类型）
3. `ppe_config/group_group_mapping_template.csv`
   - 表头：`group_name,parent_group_name,notes`
   - 用于 `R005`（集团 -> 母集团）
4. `ppe_config/group_type_mapping_template.csv`
   - 表头：`group_name,source_type,notes`
   - 用于 `R005`（集团 -> 类型）

### 4.2 类型判定策略（R005）
1. 优先级：`transferor_type_mapping` > `group_type_mapping` > 关键词推断。
2. 冲突策略：
   - 默认：`keep_original_and_flag`
   - 可选：`prefer_mapping`
3. 市属判定采用保守策略，降低误判。

### 4.3 异常机制（R005）
1. `source_type_conflict`
2. `entity_type_mapping_ambiguous`
3. `entity_type_no_match`
4. `ministry_missing`
5. `ministry_ambiguous`
6. `ministry_conflict`

### 4.4 多转让方治理（R004）
1. 统一分隔符（标准输出为 `；`）
2. 去重（同名去重）
3. 比例规范化（`20` -> `20%`，`A(10%)`保留）
4. 比例冲突告警：`multi_seller_ratio_conflict`
5. 多转让方识别告警：`multi_seller_detected`

### 4.5 挂牌次数派生（R006）
1. `-x` => `x`
2. `-0` => 空（预披露辅助后缀，不计挂牌次数）
3. 无后缀 => `1`

### 4.6 报废实物资产过滤（R010）
1. 识别范围：仅对“实物资产”记录生效（项目类型/文件名/工作表名命中标记）。
2. 过滤条件：命中报废关键词且未命中否定关键词（例如“非报废”）。
3. 执行动作：
   - `plan`：仅产出审计 finding（`scrap_physical_asset_filtered`）。
   - `apply`：删除该行（`filter_out_row`），并记录审计轨迹。
4. 配置原则：默认关闭（`enabled=false` + `active=false`），业务确认后再开启。

---

## 5. 迁移实施方案（解析器 -> PPE）

### 5.1 双轨阶段
1. 阶段 A：`parser=full` + `ppe=plan`
2. 阶段 B：`parser=full` + `ppe=apply(copy-on-write)`
3. 阶段 C：`parser=ppe_ready` + `ppe=apply`

### 5.2 白名单字段优先迁移
1. 第一批：`挂牌次数`、`类型`、`隶属集团`
2. 第二批：多转让方规范化相关字段（`转让方`、`备注`）

### 5.3 回滚策略
1. 解析器保留 `full` profile 作为应急回滚。
2. PPE 采用 copy-on-write，禁止覆盖原始输入目录。

---

## 6. 里程碑与交付

### M1（骨架）
1. 已完成。
2. 交付：可跑通输入->规则->审计闭环。

### M2（核心规则）
1. 进行中：
   - `R001~R006` 已完成并可运行。
2. 待完成：
   - 规则参数调优与数据表覆盖率提升。

### M3（稳定性）
1. 待完成：
   - `R007~R010` 迁移验收与阈值固化（规则已实现，需批量数据验证）。
2. 增强点：错误隔离、批量性能优化、规则监控统计。

### M4（业务化）
1. 待完成：一键批处理、发布说明、验收模板。

---

## 7. 测试与验收

### 7.1 必测项
1. `plan/apply` 一致性（审计口径一致）
2. 幂等性（同输入重复运行结果一致）
3. copy-on-write 不覆盖原始文件
4. 差异可追溯（每条 patch/finding 有 `rule_id`）

### 7.2 迁移验收
1. `parser full` vs `parser ppe_ready + ppe apply` 在白名单字段达到目标一致率。
2. 冲突与缺失均有审计记录，人工可复核。
3. 回滚演练通过。

---

## 8. GitHub 发布策略（数据剥离）

### 8.1 禁止上传内容
1. 具体网页缓存目录：`网页暂存*`
2. 具体业务表格/结果：`*.xlsx`、`*.xls`、运行生成 CSV
3. 运行目录：`logs/`、`postprocess_output/`、`postprocess_audit/`

### 8.2 允许上传内容
1. 代码、脚本、文档
2. 配置模板（`*.template.csv`）
3. 示例配置（不含业务敏感数据）

### 8.3 发布前检查
1. 全量检索敏感目录与文件后再推送。
2. 仅推送“清洁仓库”版本。

---

## 9. 下一步任务（短期）
1. 你填写四张模板表（转让方-集团、转让方-类型、集团-集团、集团-类型）。
2. 我基于真实样本调优 `R003` 与 `R007~R009` 参数（减少误报/漏报）。
3. 对 `R010` 做首轮业务词库校准（报废/否定词），并确认误删率。
4. 用固定回归样本跑自动对比脚本，产出一致率与差异清单。

---

## 10. 复杂逻辑审查结论（2026-02-28）

### 10.1 高优先级问题（P0）
1. 规则链路非增量视图：后续规则读取不到前序规则 patch 后的数据视图。
   - 典型现象：`R005` 修正 `source_type` 后，`R009` 仍按旧值校验，产生误报。
2. Excel 读取句柄释放不彻底（Windows 文件锁）。
   - 典型现象：执行后临时文件/输出文件可能无法删除或覆盖。

### 10.2 中优先级问题（P1）
1. `-0` 场景仅告警不修正：`R006` 不会清空已有挂牌次数，和“`-0` 不计挂牌次数”口径存在偏差。
2. 规则配置语义不直观：`rules` 采用对象配置时，未显式配置的内置规则默认仍启用。
3. `overwrite=true` 且 `output_dir=input_dir` 时，输入可能被排除，导致 `discovered_files=0`。

### 10.3 修复计划
1. 第 1 批（P0）：先修规则执行上下文一致性与 Excel 句柄释放，补最小回归样例。
2. 第 2 批（P1）：修正 `-0` 数据治理策略，明确配置语义（对象模式/白名单模式），并加配置保护。
3. 第 3 批（验收）：补端到端回归集（plan/apply 一致性、幂等、copy-on-write、审计可追溯）。
