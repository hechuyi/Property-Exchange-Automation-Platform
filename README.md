# Property Exchange Automation Platform

PEAP 是面向产权交易网页的本地自动化处理平台：下载证据 → 解析 → 后处理 → canonical 入库 → 浏览/筛选 → 映射处置 → 导出归档。

## Quick Start

```bash
bash scripts/bootstrap_desktop_env.sh
bash start.sh
```

首次初始化会按 `.python-version` 准备 Python 环境，脚本内部执行 `uv sync --locked` 并依据 `uv.lock` 安装运行依赖，依据
`frontend/package-lock.json` 安装前端依赖，并把 Chromium 安装到工作区缓存。初始化前需要
可用的 `uv`、Node.js 18+ 和 npm；脚本会在缺失或版本不满足时直接报错。

或分步：

```bash
uv run python -m desktop_backend.app_backend --host 127.0.0.1 --port 42679
cd frontend && npm run dev
```

首次部署会按 `~/Documents/PEAP` 自动建出工作区。如需自定义，启动前 `export PEAP_WORKSPACE_ROOT=/path/to/workspace`（详见 `docs/storage.md`）。

## 文档导航

| 你想做的事 | 读这个 |
|---|---|
| 理解系统结构、模块归属、数据流、术语 | [docs/architecture.md](docs/architecture.md) |
| 查 HTTP API、错误码、scope/default 字段 | [docs/api.md](docs/api.md) |
| 查 SQLite 表结构、工作区目录布局、`PEAP_*` 环境变量 | [docs/storage.md](docs/storage.md) |
| 跑产品 / 排障 / 提交归档 / PPE 规则调试 / 维护脚本 | [docs/operations.md](docs/operations.md) |
| 加新交易所 / 业务 / 记录族 | [docs/extending.md](docs/extending.md) |
| 确认当前 worktree 是否可发布 | [docs/release-gate.md](docs/release-gate.md) |

## 仓库分区

- `frontend/`：当前桌面产品前端，仅消费 backend 已发布的 contract
- `desktop_backend/`：本地 HTTP adapter、service slice、resource/action contract
- `peap/`：下载、导入、后处理编排、canonical 化、store、导出
- `peap_core/`：catalog、状态机、共享 contract、跨层不可变语义
- `peap_parsers/`：parser runtime 与 source-specific 实现
- `peap_postprocess/`：PPE 引擎、规则、默认配置、CLI
- `scripts/`：发布、门禁、提交归档、维护脚本
- `tests/`：contract / regression / smoke / architecture 测试
- `docs/`：正式文档（活跃文件由 `docs/release-gate.md` 注册）

## 当前关键边界

- **shared actionable default scope** 是 backend-owned truth，不允许 frontend / helper 自行修补
- **`records browse runtime`** 是独立 read model，因此 records 页可以公开 `listing/all/all` 这样的 browse truth
- **导出语义**：记录页"导出 Excel"消费当前 records browse scope；总览页导出消费当前 actionable default scope。两者不能互通
- **records / export routing scope**：`record_family + business_id + exchange`。listing 兼容显示标签仅用于展示，**不是**当前 request routing truth
- **family / business / source 选项**只允许由 backend `/api/catalog` 从共享 catalog 发布
- **`mapping_refresh` 与 hidden/internal legacy `business_re_evaluation`** 是两条不同运行路径；后者只保留 distinct job event / metrics / 文案兼容，不属于当前公开 UI 或操作入口

历史快照 / 旧 adapter / 维护路径仍提到 legacy display label、旧默认范围别名或旧 backlog 形状的——那些都是兼容残留，不属于当前公开 contract。

## 常用验证

```bash
uv run python scripts/check_release_gate.py
```

执行当前发布门禁：ruff、前后端定向测试、全量 pytest、frontend build、文档注册表与 contract 漂移检查。
release gate 在执行 `frontend` 构建前会依据 `frontend/package-lock.json` 先跑一次 `npm ci`，因此干净 worktree 不依赖预先存在的本地 `node_modules/`。
完整门禁只在包含测试集的开发树执行；`runtime-source` staging 是门禁通过后的运行时输入，
不会携带 `tests/` 或 `frontend/tests/`，不能替代开发树做发布判定。

## 开发树与分发树

仓库根目录始终是开发树：源码、测试和文档在这里持续演进。分发时不要手工复制整个工作区，也不要把 `.venv`、`node_modules`、数据库、日志或下载证据带入包内。使用 `packaging/distribution-manifest.json` 定义的白名单生成独立 staging 树：

```bash
uv run python scripts/prepare_distribution.py --output release/PEAP-source
```

该命令只整理源码并写入 `DISTRIBUTION_MANIFEST.json`，不会安装依赖、构建前端或生成 `.app`。默认要求 Git 工作树干净；开发中仅需本地预览时才显式使用 `--allow-dirty`。后续 macOS 启动器和依赖内置属于独立的平台打包步骤，不应反向污染开发树。
