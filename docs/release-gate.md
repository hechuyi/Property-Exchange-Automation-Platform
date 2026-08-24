# 发布门槛

PEAP 当前是否可放行由本文与 `scripts/check_release_gate.py` 共同决定。门禁脚本读取本文的「自动化基线」与「活跃文档」两节；「自动化基线」必须与脚本内 `AUTOMATED_COMMANDS` 字面相等，「活跃文档」必须闭合覆盖受 git 跟踪的 `README.md` 与直接位于 `docs/` 下的 Markdown；任何漂移都会让 gate fail。

该门禁只在包含完整 `tests/` 与 `frontend/tests/` 的开发树执行。`runtime-source` staging
刻意排除测试输入，只是已通过门禁的运行时源码快照，不是再次运行完整门禁的工作树。

## 执行入口

```bash
uv run python scripts/check_release_gate.py
```

`--skip-commands` 只用于快速检查文档、发布标签与 worktree hygiene。它明确不是发布门禁：输出必须保持 `Overall: BLOCKED`，直到完整自动化基线实际运行并通过。

## 自动化基线

每条命令在 release 前必须 PASS。

- `uv run ruff check desktop_backend peap peap_core peap_parsers peap_postprocess tests config.py scripts`
- `uv run python -m pytest tests/test_bs4_dependency_isolation.py tests/test_environment_tooling.py tests/test_parser_registry.py tests/test_parsing_contract.py tests/test_snapshot_contracts.py tests/test_scope_validation_contract.py tests/test_record_scope.py tests/test_request_contract.py tests/test_records_service_scope_contract.py tests/test_settings_service.py tests/test_settings_backend.py tests/test_export_service_scope.py tests/test_execution_download_service.py -q`
- `uv run python -m pytest tests/test_catalog_api.py tests/test_mapping_backlog_service.py tests/test_mapping_backlog_backend.py tests/test_job_result_contract.py tests/test_job_event_contract.py tests/test_progress_contract.py tests/test_progress_resource_contract.py tests/test_overview_runtime_contract.py tests/test_overview_runtime_backend.py tests/test_jobs_actions_backend.py -q`
- `node --test frontend/tests/appConsumerGating.test.mjs frontend/tests/mappingsPanelConsumer.test.mjs frontend/tests/mappingActionsContract.test.mjs frontend/tests/mappingApiClient.test.mjs frontend/tests/contractAdapters.test.mjs frontend/tests/catalogContract.test.mjs frontend/tests/recordScopeContract.test.mjs frontend/tests/actionRequestsContract.test.mjs frontend/tests/jobPresentation.test.mjs frontend/tests/oneClickModal.test.mjs frontend/tests/overviewPresentation.test.mjs frontend/tests/settingsState.test.mjs frontend/tests/*.mjs`
- `uv run python -m pytest tests/test_frontend_fresh_settings_one_click_smoke.py tests/test_manual_import_export_http_smoke.py -q`
- `uv run python -m pytest tests/test_release_gate.py -q`
- `uv run python -m pytest -q`
- `cd frontend && npm run build`

CI（`.github/workflows/ci.yml`）跑第 1 / 2 / 7 条（ruff + targeted pytest + 全量 pytest）。其余条本地必跑。

执行 `uv run python scripts/check_release_gate.py` 时，脚本会在 `frontend` 构建前依据 `frontend/package-lock.json` 先执行一次 `npm ci`，确保干净 worktree 可直接复现前端 build，不依赖仓库外残留的 `node_modules/` 或本地 npm cache 命中。

## 活跃文档

PEAP 发布版本下 `docs/` 与根 README 必须只包含以下文件，不允许增加未注册的灰色文档。

- `README.md`
- `docs/architecture.md`
- `docs/api.md`
- `docs/storage.md`
- `docs/operations.md`
- `docs/extending.md`
- `docs/release-gate.md`

## 真实产品烟测

进入 `release_candidate` label 前必须人工跑过：

- [x] 总览 / 任务 / 记录 / 待复核 / 映射 / 设置 六个主页面真实打开
- [x] 待复核页只读打开，确认无修改、重跑、批量处理或禁用未来动作按钮
- [x] 记录页真实筛选已执行
- [x] 映射页「预览影响范围」已真实执行
- [x] fresh `settings/basic` 仅修改 `default_exchange` 不会静默改写共享 actionable scope，已由 automated smoke 覆盖
- [x] fresh 未显式 scope 的手动导入保留 unknown truth，不被默认范围覆盖，已由 automated smoke 覆盖
- [x] fresh 显式 scope 的手动导入 → 映射预览/保存 → ready → 导出闭环，已由 automated smoke 覆盖
- [x] fresh 原生目录选择器分支已单独 smoke
- [x] fresh 导出空结果与 delayed reclassification regression 已由 automated smoke 覆盖
- [x] `GET /api/mappings` 契约检查没有 `business_resolution`、`business_resolution_count` 或 `re_evaluate_business` CTA
- [x] `GET /api/review-problems` 契约检查包含五类 problem kind，且说明 field-missing acknowledgement 不补字段、不允许导出
- [x] mapping refresh 明确不扫描 `pending_review`

automated smoke 覆盖项见 `tests/test_frontend_fresh_settings_one_click_smoke.py` + `tests/test_manual_import_export_http_smoke.py`。

Playwright smoke 不依赖仓库内被忽略的 `cache/ms-playwright`。测试会优先尊重 `PEAP_PLAYWRIGHT_BROWSERS_PATH` / `PLAYWRIGHT_BROWSERS_PATH`，若未提供可用 Playwright runtime，则回退到 `PEAP_BROWSER_EXECUTABLE_PATH` 或本机可检测到的 Chromium / Chrome 可执行文件。

## 阶段一冻结边界

冻结的产品面：总览 / 任务 / 记录 / 待复核 / 映射 / 设置六个主页面可打开、记录筛选主路径、待复核只读查看主路径、映射预览/裁决主路径、one-click 主路径、手动导入路径输入主路径、导出主路径。任何变更不得破坏：

- transport envelope 不扩第二套 shape
- controller / contract / adapter / presenter 分层不因单一业务破坏
- 活跃 consumer 不引入 silent fallback scope/default truth

历史兼容残留可能仍带有旧 scope/default 用语（如 listing 兼容字段 `project_type`）—— 那是 maintenance-only residue，不是当前 contract。

## 法定 release label

唯一允许进入正式发布的 label：`release_candidate`。

## 当前发布状态

- 当前标签：`release_candidate`
