# 发布门槛

当前文档定义的是 **`mainline_development` 阶段** 的主线门槛，而不是发版门槛。执行入口：

```bash
uv run python scripts/check_release_gate.py
```

## 自动化基线

- `uv lock --check`
- `uv run pytest tests/test_environment_tooling.py tests/test_release_gate.py -q`
- `uv run python -m desktop_backend.app_backend --help`
- `cd desktop_app && npm test`
- `cd desktop_app && npm run build`

## 活跃文档

- `README.md`
- `docs/development_plan.md`
- `docs/project_layout.md`
- `docs/submission_guide.md`
- `docs/desktop_product_runbook_2026-03-26.md`
- `docs/release_gate.md`

## 真实 Electron Smoke

- [x] `renderer_ready`
- [x] `manual_import`
- [x] `export`
- [x] `interrupt_restart` 严格语义到达字面 `interrupted` 终态
- [x] `mapping_refresh_1` 真实证据已留痕

## Smoke 证据

- dated 报告：`docs/desktop_electron_smoke_report_2026-03-28.md`
- 最新 strict raw report：`/tmp/peap-desktop-smoke-main-1774696290.md`
- `mapping_refresh_1` 补充 raw report：`/tmp/peap-desktop-smoke-1774691518.md`

## 当前发布状态

- 当前标签：`mainline_development`
- 当前结论：主线文档、自动化基线与 dated smoke 证据已经对齐
- 当前边界：只评估 repo-root 开发主线，不包含独立安装包、附带运行时或发包产物
