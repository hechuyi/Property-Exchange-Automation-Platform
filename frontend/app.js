/* ── App ── */
(function () {
  "use strict";

  /* ── Helpers ── */
  const $ = (sel, ctx = document) => ctx.querySelector(sel);
  const $$ = (sel, ctx = document) => [...ctx.querySelectorAll(sel)];
  const num = (v) => { const n = Number(v); return Number.isFinite(n) ? n : 0; };

  /* ── Job 状态常量（后端实际值）── */
  const ACTIVE_STATUSES = new Set(["starting", "running"]);
  const TERMINAL_STATUSES = new Set(["success", "success_with_warnings", "failed", "interrupted"]);

  const JOB_TYPE_LABELS = {
    one_click: "一键执行",
    download_ingest: "历史区间任务",
    export_excel: "导出 Excel",
    manual_import: "手动导入解析",
    mapping_refresh: "映射回刷",
  };

  const JOB_STATUS_LABELS = {
    starting: "启动中",
    running: "执行中",
    success: "已完成",
    success_with_warnings: "已完成（有待处理）",
    failed: "执行失败",
    interrupted: "已中断",
  };

  function jobTypeLabel(v) { return JOB_TYPE_LABELS[String(v || "")] || String(v || "任务"); }
  function jobStatusLabel(v) { return JOB_STATUS_LABELS[String(v || "")] || String(v || ""); }
  function isActive(v) { return ACTIVE_STATUSES.has(String(v || "").trim().toLowerCase()); }
  function isTerminal(v) { return TERMINAL_STATUSES.has(String(v || "").trim().toLowerCase()); }

  function stateDotClass(status) {
    if (isActive(status)) return "running";
    if (status === "success" || status === "completed") return "success";
    if (status === "failed") return "failed";
    return "idle";
  }

  function parseTs(ts) {
    // 后端返回 "YYYY-MM-DD HH:MM:SS" 格式，不是时间戳
    if (!ts || typeof ts !== "string") return null;
    const d = new Date(ts.replace(" ", "T"));
    return isNaN(d.getTime()) ? null : d.getTime();
  }

  function formatTimeAgo(ts) {
    const ms = parseTs(ts);
    if (!ms) return "";
    const diff = Date.now() - ms;
    const m = Math.floor(diff / 60000);
    if (m < 1) return "刚刚";
    if (m < 60) return `${m} 分钟前`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h} 小时前`;
    const d = Math.floor(h / 24);
    if (d < 7) return `${d} 天前`;
    return new Date(ms).toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
  }

  function formatJobTime(ts) {
    const ms = parseTs(ts);
    if (!ms) return "";
    return new Date(ms).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  }

  /* ── State ── */
  let currentPanel = "overview";
  let overview = {};
  let jobs = [];
  let records = { rows: [], summary: {}, columns: [] };
  let mappings = { pending: [], entries: [] };
  let settings = {};
  let recordPage = 1;
  let recordPageSize = 50;
  let recordFilters = { state: "all", project_type: "all", keyword: "", date_from: "", date_to: "" };
  let pollTimer = null;
  let errorMsg = "";
  let currentEvents = [];

  /* ── Render ── */
  function render() {
    $$(".sidebar-nav-link").forEach((link) => {
      link.classList.toggle("active", link.dataset.panel === currentPanel);
    });
    $$(".panel").forEach((p) => p.classList.toggle("active", p.id === `panel-${currentPanel}`));
    switch (currentPanel) {
      case "overview": renderOverview(); break;
      case "tasks": renderTasks(); break;
      case "records": renderRecords(); break;
      case "mappings": renderMappings(); break;
      case "settings": renderSettings(); break;
    }
  }

  /* ── Overview ── */
  function renderOverview() {
    const el = $("#panel-overview");
    const latestJob = overview.latest_job || null;
    const recentJobs = Array.isArray(overview.recent_jobs) ? overview.recent_jobs : [];
    const stateCounts = overview.record_state_counts || {};
    const latestProgress = overview.latest_progress || {};
    const runtime = overview.product_readiness || {};
    const browserRt = overview.browser_runtime || {};
    const browserInstall = overview.browser_install || {};

    const readyCount = num(stateCounts.ready);
    const pendingCount = num(stateCounts.pending_mapping);
    const failedCount = num(stateCounts.parse_failed) + num(stateCounts.postprocess_failed);
    const jobRunning = latestJob && isActive(latestJob.status);

    const isInstalling = browserInstall.status === "running";
    const browserReady = browserRt.installed;
    const headline = isInstalling ? "正在准备浏览器运行环境"
      : browserReady ? "运行环境已就绪"
      : "运行环境缺失或异常";
    const browserState = !browserRt ? "浏览器未检测"
      : isInstalling ? "浏览器正在安装"
      : browserReady ? "浏览器已就绪"
      : browserRt.error ? "浏览器状态异常"
      : "浏览器未安装";

    const runtimeIssues = [];
    if (browserInstall.message) runtimeIssues.push(browserInstall.message);
    if (browserRt.error) runtimeIssues.push(browserRt.error);
    if (Array.isArray(runtime.issues) && runtime.issues.length) {
      runtime.issues.forEach((i) => runtimeIssues.push(i.message));
    }

    const progress = latestProgress;
    const pct = isTerminal(latestJob?.status) ? 100 : Math.max(0, Math.min(100, num(progress.phase_percent)));

    el.innerHTML = `
      <div class="animate-in">
        <h1 class="page-title">总览</h1>
      </div>

      <!-- 快捷操作 -->
      <section class="animate-in delay-1">
        <p class="section-label">快捷操作</p>
        <div class="action-grid">
          <button class="action-btn primary" id="btn-oneclick">
            <div class="icon">&#9654;</div>
            <span class="label">一键执行</span>
            <span class="sublabel">抓取 → 解析 → 映射 → 归档</span>
          </button>
          <button class="action-btn" id="btn-historical">
            <div class="icon">&#9784;</div>
            <span class="label">历史区间</span>
            <span class="sublabel">指定日期范围抓取</span>
          </button>
          <button class="action-btn" id="btn-import">
            <div class="icon">&#8679;</div>
            <span class="label">手动导入</span>
            <span class="sublabel">解析本地 HTML / MHTML 文件</span>
          </button>
          <button class="action-btn" id="btn-export">
            <div class="icon">&#8594;</div>
            <span class="label">导出 Excel</span>
            <span class="sublabel">将就绪记录导出为表格</span>
          </button>
        </div>
        ${errorMsg ? `<div class="alert alert-danger">&#9888; ${errorMsg}</div>` : ""}
      </section>

      <!-- 记录统计 -->
      <section class="animate-in delay-2">
        <p class="section-label">记录统计</p>
        <div class="stats-grid">
          <div class="stat-card">
            <div class="stat-label">已录入</div>
            <div class="stat-value">${readyCount}</div>
            <div class="stat-sub">ready 状态</div>
          </div>
          <div class="stat-card">
            <div class="stat-label">待补映射</div>
            <div class="stat-value" style="color:var(--warning)">${pendingCount}</div>
            <div class="stat-sub">需要补充规则</div>
          </div>
          <div class="stat-card">
            <div class="stat-label">解析失败</div>
            <div class="stat-value" style="color:var(--danger)">${failedCount}</div>
            <div class="stat-sub">需要检查原始文件</div>
          </div>
          <div class="stat-card">
            <div class="stat-label">最新任务</div>
            <div class="stat-value" style="font-size:20px">${jobRunning ? "进行中" : latestJob ? "已完成" : "无"}</div>
            <div class="stat-sub">${latestJob ? formatTimeAgo(latestJob.created_at) : "暂无任务记录"}</div>
          </div>
        </div>
      </section>

      <!-- 当前任务进度 -->
      ${latestJob ? `
      <section class="progress-section animate-in delay-3">
        <p class="section-label">当前任务</p>
        <div class="progress-card">
          <div class="progress-header">
            <span class="progress-title">${jobTypeLabel(latestJob.job_type)}${progress.phase_label ? ` · ${progress.phase_label}` : ""}</span>
            <span class="progress-badge${jobRunning ? " running" : ""}">${jobRunning ? "进行中" : jobStatusLabel(latestJob.status)}</span>
          </div>
          ${!isTerminal(latestJob.status) ? `
          <div class="progress-bar-wrap">
            <div class="progress-bar" style="width:${pct}%"></div>
          </div>` : ""}
          <div class="progress-meta">
            <span>${progress.phase_label || "—"}</span>
            <span>${pct}%</span>
          </div>
          ${currentEvents.length > 0 ? `
          <div class="progress-log">
            ${(function() {
              const reversed = currentEvents.slice().reverse();

              // 找到第一个 running 事件（当前正在进行的）
              let currentEntry = null;
              let doneCount = 0;
              const maxDone = 2;

              for (const ev of reversed) {
                const sp = ev.payload?.summary_payload || {};
                // running 事件只有 summary，done 事件有 aggregate_summary，两者都取
                const summary = sp.summary || sp.aggregate_summary || {};
                const isError = ev.error_type || ev.payload?.status === "failed";

                if (isError) {
                  const msg = ev.error_message || ev.payload?.error_message || "未知错误";
                  return `<div class="progress-log-item error">
                    <span class="progress-log-stage">${ev.stage}</span>
                    <span class="progress-log-msg">${ev.error_type ? ev.error_type + ": " : ""}${msg}</span>
                  </div>`;
                }

                const phase = sp.phase_percent;
                const taskLabel = sp.task_label || "";
                const kind = sp.kind || ev.stage;
                const listed = num(summary.listed);
                const candidates = num(summary.detail_candidates);
                const saved = num(summary.saved);
                const fetched = num(summary.detail_fetched);

                if (ev.status === "running" && !currentEntry) {
                  // 当前阶段：显示任务名 + 进度 + 统计
                  let statLine = "";
                  if (ev.stage === "prepare_tasks" || kind === "collect") {
                    statLine = listed ? `已列 ${listed} · 候选 ${candidates}` : `进行中 ${phase}%`;
                  } else if (ev.stage === "save_pages" || kind === "download") {
                    statLine = listed ? `已列 ${listed} · 已抓 ${fetched} · 已保存 ${saved}` : `进行中 ${phase}%`;
                  } else if (ev.stage === "parse_documents") {
                    statLine = saved ? `已保存 ${saved} · 候选 ${candidates}` : `进行中 ${phase}%`;
                  } else {
                    statLine = listed ? `已列 ${listed} · 已保存 ${saved}` : `进行中 ${phase}%`;
                  }
                  currentEntry = `<div class="progress-log-item">
                    <span class="progress-log-stage">${ev.stage}</span>
                    <span class="progress-log-msg">${taskLabel ? taskLabel + " · " : ""}${statLine}</span>
                  </div>`;
                  continue;
                }

                // done 阶段：只取有统计数据的
                if (ev.status === "done" && (listed || saved || candidates)) {
                  doneCount++;
                  let statLine = "";
                  if (ev.stage === "prepare_tasks" || kind === "collect") {
                    statLine = `已列 ${listed} · 候选 ${candidates}`;
                  } else if (ev.stage === "save_pages" || kind === "download") {
                    statLine = `已列 ${listed} · 已抓 ${fetched} · 已保存 ${saved}`;
                  } else if (ev.stage === "parse_documents") {
                    statLine = `已保存 ${saved} · 候选 ${candidates}`;
                  } else {
                    statLine = listed ? `已列 ${listed} · 已保存 ${saved}` : `完成`;
                  }
                  currentEntry = (currentEntry || "") + `<div class="progress-log-item">
                    <span class="progress-log-stage">${ev.stage}</span>
                    <span class="progress-log-msg">${taskLabel ? taskLabel + " · " : ""}${statLine}</span>
                  </div>`;
                  if (doneCount >= maxDone) break;
                }
              }
              return currentEntry || "";
            })()}
          </div>` : `
          <div class="progress-hint">
            ${progress.current_task_label ? `当前：${progress.current_task_label} · ` : ""}
            ${num(progress.downloaded_count)} 页已保存
          </div>`}
        </div>
      </section>` : ""}

      <!-- 最近任务 -->
      <section class="jobs-section animate-in delay-4">
        <p class="section-label">最近任务</p>
        <div class="jobs-card">
          <div class="jobs-header">
            <span class="jobs-header-title">历史记录</span>
            <a class="jobs-header-link" onclick="switchPanel('tasks')" role="button">查看全部</a>
          </div>
          ${recentJobs.length === 0 ? `<div style="padding:24px 0;text-align:center;color:var(--text-faint)">暂无任务记录</div>` : `
          <ul class="job-list">
            ${recentJobs.slice(0, 5).map((job) => {
              const s = String(job.status || "");
              const isTerm = isTerminal(s);
              const isFail = s === "failed";
              const isSucc = s === "success" || s === "success_with_warnings";
              const exported = job.job_type === "export_excel" && job.summary;
              const meta = exported
                ? `<span class="badge ${isFail ? "failed" : "ready"}"><span class="badge-dot"></span>${num(job.summary.new_records)} 条</span>`
                : isFail
                ? `<span class="badge failed"><span class="badge-dot"></span>失败</span>`
                : isTerm
                ? `<span class="badge ready"><span class="badge-dot"></span>${num(job.persisted_count)} 条</span>`
                : `<span class="job-count">${num(job.downloaded_count)} 条</span>`;
              return `<li class="job-item">
                <span class="job-status-dot ${stateDotClass(s)}"></span>
                <div class="job-info">
                  <div class="job-type">${jobTypeLabel(job.job_type)}</div>
                  <div class="job-time">${formatJobTime(job.created_at)} · ${formatTimeAgo(job.created_at)}</div>
                </div>
                ${meta}
              </li>`;
            }).join("")}
          </ul>`}
        </div>
      </section>

      <!-- 运行环境 -->
      <section class="animate-in delay-5">
        <p class="section-label">运行环境</p>
        <div class="card">
          <div style="font-size:15px;font-weight:600;color:var(--text);margin-bottom:4px">${headline}</div>
          <div style="font-size:13px;color:var(--text-muted);margin-bottom:8px">${browserState}</div>
          ${runtimeIssues.map((d) => `<div style="font-size:12px;color:var(--text-faint);margin-bottom:2px">${d}</div>`).join("")}
        </div>
      </section>
    `;

    $("#btn-oneclick").addEventListener("click", handleOneClick);
    $("#btn-historical").addEventListener("click", showHistoricalModal);
    $("#btn-import").addEventListener("click", handleManualImport);
    $("#btn-export").addEventListener("click", handleExport);
  }

  /* ── Tasks ── */
  function renderTasks() {
    const el = $("#panel-tasks");
    el.innerHTML = `
      <div class="animate-in"><h1 class="page-title">任务</h1></div>
      <div class="card animate-in delay-1">
        <div style="color:var(--text-muted);font-size:13px;padding:24px 0;text-align:center">加载中...</div>
      </div>
    `;
    loadJobs();
  }

  async function loadJobs() {
    try {
      const data = await API.listJobs(50);
      jobs = Array.isArray(data.jobs) ? data.jobs : [];
      renderTasksList();
    } catch (e) {
      $("#panel-tasks .card").innerHTML = `<div class="alert alert-danger">加载失败: ${e.message}</div>`;
    }
  }

  function renderTasksList() {
    const el = $("#panel-tasks .card");
    if (!jobs.length) {
      el.innerHTML = `<div style="padding:24px 0;text-align:center;color:var(--text-faint)">暂无任务</div>`;
      return;
    }
    el.innerHTML = `
      <ul class="job-list">
        ${jobs.map((job) => {
          const s = String(job.status || "");
          const meta = job.job_type === "export_excel" && job.summary
            ? `新增 ${num(job.summary.new_records)} · 变更 ${num(job.summary.changed_records)}`
            : `已保存 ${num(job.downloaded_count)} · 已写入 ${num(job.persisted_count)} · 异常 ${num(job.exception_count)}`;
          return `<li class="job-item">
            <span class="job-status-dot ${stateDotClass(s)}"></span>
            <div class="job-info">
              <div class="job-type">${jobTypeLabel(job.job_type)} · ${jobStatusLabel(job.status)}</div>
              <div class="job-time">${formatJobTime(job.created_at)}</div>
              <div class="job-time">${meta}</div>
            </div>
          </li>`;
        }).join("")}
      </ul>
    `;
  }

  /* ── Records ── */
  function renderRecordsLayout() {
    const el = $("#panel-records");
    el.innerHTML = `
      <div class="animate-in"><h1 class="page-title">记录</h1></div>

      <!-- 筛选 -->
      <section class="card animate-in delay-1" style="margin-bottom:var(--space-4)">
        <div class="form-row">
          <div class="form-group">
            <label>状态</label>
            <select id="filter-state">
              <option value="all">全部</option>
              <option value="ready">已录入</option>
              <option value="pending_mapping">待补映射</option>
              <option value="mapping_conflict">映射冲突</option>
              <option value="skipped">已跳过</option>
              <option value="parse_failed">解析失败</option>
              <option value="postprocess_failed">处理失败</option>
            </select>
          </div>
          <div class="form-group">
            <label>项目类型</label>
            <select id="filter-project-type">
              <option value="all">全部</option>
              <option value="equity_transfer">股权转让</option>
              <option value="physical_asset">实物资产</option>
              <option value="capital_increase">增资扩股</option>
              <option value="pre_disclosure">预披露</option>
            </select>
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>关键词</label>
            <input type="text" id="filter-keyword" placeholder="项目编号或名称">
          </div>
          <div class="form-group">
            <label>日期范围</label>
            <div style="display:flex;gap:8px;align-items:center">
              <input type="date" id="filter-date-from" style="flex:1">
              <span style="color:var(--text-muted)">至</span>
              <input type="date" id="filter-date-to" style="flex:1">
            </div>
          </div>
        </div>
        <div style="display:flex;gap:var(--space-3)">
          <button class="btn btn-primary" id="btn-records-search">查询</button>
          <button class="btn" id="btn-records-export">导出 Excel</button>
        </div>
      </section>

      <!-- 统计 -->
      <section class="animate-in delay-2" style="margin-bottom:var(--space-4)">
        <div class="stats-grid" style="grid-template-columns:repeat(5,1fr)">
          ${["ready","pending_mapping","mapping_conflict","parse_failed","postprocess_failed"].map(k =>
            `<div class="stat-card" id="stat-${k}">
              <div class="stat-label">${k==="ready"?"已录入":k==="pending_mapping"?"待补映射":k==="mapping_conflict"?"映射冲突":k==="parse_failed"?"解析失败":"处理失败"}</div>
              <div class="stat-value" style="font-size:20px" id="statval-${k}">—</div>
            </div>`
          ).join("")}
        </div>
      </section>

      <!-- 表格 -->
      <section class="animate-in delay-3">
        <div class="card" id="records-table-card">
          <div class="table-wrap" id="records-table-wrap" style="overflow-x:auto">
            <table id="records-table" style="min-width:100%">
              <thead id="records-thead"></thead>
              <tbody id="records-tbody"></tbody>
            </table>
          </div>
          <div id="records-pagination" style="margin-top:var(--space-4);display:flex;justify-content:space-between;align-items:center">
            <span id="records-count" style="font-size:13px;color:var(--text-muted)"></span>
            <div style="display:flex;gap:var(--space-2);align-items:center">
              <button class="btn btn-sm" id="btn-records-prev">上一页</button>
              <span class="job-count" id="records-page-info"></span>
              <button class="btn btn-sm" id="btn-records-next">下一页</button>
            </div>
          </div>
        </div>
      </section>
    `;

    // 筛选器事件只绑定一次
    function applyRecordFilters() {
      recordFilters = {
        state: $("#filter-state").value,
        project_type: $("#filter-project-type").value,
        keyword: $("#filter-keyword").value.trim(),
        date_from: $("#filter-date-from").value,
        date_to: $("#filter-date-to").value,
      };
      recordPage = 1;
      loadRecords();
    }
    $("#btn-records-search").addEventListener("click", applyRecordFilters);
    $("#filter-state").addEventListener("change", applyRecordFilters);
    $("#filter-project-type").addEventListener("change", applyRecordFilters);
    $("#filter-keyword").addEventListener("keydown", (e) => { if (e.key === "Enter") applyRecordFilters(); });
    $("#filter-date-from").addEventListener("keydown", (e) => { if (e.key === "Enter") applyRecordFilters(); });
    $("#filter-date-to").addEventListener("keydown", (e) => { if (e.key === "Enter") applyRecordFilters(); });
    $("#btn-records-export").addEventListener("click", handleExport);
    $("#btn-records-prev").addEventListener("click", () => { if (recordPage > 1) { recordPage--; loadRecords(); }});
    $("#btn-records-next").addEventListener("click", () => { recordPage++; loadRecords(); });
  }

  function updateRecordsTable() {
    const summary = records.summary || {};
    const totalCount = num(summary.total_count);
    const visibleCount = num(summary.visible_count);
    const pageCount = num(summary.page_count);
    const hasMore = recordPage < pageCount;
    const rows = records.rows || [];
    const columns = records.columns || [];

    // 动态生成表头（状态列 + API返回的columns + 最近更新）
    const thead = $("#records-thead");
    const allHeaders = ["状态", ...columns, "最近更新"];
    thead.innerHTML = `<tr>${allHeaders.map(h => `<th>${h}</th>`).join("")}</tr>`;

    // 更新筛选器选中状态
    $("#filter-state").value = recordFilters.state;
    $("#filter-project-type").value = recordFilters.project_type;
    $("#filter-keyword").value = recordFilters.keyword || "";
    $("#filter-date-from").value = recordFilters.date_from || "";
    $("#filter-date-to").value = recordFilters.date_to || "";

    // 更新统计数字（始终用全局计数，不受当前过滤条件影响）
    const globalCounts = overview.record_state_counts || {};
    ["ready","pending_mapping","mapping_conflict","parse_failed","postprocess_failed"].forEach(k => {
      const el = document.getElementById("statval-" + k);
      if (el) el.textContent = num(globalCounts[k]);
    });

    // 更新表格
    const tbody = $("#records-tbody");
    if (rows.length === 0) {
      tbody.innerHTML = `<tr><td colspan="${allHeaders.length}" style="padding:32px 0;text-align:center;color:var(--text-faint)">暂无记录</td></tr>`;
    } else {
      tbody.innerHTML = rows.map((row) => {
        const badgeClass = row.state === "ready" ? "ready"
          : row.state === "pending_mapping" || row.state === "mapping_conflict" ? "pending"
          : row.state === "skipped" ? "skipped" : "failed";
        const vals = row.values || {};
        const cells = [
          `<td><span class="badge ${badgeClass}"><span class="badge-dot"></span>${row.status_label || row.state}</span></td>`,
          ...columns.map(col => `<td>${vals[col] != null ? vals[col] : "—"}</td>`),
          `<td style="white-space:nowrap">${row.updated_at ? formatJobTime(row.updated_at) : "—"}</td>`,
        ];
        return `<tr>${cells.join("")}</tr>`;
      }).join("");
    }

    // 更新分页
    $("#records-count").textContent = `共 ${totalCount} 条${hasMore ? `，显示 ${visibleCount} 条` : ""}`;
    $("#records-page-info").textContent = `第 ${recordPage} / ${pageCount || 1} 页`;
    $("#btn-records-prev").disabled = recordPage <= 1;
    $("#btn-records-next").disabled = !hasMore;
  }

  async function loadRecords() {
    try {
      const data = await API.listRecords({ ...recordFilters, page: recordPage, page_size: recordPageSize });
      records = data;
      updateRecordsTable();
    } catch (e) {
      console.error("Records load failed:", e);
    }
  }

  function renderRecords() {
    renderRecordsLayout();
    loadRecords();
  }

  /* ── Mappings ── */
  function renderMappings() {
    const el = $("#panel-mappings");
    const pending = Array.isArray(mappings.pending) ? mappings.pending : [];
    const entries = Array.isArray(mappings.entries) ? mappings.entries : [];

    el.innerHTML = `
      <div class="animate-in"><h1 class="page-title">映射</h1></div>

      <!-- 待补映射 -->
      <section class="card animate-in delay-1" style="margin-bottom:var(--space-4)">
        <div class="jobs-header">
          <span class="jobs-header-title">待补映射</span>
          <button class="btn btn-sm btn-primary" id="btn-reprocess-all">全部回刷</button>
        </div>
        ${pending.length === 0
          ? `<div style="padding:24px 0;text-align:center;color:var(--text-faint)">没有待补映射的记录</div>`
          : `<div class="table-wrap" style="margin-top:var(--space-4)">
          <table>
            <thead><tr><th>项目编号</th><th>公司名称</th><th>状态</th><th>缺失字段</th></tr></thead>
            <tbody>
              ${pending.slice(0, 20).map((item) => {
                const gapLabel = Array.isArray(item.gap_codes) ? item.gap_codes.join("、") : "—";
                return `<tr>
                  <td style="font-family:var(--font-mono);font-size:12px">${item.project_code || "—"}</td>
                  <td>${item.company_name || "—"}</td>
                  <td><span class="badge pending"><span class="badge-dot"></span>${item.status_label || "待补映射"}</span></td>
                  <td style="font-size:12px;color:var(--text-muted)">${gapLabel}</td>
                </tr>`;
              }).join("")}
            </tbody>
          </table>
        </div>
        ${pending.length > 20 ? `<div style="margin-top:var(--space-3);font-size:12px;color:var(--text-faint)">还有 ${pending.length - 20} 条未显示</div>` : ""}`
        }
      </section>

      <!-- 已保存规则 -->
      <section class="card animate-in delay-2">
        <div class="jobs-header">
          <span class="jobs-header-title">已保存规则</span>
          <span style="font-size:13px;color:var(--text-muted)">${entries.length} 条</span>
        </div>
        ${entries.length === 0
          ? `<div style="padding:24px 0;text-align:center;color:var(--text-faint)">暂无保存的映射规则</div>`
          : `<div class="table-wrap" style="margin-top:var(--space-4)">
          <table>
            <thead><tr><th>来源名称</th><th>分组</th><th>来源类型</th></tr></thead>
            <tbody>
              ${entries.map((e) => `<tr>
                <td>${e.company_name || "—"}</td>
                <td>${e.group_name || "—"}</td>
                <td>${e.source_type || "—"}</td>
              </tr>`).join("")}
            </tbody>
          </table>
        </div>`}
      </section>
    `;

    $("#btn-reprocess-all")?.addEventListener("click", async () => {
      try {
        errorMsg = "";
        await API.reprocessPendingMappings();
        await loadMappings();
        await refresh();
      } catch (e) {
        errorMsg = `回刷失败: ${e.message}`;
        render();
      }
    });
  }

  async function loadMappings() {
    try {
      mappings = await API.listMappings();
      if (currentPanel === "mappings") renderMappings();
    } catch (e) {
      console.error("Mappings load failed:", e);
    }
  }

  /* ── Settings ── */
  function renderSettings() {
    const el = $("#panel-settings");
    el.innerHTML = `
      <div class="animate-in"><h1 class="page-title">设置</h1></div>
      <section class="card animate-in delay-1">
        <p class="section-label">基本设置</p>
        <div style="font-size:13px;color:var(--text-muted);padding:12px 0">加载中...</div>
      </section>
    `;
    loadSettings();
  }

  async function loadSettings() {
    try {
      settings = await API.getSettingsBasic() || {};
      renderSettingsForm();
    } catch (e) {
      $("#panel-settings .card").innerHTML = `<div class="alert alert-danger">加载失败: ${e.message}</div>`;
    }
  }

  function renderSettingsForm() {
    const s = settings;
    const el = $("#panel-settings .card");
    el.innerHTML = `
      <p class="section-label">基本设置</p>
      <div class="form-row">
        <div class="form-group">
          <label>默认交易所</label>
          <input type="text" value="${s.default_exchange || ""}" readonly>
        </div>
        <div class="form-group">
          <label>默认项目类型</label>
          <input type="text" value="${s.default_project_type || ""}" readonly>
        </div>
      </div>
      <div class="form-row">
        <div class="form-group">
          <label>默认并发数</label>
          <input type="text" value="${s.default_concurrency || ""}" readonly>
        </div>
        <div class="form-group">
          <label>归档目录</label>
          <input type="text" value="${s.archive_root || ""}" readonly>
        </div>
      </div>

      <p class="section-label" style="margin-top:var(--space-6)">运行环境</p>
      <div style="display:flex;gap:var(--space-3)">
        <button class="btn btn-primary" id="btn-install-browser">安装浏览器</button>
      </div>
    `;

    $("#btn-install-browser")?.addEventListener("click", async () => {
      try {
        errorMsg = "";
        await API.installBrowser();
        await loadSettings();
      } catch (e) {
        errorMsg = `安装失败: ${e.message}`;
        render();
      }
    });
  }

  /* ── Actions ── */
  async function handleOneClick(payload = {}) {
    try {
      errorMsg = "";
      await API.runOneClick(payload);
      await refresh();
    } catch (e) {
      errorMsg = `一键执行失败: ${e.message}`;
      render();
    }
  }

  function showHistoricalModal() {
    const modal = document.createElement("div");
    modal.id = "modal-historical";
    modal.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,0.4);display:flex;align-items:center;justify-content:center;z-index:1000";
    modal.innerHTML = `
      <div style="background:var(--surface);border-radius:var(--radius);padding:var(--space-8);width:380px;box-shadow:var(--shadow)">
        <h2 style="font-family:var(--font-serif);font-size:20px;margin-bottom:var(--space-6)">历史区间任务</h2>
        <div class="form-group" style="margin-bottom:var(--space-4)">
          <label>开始日期</label>
          <input type="date" id="hist-start" style="width:100%">
        </div>
        <div class="form-group" style="margin-bottom:var(--space-6)">
          <label>结束日期</label>
          <input type="date" id="hist-end" style="width:100%">
        </div>
        <div style="display:flex;gap:var(--space-3);justify-content:flex-end">
          <button id="hist-cancel" class="btn">取消</button>
          <button id="hist-confirm" class="btn btn-primary">确定</button>
        </div>
      </div>`;
    document.body.appendChild(modal);

    $("#hist-cancel", modal).addEventListener("click", () => document.body.removeChild(modal));
    $("#hist-confirm", modal).addEventListener("click", () => {
      const startDate = $("#hist-start", modal).value;
      const endDate = $("#hist-end", modal).value;
      document.body.removeChild(modal);
      const payload = {};
      if (startDate) payload.start_date = startDate;
      if (endDate) payload.end_date = endDate;
      handleOneClick(payload);
    });
  }

  async function handleManualImport() {
    const dir = prompt("请输入要导入的文件目录路径：");
    if (!dir) return;
    try {
      errorMsg = "";
      await API.runManualImport(dir.trim());
      await refresh();
    } catch (e) {
      errorMsg = `手动导入失败: ${e.message}`;
      render();
    }
  }

  async function handleExport() {
    try {
      errorMsg = "";
      await API.runExport("listing", "rebuild");
      await refresh();
    } catch (e) {
      errorMsg = `导出失败: ${e.message}`;
      render();
    }
  }

  /* ── Data Fetch ── */
  async function refresh() {
    try {
      overview = await API.getOverview();
      if (currentPanel === "overview") {
        const latestJob = overview.latest_job;
        if (latestJob && isActive(latestJob.status)) {
          const eventsData = await API.getJobEvents(latestJob.job_id);
          currentEvents = Array.isArray(eventsData.events) ? eventsData.events : [];
        } else {
          currentEvents = [];
        }
        renderOverview();
      }
    } catch (e) {
      console.error("Overview refresh failed:", e);
    }
  }

  function startPoll() {
    refresh();
    pollTimer = setInterval(refresh, 3000);
  }

  function stopPoll() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  /* ── Navigation ── */
  window.switchPanel = function (panel) {
    currentPanel = panel;
    stopPoll();
    render();
    if (panel === "mappings") loadMappings();
    else if (panel === "settings") loadSettings();
    startPoll();
  };

  /* ── Init ── */
  function init() {
    document.addEventListener("DOMContentLoaded", () => {
      render();
      startPoll();
    });
  }

  init();
})();
