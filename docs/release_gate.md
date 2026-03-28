# 发布门槛

执行命令：

```bash
uv run python scripts/check_release_gate.py
```

## 自动化基线

- `uv lock --check`
- `uv run python -m unittest discover -s tests -q`
- `cd desktop_app && npm test`

## 活跃文档

- `README.md`
- `docs/development_plan.md`
- `docs/project_layout.md`
- `docs/submission_guide.md`
- `docs/desktop_product_runbook_2026-03-26.md`

## 真实 Electron Smoke

- [ ] one-click 主路径
- [ ] manual-import 主路径
- [ ] export 主路径
- [ ] interrupt / cancel 主路径
- [ ] recovery / restart 后状态恢复

## 当前发布状态

- 当前标签：`release_candidate`
- 阻塞原因：真实 Electron smoke 报告尚未闭环并回写 dated 报告
