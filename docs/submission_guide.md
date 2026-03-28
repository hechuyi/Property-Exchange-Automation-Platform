# 提交归档指南

## 目的

该流程用于把下载或手动导入得到的页面整理为可交付的归档目录，统一文件命名，并附带清单文件。

## 执行方式

在仓库根目录执行：

```bash
uv run python scripts/prepare_submission.py
```

脚本会扫描当前工作区下的归档页面与相关元数据，生成 `<workspace_root>/outputs/submission/` 目录。

## 输出内容

- 规范命名的 HTML 页面
- 对应的 `_files` 静态资源目录
- `_manifest.json` 清单文件

## 使用约束

- 归档输出只针对当前工作区数据，不应指向仓库内的临时目录。
- 如果记录仍处于 `pending_mapping` 或 `failed`，应先完成映射或修复，再进入归档提交流程。
