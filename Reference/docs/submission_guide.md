# 提交文件准备 - 使用说明

## 概述

`prepare_submission.py` 脚本用于将下载的 HTML/MHTML 文件从 `raw/auto` 目录复制并重命名到 `outputs/submission` 目录，方便后续的网页备份/归档提交。

## 工作流程

```
1. 下载文件（下载器输出到 raw/auto）
        ↓
2. 解析数据（解析器生成 Excel 映射）
        ↓
3. 执行提交准备脚本
        ↓
4. outputs/submission 生成重命名后的文件
        ↓
5. 提交网页备份
```

## 使用方法

### Python 直接运行

```powershell
python scripts/prepare_submission.py
```

日志文件会自动输出到 `<data_root>/logs/submission_prepare_YYYYMMDD_HHMMSS.log`

## 支持的格式

- ✅ **HTML** - `.html` 标准网页格式（SSE/CBEX 下载器输出）
- ✅ **MHTML** - `.mhtml` 单文件网页格式（公共资源网等）
- ✅ **资源文件夹** - `_files` 文件夹（自动复制）

## 映射数据源

默认策略（`submission_defaults.mapping_source = "excel_only"`）：

### 1. Excel 文件（默认）✅
- 扫描 `<data_root>/outputs/excel` 目录
- 扫描目录下全部 `.xlsx`（忽略临时文件 `~$*`），全部参与映射汇总
- 读取每个 Excel 的全部 sheet，提取 `项目编号` 和 `项目名称` 两列
- **最可靠，推荐作为正式提交映射源**

### 2. Metadata JSON（可选补充）
- 当 `mapping_source = "excel_then_metadata"` 时启用
- 查找 HTML/MHTML 旁边的 `_metadata.json`
- 仅补充 Excel 缺失编号；冲突时仍优先 Excel

## 特殊字符处理

- 项目名称中的特殊字符自动转换为下划线：`/ \ : * ? " < > |` → `_`
- 文件名长度限制 200 字符，超长自动截断
- 保证 Windows 260 字符路径限制兼容

## 输出结构

执行后会在 `<data_root>/outputs/submission` 生成：

```
submission/
├─ G32025SH1000194-上海电气集团恒联企业发展有限公司35%股权.html
├─ G32025SH1000194-上海电气集团恒联企业发展有限公司35%股权_files/
│  ├─ image1.png
│  ├─ style.css
│  ├─ script.js
│  └─ ... (资源文件)
├─ P32025BJ1000195-某项目.mhtml  # MHTML 格式
├─ ... (其他项目)
└─ _manifest.json  # 清单文件
```

## 清单文件说明

`_manifest.json` 包含所有复制的文件的元数据：

```json
{
  "timestamp": "2026-03-06T11:49:01",
  "submission_dir": "E:\\PEAP_DATA\\outputs\\submission",
  "total_files": 147,
  "files": [
    {
      "filename": "G32025SH1000194-上海电气集团恒联企业发展有限公司35%股权.html",
      "project_code": "G32025SH1000194",
      "project_name": "上海电气集团恒联企业发展有限公司35%股权",
      "size_bytes": 524288,
      "has_assets_dir": true
    },
    ...
  ]
}
```

## 日志输出

脚本执行时会生成详细日志：

- **日志文件**：`<data_root>/logs/submission_prepare_YYYYMMDD_HHMMSS.log`
- **控制台输出**：实时显示处理进度和错误信息
- **包含内容**：
  - 加载的映射条数
  - 复制的文件数
  - 失败的文件列表
  - 生成的清单位置

## 常见场景

### 场景 1：首次准备提交

```powershell
# 1. 在桌面应用中完成一键执行或手动导入，确认记录已入库

# 2. 如需提交留档，再执行准备脚本
python scripts/prepare_submission.py
```

### 场景 2：重复准备（默认增量更新）

```powershell
# 直接执行即可：未变化文件会跳过，变化文件会增量更新
python scripts/prepare_submission.py
```

### 场景 3：验证导出质量

```powershell
# 检查清单文件
Get-Content E:\PEAP_DATA\outputs\submission\_manifest.json | ConvertFrom-Json | Select-Object total_files

# 查看日志
Get-Content E:\PEAP_DATA\logs\submission_prepare_*.log | Tail -50
```

## 故障排除

### 问题 1：提示找不到 Excel 文件

**原因**：桌面应用尚未生成可用导出结果，或 Excel 输出目录位置不对  
**解决**：
```powershell
# 先在桌面应用中确认导出完成，再检查 Excel 文件位置
Get-ChildItem E:\PEAP_DATA\outputs\excel -Filter "*.xlsx"
```

### 问题 2：复制失败数较多

**原因**：  
- HTML 文件名中无法提取项目编号
- 项目编号与 Excel 映射不匹配

**解决**：
- 检查 `<data_root>/logs/submission_prepare_*.log` 文件查看失败原因
- 确保下载器正确提取项目编号
- 检查 Excel 中项目编号的格式

### 问题 3：文件名包含乱码

**原因**：编码问题  
**解决**：脚本已处理 UTF-8 编码，通常不会出现此问题。若出现，请检查：
- 项目名称字段是否包含非标准字符
- Excel 文件是否正确保存为 UTF-8

### 问题 4：日志文件没有输出到 logs 目录

**原因**：`<data_root>/logs` 目录不存在  
**解决**：
```powershell
# 运行数据根初始化脚本
powershell -ExecutionPolicy Bypass -File scripts\init_data_root.ps1
```

## 技术细节

### 项目编号识别规则

脚本识别以下格式的项目编号：

```regex
(G3|Q3|P3|G6|Q6|P6|GR|QR|PR)\d{4}(SH|SZ|BJ|CQ|GZ|CD|TJ|WH|XA)\d+(?:-\d+)?
```

示例：
- `G32025SH1000194` ✓ SSE 上海
- `Q32025BJ1000195` ✓ 北交所 北京
- `P32025SZ1000196` ✓ 其他交易所
- `GR2025CQ1000197` ✓ 重庆

### 配置支持

脚本支持通过环境变量切换配置，也支持在运行配置文件中配置提交策略。推荐基于 `assets/runtime_config.template.json` 生成外部配置文件：

```json
"submission_defaults": {
  "resume": true,
  "prefer_auto": true,
  "mapping_source": "excel_only",
  "filename_max_bytes": 200
}
```

- `resume=true`：增量更新（默认）
- `prefer_auto=true`：同编号同时存在 auto/manual 时优先 auto，并在日志/终端给出 warning
- `mapping_source`：`excel_only` 或 `excel_then_metadata`

```powershell
# 使用自定义配置文件
$env:PEAP_RUNTIME_CONFIG_FILE = "D:\custom_config.json"
python scripts/prepare_submission.py
```

### MHTML 支持

- 自动识别 `.mhtml` 文件
- 保持原始扩展名不变
- 支持 MHTML 关联的资源文件夹

## 相关文档

- 数据结构详情：[docs/project_layout.md](../docs/project_layout.md#7-提交流程)
- 下载器说明：[README.md](../README.md#entrypoints)
- 解析器说明：[README.md](../README.md#entrypoints)
- 数据初始化：[scripts](../scripts)
