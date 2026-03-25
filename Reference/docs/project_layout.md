# 项目目录规范（重建版）

## 1. 原则

- 本分支不做旧版目录兼容。
- 运行数据与代码彻底分离。
- 所有运行时数据都基于统一数据根：运行配置的 `paths.data_root`。
- 解析器不依赖单独的字段映射表文件。

## 2. 数据根规则

- 由运行配置的 `paths.data_root` 指定。
- 推荐基于 `assets/runtime_config.template.json` 生成外部配置文件，并通过 `PEAP_RUNTIME_CONFIG_FILE` 指向该文件。
- 代码里不再内置运行路径默认值。

## 3. 标准目录结构

### 3.1 数据目录（`<paths.data_root>`）

```text
<paths.data_root>
├─raw
│  ├─manual
│  └─auto
├─reference
│  ├─RawPages
│  ├─挂牌_股权转让.xlsx
│  ├─挂牌_实物资产.xlsx
│  ├─挂牌_增资扩股.xlsx
│  └─挂牌_预披露.xlsx
├─logs
│  ├─download_chunk_state
│  ├─parser_regression_runs
│  └─compare
├─outputs
│  ├─excel              # 解析器 Excel 输出
│  ├─submission         # 提交文件（通过 prepare_submission.py 生成）
│  │  ├─[项目编号]-[项目名称].html
│  │  ├─[项目编号]-[项目名称]_files/
│  │  └─_manifest.json  # 提交清单
│  ├─postprocess        # PostProcess 输出
│  └─postprocess_audit  # PostProcess 审计日志
└─backup
```

### 3.2 程序配置目录（`<PROJECT_ROOT>/assets`）

```text
<PROJECT_ROOT>/assets
├─excel_output_schema.json
├─runtime_config.template.json
└─runtime_config.json          # 源码树本地默认配置
```

## 4. 初始化

```powershell
python scripts\init_runtime_config.py --output "C:\PEAP\runtime_config.json" --data-root "E:\PEAP_DATA"
$env:PEAP_RUNTIME_CONFIG_FILE = "C:\PEAP\runtime_config.json"
powershell -ExecutionPolicy Bypass -File scripts\init_data_root.ps1 -DataRoot "E:\PEAP_DATA" -RuntimeConfigFile "C:\PEAP\runtime_config.json"
```

## 5. 运行约束

- 下载器禁止把输出写回代码仓库目录。
- 解析、回归、缓存、日志默认都写入数据根。
- 解析 Excel 输出由 `paths.output_excel_dir` 决定。
- PostProcess 可使用：
  `peap_postprocess/ppe_config/postprocess_external_template.json`

## 6. 配置部署

- 推荐使用 `assets/runtime_config.template.json` 生成外部运行配置，不在代码内保留运行路径默认值。
- 可通过 `PEAP_RUNTIME_CONFIG_FILE` 指向外部配置文件；`assets/runtime_config.json` 仅作为源码树本地默认配置。
- 主要字段：
  - `paths.*`：数据根、日志、解析输出、缓存、回归目录等
  - `output_file_names` / `deal_file_names`：输出文件命名
  - `parser_defaults` / `downloader_defaults`：CLI 默认参数
  - `downloader_task_page_size`：各任务 page_size

## 7. 提交流程

### 7.1 准备提交文件

下载完成后，可通过 `prepare_submission.py` 脚本一键准备提交文件：

```powershell
# 执行提交文件准备
python scripts/prepare_submission.py
```

**脚本功能：**
- 扫描 `<data_root>/raw/auto` 下的所有 HTML/MHTML 文件
- 读取项目编号和项目名称的映射关系（优先级：Excel > metadata.json > 文件名）
- 复制文件到 `<data_root>/outputs/submission` 并重命名为 `[项目编号]-[项目名称].html` 或 `.mhtml`
- 同时复制对应的 `_files` 文件夹（包含资源文件）
- 生成 `_manifest.json` 清单文件
- 日志输出到 `<data_root>/logs/submission_prepare_*.log`

### 7.2 输出结构

```
<data_root>/outputs/submission/
├─ G32025SH1000194-上海电气集团恒联企业发展有限公司35%股权.html
├─ G32025SH1000194-上海电气集团恒联企业发展有限公司35%股权_files/
│  ├─ image1.png
│  ├─ style.css
│  └─ ...
├─ [其他项目]
└─ _manifest.json  # 包含所有文件的项目代码、名称等信息
```

### 7.3 特殊字符处理

- 项目名称中的特殊字符（`/\:*?"<>|` 等）自动转换为 `_`
- 文件名过长（超过 200 字符）会自动截断
- 保证 Windows 路径长度限制兼容
