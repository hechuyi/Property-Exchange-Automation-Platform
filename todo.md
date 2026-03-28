# PEAP Release-Trust TODO

Last updated: 2026-03-28
Owner in this thread: Codex

当前根工作区已经把“桌面交付主线 + `uv` 环境统一”整合到一起，不再依赖 `.venv-desktop`、`pyenv` 或 `requirements*.lock`。本轮实际验收命令如下：

```bash
uv lock --check
```

```bash
uv run python -m unittest discover -s tests -q
```

```bash
cd desktop_app && npm test
```

上面三组命令当前都是全绿。此前关于默认开发态依赖 `.venv-desktop` 的阻塞描述已经失效；当前主线环境约束是 repo 根目录 `.venv + uv.lock`。

## Release Blockers

- 当前没有新的环境级 release blocker。
- 如果后续再出现发布阻塞，应按“产品语义 / 数据契约 / 真实 Electron 冒烟”三类单独记录，不要再把旧 Python 环境入口问题写回这里。

## Closed By Contract Regression

- 任务语义：`5.15`、`5.17`、`5.21`、`5.32`、`5.37`、`5.41`、`5.42`
- 范围语义：`5.18`、`5.30`
- 对象身份：`5.36`、`5.38`、`5.39`、`5.40`、`5.43`
- fallback / cap：`5.35`

这些关闭项的测试证据已经写回 [`docs/real_operation_test_report_2026-03-23.md`](/Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform/docs/real_operation_test_report_2026-03-23.md)。
