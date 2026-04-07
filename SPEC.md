# 前端重构规格说明书

## 项目信息

- **项目名称**：PEAP Frontend Redesign
- **Worktree**：`feature/frontend-redesign`
- **设计风格**：Paper-Tool Aesthetic（暖米白底 + DM Serif Display 标题 + DM Sans 正文）
- **技术选型**：纯原生 HTML/CSS/JS（无框架），Vite dev server

---

## 目录结构

```
frontend/
├── index.html      # 入口 HTML
├── app.css         # 全局样式（设计系统）
├── api.js          # API 调用层
├── app.js          # 主应用逻辑（路由、状态、渲染）
├── vite.config.js  # Vite 配置（含 proxy）
└── package.json    # npm 依赖
```

---

## 后端 API（已验证）

**前缀**：`/api/*`，Vite proxy 转发到 `http://127.0.0.1:42679`

### GET 接口

| 接口 | 响应关键字段 |
|------|-------------|
| `GET /api/overview` | `latest_job`, `latest_progress`, `recent_jobs`, `record_state_counts`, `pending_mapping_count`, `browser_runtime`, `browser_install`, `product_readiness` |
| `GET /api/jobs?limit=N` | `{ jobs: [{ job_id, job_type, status, downloaded_count, persisted_count, exception_count, summary, created_at, updated_at }] }` |
| `GET /api/jobs/{id}/events?limit=N` | `{ events: [{ event_id, event_ts, stage, status, project_code, archive_path, error_type, error_message, payload }], returned_count, total_count, truncated }` |
| `GET /api/mappings` | `{ pending: [{ record_id, revision_id, project_code, company_name, state, status_label, gap_codes, recommended_rule, available_rule_kinds, candidate_resolutions }], entries: [{ entry_id, company_name, group_name, source_type, metadata }], returned_count, total_count, truncated }` |
| `GET /api/settings/basic` | `{ default_exchange, default_project_type, default_concurrency, archive_root, export_root, workspace_root }` |

### POST 接口

| 接口 | 请求 body |
|------|----------|
| `POST /api/jobs/one-click` | `{}` |
| `POST /api/jobs/manual-import` | `{ input_dir: string }` |
| `POST /api/exports` | `{ scope: string, mode: string }` |
| `POST /api/records` | `{ record_family, state, project_type, keyword, date_from, date_to, page, page_size }` |
| `POST /api/mappings` | mapping 对象 |
| `POST /api/mappings/reprocess-pending` | `{}` |
| `POST /api/runtime/install-browser` | `{}` |

---

## 数据结构（后端实际字段）

### Job 状态值

- `starting` — 启动中（活跃）
- `running` — 执行中（活跃）
- `success` — 已完成（终止）
- `success_with_warnings` — 已完成有待处理（终止）
- `failed` — 执行失败（终止）
- `interrupted` — 已中断（终止）

**终止状态**：`success`, `success_with_warnings`, `failed`, `interrupted`

### Job 类型

| job_type | 标签 |
|----------|------|
| `one_click` | 一键执行 |
| `download_ingest` | 历史区间任务 |
| `export_excel` | 导出 Excel |
| `manual_import` | 手动导入解析 |
| `mapping_refresh` | 映射回刷 |

### latest_progress 字段

| 字段 | 说明 |
|------|------|
| `phase_code` | 阶段代码：`prepare_tasks`, `save_pages`, `reprocessing`, `exporting` 等 |
| `phase_label` | 阶段中文标签 |
| `job_status` | job 状态 |
| `downloaded_count` | 已下载页数 |
| `persisted_count` | 已保存记录数 |
| `exception_count` | 异常数 |
| `pending_mapping_count` | 待映射数 |
| `task_index` | 当前任务索引 |
| `task_total` | 任务总数 |
| `phase_percent` | 阶段进度百分比 |
| `current_task_label` | 当前任务标签 |

### Record 状态

| state | 标签 |
|-------|------|
| `ready` | 已录入 |
| `pending_mapping` | 待补映射 |
| `mapping_conflict` | 映射冲突 |
| `skipped` | 已跳过 |
| `parse_failed` | 解析失败 |
| `postprocess_failed` | 处理失败 |
| `conflict` | 归档重名 |

### Records 响应

```json
{
  "rows": [{ "record_id", "project_code", "project_name", "project_type", "exchange", "listing_date", "state", "status_label", "status_detail", "archive_path", "updated_at", "values" }],
  "total_count", "page_count", "has_more",
  "summary": { "filtered_state_counts", "page_state_counts", "total_count", "visible_count" }
}
```

---

## 页面与路由

SPA 模式，五个面板，通过侧边栏导航切换：

| 路由参数 | 面板 | 功能 |
|----------|------|------|
| `#overview`（默认） | 总览 | 快捷操作、统计、当前任务进度、最近任务、运行环境 |
| `#tasks` | 任务 | 任务列表 |
| `#records` | 记录 | 筛选表单、记录表格（分页）、导出 |
| `#mappings` | 映射 | 待补映射列表、已保存规则 |
| `#settings` | 设置 | 基本设置（只读）、运行环境、安装浏览器 |

---

## 设计系统

### 色彩

```css
--bg:           #F5F0E8   /* 暖米白底 */
--surface:      #FEFCF9   /* 卡片白 */
--border:       #E5DDD0   /* 边框 */
--text:         #1C1917   /* 主文字 */
--text-muted:   #78716C   /* 次要文字 */
--text-faint:   #A8A29E   /* 辅助文字 */
--accent:       #C2410C   /* 强调色（深赤陶） */
--accent-hover: #9A3412
--accent-light: #FEF3EC
--success:      #166534
--success-bg:   #F0FDF4
--warning:      #B45309
--warning-bg:   #FFFBEB
--danger:       #B91C1C
--danger-bg:    #FEF2F2
```

### 字体

- 标题/品牌：`DM Serif Display`
- 正文/UI：`DM Sans`
- 数字/代码：`JetBrains Mono`

### 布局

- 侧边栏：180px，纯文字导航，中文标签
- 内容区：padding 40px，最大宽度 1200px
- 卡片：12px border-radius，轻阴影

---

## 验证方式

```bash
# 终端1：启动后端
cd /Users/rtoc/Documents/WorkSpace/Property-Exchange-Automation-Platform && \
  source .venv/bin/activate && \
  uv run python -m desktop_backend.app_backend --port 42679

# 终端2：启动前端
cd frontend && npm install && npm run dev

# 浏览器打开 http://localhost:5173
```
