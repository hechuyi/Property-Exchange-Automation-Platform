let desktopState = null;
let apiClient = null;
let waitForDesktopBackendAvailability = null;
let buildRecordsQuery = null;
let formatRecordsSummary = null;
let buildRecordsTableMarkup = null;
let isMappingInteractionActive = null;
let runMappingUpsertFlow = null;
let runBatchMappingUpsertFlow = null;
let formatMappingConflictSummary = null;
let createPollLoop = null;
let startPolling = null;
let currentPanel = "overview";
let selectedJobId = "";
let selectedPendingRecordId = "";
let selectedPendingCompanyName = "";
let pendingMappingsCache = [];
let mappingDraftState = [];
let mappingEntriesCache = [];
let mappingEntriesExpanded = true;
let overviewCache = null;
let runtimeBrowser = null;
let productReadiness = null;
let browserInstallState = null;
let startupGateDismissed = false;
let startupAutoInstallAttempted = false;
let actionDefaultsInitialized = false;
let backendRestartInProgress = false;
let mappingConflictPromptResolver = null;

const JOB_TYPE_LABELS = {
  one_click: "一键执行",
  download_ingest: "历史区间任务",
  export_excel: "导出 Excel",
  manual_import: "手动导入解析",
  mapping_refresh: "映射回刷",
};

const JOB_STATUS_LABELS = {
  running: "执行中",
  success: "已完成",
  success_with_warnings: "已完成，但有待处理项",
  interrupted: "已中断",
  failed: "执行失败",
};

const STAGE_LABELS = {
  prepare_tasks: "正在扫描网页",
  save_pages: "正在保存网页",
  manual_import_scan: "正在整理导入文件",
  reprocessing: "正在重处理记录",
  archive_pending: "正在存档",
  exporting: "正在导出 Excel",
  downloaded: "网页已保存",
  queued_for_parse: "等待写入",
  persisted: "已写入数据",
  skipped: "已跳过",
  failed: "处理失败",
};

const RECORD_STATE_LABELS = {
  ready: "已录入",
  pending_mapping: "待补映射",
  skipped: "已跳过",
  parse_failed: "解析失败",
  postprocess_failed: "处理失败",
  conflict: "归档重名",
};

const MAPPING_RULE_CONFIG = {
  transferor_group: {
    matchField: "transferor",
    targetField: "group_name",
    sourceLabel: "转让方名称",
    targetLabel: "集团名称",
    title: "转让方 -> 集团",
  },
  transferor_type: {
    matchField: "transferor",
    targetField: "source_type",
    sourceLabel: "转让方名称",
    targetLabel: "类型",
    title: "转让方 -> 类型",
  },
  group_group: {
    matchField: "group",
    targetField: "group_name",
    sourceLabel: "当前集团名称",
    targetLabel: "归并后的集团名称",
    title: "集团 -> 集团",
  },
  group_type: {
    matchField: "group",
    targetField: "source_type",
    sourceLabel: "集团名称",
    targetLabel: "类型",
    title: "集团 -> 类型",
  },
};

function $(id) {
  return document.getElementById(id);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

async function api(path, options = {}) {
  if (!apiClient) {
    throw new Error("前端 API 客户端尚未初始化");
  }
  return apiClient(path, options);
}

function setStatus(id, text, isError = false) {
  const node = $(id);
  if (!node) return;
  node.textContent = text || "";
  node.style.color = isError ? "#a13b1d" : "";
}

function statusLabel(state) {
  return RECORD_STATE_LABELS[String(state || "").trim()] || String(state || "").trim() || "未知";
}

function jobTypeLabel(jobType) {
  return JOB_TYPE_LABELS[String(jobType || "").trim()] || String(jobType || "").trim() || "任务";
}

function jobStatusLabel(status) {
  return JOB_STATUS_LABELS[String(status || "").trim()] || String(status || "").trim() || "未知";
}

function stageLabel(stage) {
  return STAGE_LABELS[String(stage || "").trim()] || String(stage || "").trim() || "任务更新";
}

function formatRuntimeIssues(readiness) {
  const issues = Array.isArray(readiness?.issues) ? readiness.issues : [];
  return issues
    .map((item) => String(item.message || "").trim())
    .filter(Boolean)
    .join("\n");
}

function setDownloadActionAvailability(isReady, reason = "") {
  const latestJobStatus = String(overviewCache?.latest_job?.status || "").trim();
  const hasRunningJob = backendRestartInProgress || latestJobStatus === "running";
  const runNode = $("runOneClickBtn");
  if (runNode) {
    runNode.disabled = !isReady || hasRunningJob;
    runNode.title = !isReady
      ? reason
      : hasRunningJob
        ? "当前已有执行中的任务，请先等待完成或点击强制停止"
        : "";
  }
  const stopNode = $("forceStopBtn");
  if (stopNode) {
    stopNode.disabled = !hasRunningJob;
    stopNode.title = hasRunningJob ? "强制停止当前后台任务并重启后端" : "当前没有执行中的任务";
  }
}

function sleep(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

async function waitForBackendAvailability(timeoutMs = 60000) {
  if (!waitForDesktopBackendAvailability || !desktopState) {
    throw new Error("后台启动检查器尚未初始化");
  }
  await waitForDesktopBackendAvailability({
    baseUrl: desktopState.backendUrl,
    apiToken: desktopState.backendApiToken,
    timeoutMs,
    sleepFn: sleep,
  });
}

function formatInstallState(installState) {
  if (!installState) {
    return "";
  }
  const status = String(installState.status || "idle");
  if (status === "running") {
    return "浏览器正在后台安装";
  }
  if (status === "succeeded") {
    return String(installState.message || "浏览器已安装");
  }
  if (status === "failed") {
    return String(installState.message || "浏览器安装失败");
  }
  return "";
}

function formatLocalDate(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function countValue(value) {
  const numeric = Number(value || 0);
  return Number.isFinite(numeric) ? numeric : 0;
}

function jobDateRangeText(job) {
  const metadata = job && typeof job.metadata === "object" ? job.metadata : {};
  const start = String(metadata.start_date || "").trim();
  const end = String(metadata.end_date || "").trim();
  if (start && end) {
    return start === end ? start : `${start} 至 ${end}`;
  }
  return start || end || "";
}

function latestStageSummary(progress) {
  return progress && typeof progress.latest_stage_summary === "object" && progress.latest_stage_summary
    ? progress.latest_stage_summary
    : {};
}

function isMappingEditorActive() {
  return isMappingInteractionActive({
    currentPanel,
    activeElement: document.activeElement,
    conflictDialogOpen: !$("mappingConflictDialog")?.classList.contains("hidden"),
  });
}

function defaultDates() {
  const today = new Date();
  const todayText = formatLocalDate(today);
  $("startDateInput").value = todayText;
  $("endDateInput").value = todayText;
  $("exportDateFromInput").value = todayText;
  $("exportDateToInput").value = todayText;
  const hintNode = $("todayHint");
  if (hintNode) {
    hintNode.textContent = `今天是 ${todayText}`;
  }
}

async function refreshCurrentPanel() {
  if (currentPanel === "tasks") {
    await Promise.all([loadJobs(), loadJobEvents()]);
    return;
  }
  if (currentPanel === "records") {
    await loadRecords();
    return;
  }
  if (currentPanel === "mappings") {
    await loadMappings();
    return;
  }
  if (currentPanel === "settings") {
    await loadSettings();
  }
}

async function switchPanel(panelName) {
  currentPanel = panelName;
  document.querySelectorAll(".rail-button").forEach((node) => {
    node.classList.toggle("active", node.dataset.panel === panelName);
  });
  document.querySelectorAll(".panel").forEach((node) => {
    node.classList.toggle("active", node.id === `panel-${panelName}`);
  });
  try {
    await refreshCurrentPanel();
  } catch (error) {
    setStatus("settingsResult", `加载失败：${error.message}`, true);
  }
}

function progressPreset(progress) {
  const explicitPercent = Number(progress?.phase_percent || 0);
  if (explicitPercent > 0) {
    return {
      width: Math.max(4, Math.min(100, explicitPercent)),
      active: !["completed", "completed_with_warnings", "failed"].includes(String(progress?.phase_code || "")),
    };
  }
  const phaseCode = String(progress?.phase_code || "");
  if (phaseCode === "prepare_tasks") {
    return { width: 24, active: true };
  }
  if (phaseCode === "save_pages") {
    return { width: 56, active: true };
  }
  if (phaseCode === "archive_pending") {
    return { width: 72, active: true };
  }
  if (phaseCode === "exporting") {
    return { width: 92, active: true };
  }
  if (phaseCode === "completed" || phaseCode === "completed_with_warnings") {
    return { width: 100, active: false };
  }
  if (phaseCode === "failed") {
    return { width: 100, active: false };
  }
  return { width: 10, active: false };
}

function renderProgress(progress, latestJob = null) {
  const data = progress || {};
  const phaseNode = $("progressPhase");
  const percentNode = $("progressPercent");
  const metaNode = $("progressMeta");
  const hintNode = $("progressHint");
  const trackFill = $("progressTrackFill");
  if (!phaseNode || !percentNode || !metaNode || !hintNode || !trackFill) {
    return;
  }

  const phaseLabel = String(data.phase_label || "暂无任务");
  const downloaded = countValue(data.downloaded_count);
  const persisted = countValue(data.persisted_count);
  const skipped = countValue(data.skipped_count);
  const pending = countValue(data.pending_mapping_count);
  const exceptions = countValue(data.exception_count);
  const archivePending = countValue(data.archive_pending_count);
  const archiveCompleted = countValue(data.archive_completed_count);
  const currentTaskLabel = String(data.current_task_label || "").trim();
  const taskIndex = countValue(data.task_index);
  const taskTotal = countValue(data.task_total);
  const overallCounts = overviewCache?.record_state_counts || {};
  const overallPending = countValue(overviewCache?.pending_mapping_count || overallCounts.pending_mapping);
  const overallSkipped = countValue(overallCounts.skipped);
  const stageSummary = latestStageSummary(data);
  const exportSummary = latestJob && typeof latestJob.summary === "object" ? latestJob.summary : {};
  const isExportJob = latestJob?.job_type === "export_excel";
  const dateRangeText = jobDateRangeText(latestJob);
  const listedCount = countValue(stageSummary.listed);
  const candidateCount = countValue(stageSummary.detail_candidates);
  const collectedCount = countValue(stageSummary.collected_candidates);
  const fetchedCount = countValue(stageSummary.detail_fetched);
  const savedCount = Math.max(downloaded, countValue(stageSummary.saved));
  const detailDateSkippedCount = countValue(stageSummary.detail_date_skipped);
  const preset = progressPreset(data);

  phaseNode.textContent = phaseLabel;
  percentNode.textContent = `${Math.max(0, Math.min(100, Math.round(preset.width)))}%`;

  const phaseCode = String(data.phase_code || "");
  if (isExportJob && (phaseCode === "exporting" || phaseCode === "completed" || phaseCode === "completed_with_warnings" || phaseCode === "failed")) {
    const artifactCount = Array.isArray(exportSummary.artifacts) ? exportSummary.artifacts.length : 0;
    metaNode.textContent = [
      `新增 ${countValue(exportSummary.new_records)} 条`,
      `变更 ${countValue(exportSummary.changed_records)} 条`,
      `生成文件 ${artifactCount} 个`,
    ].join(" · ");
  } else if (phaseCode === "prepare_tasks") {
    metaNode.textContent = (taskTotal > 0
      ? [
        `扫描进度 ${Math.min(taskIndex, taskTotal)}/${taskTotal}`,
        dateRangeText ? `日期 ${dateRangeText}` : "",
        currentTaskLabel ? `当前对象 ${currentTaskLabel}` : "",
        listedCount > 0 ? `已读取列表 ${listedCount} 条` : "",
        candidateCount > 0 ? `候选 ${candidateCount} 条` : "",
        collectedCount > 0 ? `累计待处理 ${collectedCount} 条` : "",
      ]
      : [
        currentTaskLabel ? `当前对象 ${currentTaskLabel}` : "正在整理下载范围",
        listedCount > 0 ? `已列出 ${listedCount} 条` : "",
      ]).filter(Boolean).join(" · ");
  } else if (phaseCode === "save_pages") {
    metaNode.textContent = [
      taskTotal > 0 ? `任务进度 ${Math.min(taskIndex, taskTotal)}/${taskTotal}` : "",
      dateRangeText ? `日期 ${dateRangeText}` : "",
      currentTaskLabel ? `当前下载 ${currentTaskLabel}` : "",
      listedCount > 0 ? `已列出 ${listedCount} 条` : "",
      candidateCount > 0 ? `候选 ${candidateCount} 条` : "",
      collectedCount > 0 ? `累计待处理 ${collectedCount} 条` : "",
      fetchedCount > 0 ? `已抓取详情 ${fetchedCount} 条` : "",
      `已保存网页 ${savedCount} 条`,
      detailDateSkippedCount > 0 ? `日期不符 ${detailDateSkippedCount} 条` : "",
      pending > 0 ? `待补映射 ${pending} 条` : "",
      exceptions > 0 ? `异常 ${exceptions} 条` : "",
    ].filter(Boolean).join(" · ");
  } else if (phaseCode === "manual_import_scan") {
    metaNode.textContent = [
      taskTotal > 0 ? `候选文件 ${taskTotal} 个` : "",
      currentTaskLabel ? `当前对象 ${currentTaskLabel}` : "",
      `已处理文件 ${downloaded} 个`,
      pending > 0 ? `待补映射 ${pending} 条` : "",
      exceptions > 0 ? `异常 ${exceptions} 条` : "",
    ].filter(Boolean).join(" · ") || "正在整理手动导入文件";
  } else if (phaseCode === "reprocessing") {
    metaNode.textContent = [
      taskTotal > 0 ? `处理进度 ${Math.min(taskIndex, taskTotal)}/${taskTotal}` : "",
      currentTaskLabel ? `当前对象 ${currentTaskLabel}` : "",
      `已处理 ${downloaded} 条`,
      `已写回 ${persisted} 条`,
      pending > 0 ? `待补映射 ${pending} 条` : "",
      exceptions > 0 ? `异常 ${exceptions} 条` : "",
    ].filter(Boolean).join(" · ");
  } else if (phaseCode === "archive_pending") {
    metaNode.textContent = `已保存网页 ${downloaded} 条 · 待存档 ${archivePending} 条 · 已存档 ${archiveCompleted} 条 · 已跳过 ${skipped} 条 · 待补映射 ${pending} 条 · 异常 ${exceptions} 条`;
  } else if (phaseCode === "exporting") {
    metaNode.textContent = "正在根据当前日期范围重建导出文件。";
  } else if (
    (phaseCode === "completed" || phaseCode === "completed_with_warnings")
    && downloaded <= 0
    && persisted <= 0
    && exceptions <= 0
  ) {
    metaNode.textContent = [
      candidateCount > 0 ? `候选 ${candidateCount} 条` : "",
      detailDateSkippedCount > 0 ? `日期不符 ${detailDateSkippedCount} 条` : "",
      overallPending > 0 ? `当前待补映射 ${overallPending} 条` : "",
      overallSkipped > 0 ? `累计已跳过 ${overallSkipped} 条` : "",
    ].filter(Boolean).join(" · ") || "本次没有新增网页写入数据库";
  } else {
    metaNode.textContent = `已保存网页 ${downloaded} 条 · 已存档 ${archiveCompleted} 条 · 已跳过 ${skipped} 条 · 待补映射 ${pending} 条 · 异常 ${exceptions} 条`;
  }

  let hint = "暂无任务时，可以直接选择日期范围后执行一键任务。";
  if (isExportJob && phaseCode === "exporting") {
    hint = "系统正在按当前日期范围生成 Excel，完成后会写入导出目录并出现在任务页。";
  } else if (isExportJob && phaseCode === "completed") {
    hint = "导出已完成，可以从导出目录直接打开文件。";
  } else if (isExportJob && phaseCode === "completed_with_warnings") {
    hint = "导出已结束，但当前条件下没有形成新的导出文件。";
  } else if (isExportJob && phaseCode === "failed") {
    hint = "导出失败时，详细原因会出现在任务页的任务明细里。";
  } else if (phaseCode === "prepare_tasks") {
    hint = currentTaskLabel
      ? `系统正在扫描 ${currentTaskLabel} 的可下载页面。`
      : "系统正在扫描当前筛选范围内的可下载页面。";
  } else if (phaseCode === "save_pages") {
    hint = currentTaskLabel
      ? `系统正在下载并保存 ${currentTaskLabel} 的页面。`
      : "系统正在保存已扫描到的页面。";
  } else if (phaseCode === "manual_import_scan") {
    hint = "系统正在整理手动导入目录中的网页文件。";
  } else if (phaseCode === "reprocessing") {
    hint = latestJob?.job_type === "mapping_refresh"
      ? "系统正在按最新映射规则批量回刷已有记录。"
      : "系统正在解析手动导入的本地网页并写入数据库。";
  } else if (phaseCode === "archive_pending") {
    hint = "网页已下载完成，系统正在后台存档并写入数据。";
  } else if (phaseCode === "exporting") {
    hint = "导出期间可以继续浏览界面，生成文件后会写入导出目录。";
  } else if (phaseCode === "completed") {
    hint = downloaded <= 0 && persisted <= 0
      ? "最近一次任务已完成，但当前范围没有形成新的可录入结果。"
      : "最近一次任务已完成；如需表格，请点击导出 Excel。";
  } else if (phaseCode === "completed_with_warnings") {
    hint = "任务已完成，但仍有待补映射或失败项；请先处理后再导出 Excel。";
  } else if (phaseCode === "failed") {
    hint = "任务失败时，详细原因会出现在任务页的任务明细里。";
  }
  if (dateRangeText && phaseCode && !isExportJob) {
    hint = `${hint} 当前日期范围：${dateRangeText}。`;
  }
  if (latestJob?.job_id) {
    hintNode.textContent = `${hint} 当前任务：${jobTypeLabel(latestJob.job_type)}。`;
  } else {
    hintNode.textContent = hint;
  }

  trackFill.style.width = `${preset.width}%`;
  trackFill.classList.toggle("active", preset.active);
}

function renderOverview(data) {
  overviewCache = data;
  const progress = data.latest_progress || {};
  const counts = data.record_state_counts || {};
  const readyCount = countValue(counts.ready);
  const pendingCount = countValue(data.pending_mapping_count || counts.pending_mapping);
  const skippedCount = countValue(counts.skipped);
  const savedCount = Object.values(counts).reduce((sum, item) => sum + countValue(item), 0);
  $("statLatestStatus").textContent = String(progress.phase_label || "暂无任务");
  $("statDownloaded").textContent = String(savedCount);
  $("statPersisted").textContent = String(readyCount);
  $("statSkipped").textContent = String(skippedCount);
  $("statPendingMapping").textContent = String(pendingCount);
  renderProgress(progress, data.latest_job || null);
  applyRuntimeState(
    (data && data.browser_runtime) || null,
    (data && data.product_readiness) || null,
    (data && data.browser_install) || null,
  );
  setDownloadActionAvailability(
    Boolean(productReadiness && productReadiness.download_ready) && String(browserInstallState?.status || "") !== "running",
    "浏览器运行环境未就绪，请先在提示层或设置页完成安装",
  );
}

function renderJobs(jobs) {
  const container = $("jobList");
  if (!container) {
    return;
  }
  if (!jobs.length) {
    selectedJobId = "";
    container.innerHTML = '<div class="job-item">暂无任务</div>';
    return;
  }
  if (!selectedJobId || !jobs.some((job) => job.job_id === selectedJobId)) {
    selectedJobId = jobs[0].job_id;
  }
  container.innerHTML = jobs
    .map((job) => {
      const selected = job.job_id === selectedJobId ? "selected" : "";
      const skippedCount = Number(job.summary?.skipped_count || 0);
      const pendingCount = Number(job.summary?.pending_mapping_count || 0);
      const extraMeta = [];
      if (skippedCount > 0) {
        extraMeta.push(`已跳过 ${skippedCount}`);
      }
      if (pendingCount > 0) {
        extraMeta.push(`待补映射 ${pendingCount}`);
      }
      let countMeta = `已保存网页 ${Number(job.downloaded_count || 0)} · 已写入 ${Number(job.persisted_count || 0)} · 异常 ${Number(job.exception_count || 0)}`;
      if (job.job_type === "manual_import") {
        countMeta = `已处理文件 ${Number(job.downloaded_count || 0)} · 已写入 ${Number(job.persisted_count || 0)} · 异常 ${Number(job.exception_count || 0)}`;
      } else if (job.job_type === "export_excel") {
        const artifactCount = Array.isArray(job.summary?.artifacts) ? job.summary.artifacts.length : 0;
        countMeta = `新增 ${Number(job.summary?.new_records || 0)} · 变更 ${Number(job.summary?.changed_records || 0)} · 文件 ${artifactCount}`;
      } else if (job.job_type === "mapping_refresh") {
        countMeta = `已回刷 ${Number(job.downloaded_count || 0)} 条 · 已写回 ${Number(job.persisted_count || 0)} 条 · 异常 ${Number(job.exception_count || 0)}`;
      }
      return `
        <button class="job-item ${selected}" data-job-id="${escapeHtml(job.job_id)}">
          <div class="job-title">${escapeHtml(jobTypeLabel(job.job_type))} · ${escapeHtml(jobStatusLabel(job.status))}</div>
          <div class="job-meta">${escapeHtml(countMeta)}${extraMeta.length ? ` · ${escapeHtml(extraMeta.join(" · "))}` : ""}</div>
          <div class="job-meta">${escapeHtml(job.created_at || "")} → ${escapeHtml(job.updated_at || "")}</div>
        </button>
      `;
    })
    .join("");
  container.querySelectorAll("[data-job-id]").forEach((node) => {
    node.addEventListener("click", async () => {
      selectedJobId = node.dataset.jobId;
      await loadJobs();
      await loadJobEvents();
    });
  });
}

function eventClass(event) {
  const status = String(event.status || "");
  if (status === "skipped" || status === "pending_mapping") {
    return "warn";
  }
  if (event.error_type || status === "failed" || status === "parse_failed" || status === "postprocess_failed") {
    return "error";
  }
  return "ok";
}

function renderEvents(events) {
  const container = $("jobEvents");
  if (!container) {
    return;
  }
  if (!events.length) {
    container.innerHTML = '<div class="event-item">选择任务后查看明细</div>';
    return;
  }
  container.innerHTML = events
    .map((event) => {
      const cssClass = eventClass(event);
      const code = String(event.project_code || "").trim();
      const title =
        event.status === "skipped"
          ? code
            ? `已跳过 · ${code}`
            : "已跳过"
          : code
            ? `${stageLabel(event.stage)} · ${code}`
            : `${stageLabel(event.stage)}`;
      const payloadLabel = String(event.payload?.label || "").trim();
      const description =
        event.error_message ||
        payloadLabel ||
        statusLabel(event.status) ||
        "";
      return `
        <div class="event-item ${cssClass}">
          <div class="job-title">${escapeHtml(title)}</div>
          <div class="event-meta">${escapeHtml(event.event_ts || "")}</div>
          <div class="event-meta">${escapeHtml(description)}</div>
        </div>
      `;
    })
    .join("");
}

function pendingRecordCompany(record) {
  return record["转让方"] || record["融资方"] || record["转让方名称"] || record["融资方名称"] || "";
}

function buildDraftRuleKind(record) {
  return record["隶属集团"] ? "group_type" : "transferor_group";
}

function buildDraftSourceValue(ruleKind, record) {
  if (String(ruleKind || "").startsWith("group")) {
    return String(record["隶属集团"] || "").trim();
  }
  return String(pendingRecordCompany(record) || "").trim();
}

function mappingRuleScopeLabel(matchField, targetField) {
  const sourceLabel = matchField === "group" ? "集团名称" : "转让方名称";
  const targetLabel = targetField === "source_type" ? "类型" : "集团名称";
  return `${sourceLabel} -> ${targetLabel}`;
}

function existingMappingTarget(preview) {
  const entry = preview?.existing_entry || {};
  return String(
    preview?.target_field === "group_name"
      ? entry.group_name || ""
      : entry.source_type || "",
  ).trim();
}

function renderPendingMappingSummary() {
  const button = $("runPendingMappingRefreshBtn");
  const summaryNode = $("pendingMappingsSummary");
  const count = pendingMappingsCache.length;
  if (summaryNode) {
    summaryNode.textContent = count ? `当前 ${count} 条待补项` : "当前没有待补项";
  }
  if (!button) {
    return;
  }
  button.disabled = count === 0;
  button.textContent = count ? `一键重处理当前所有待补项（${count}）` : "一键重处理当前所有待补项";
  button.title = count ? `将重处理当前全部 ${count} 条待补映射记录` : "当前没有待补映射可重处理";
}

function closeMappingConflictDialog(confirmed) {
  const dialog = $("mappingConflictDialog");
  if (dialog) {
    dialog.classList.add("hidden");
  }
  if (!mappingConflictPromptResolver) {
    return;
  }
  const resolve = mappingConflictPromptResolver;
  mappingConflictPromptResolver = null;
  resolve(Boolean(confirmed));
}

function showMappingConflictDialog(preview) {
  const dialog = $("mappingConflictDialog");
  if (!dialog) {
    return Promise.resolve(
      window.confirm(
        formatMappingConflictSummary
          ? formatMappingConflictSummary(preview)
          : "已存在同来源映射规则，确认覆盖吗？",
      ),
    );
  }
  if (mappingConflictPromptResolver) {
    closeMappingConflictDialog(false);
  }
  $("mappingConflictRuleValue").textContent = mappingRuleScopeLabel(preview.match_field, preview.target_field);
  $("mappingConflictSourceValue").textContent = String(preview.source_name || "").trim() || "未填写";
  $("mappingConflictExistingValue").textContent = existingMappingTarget(preview) || "空值";
  $("mappingConflictNextValue").textContent = String(preview.target_value || "").trim() || "空值";
  $("mappingConflictImpactValue").textContent = `预计回刷 ${Number(preview.affected_count || 0)} 条记录，其中 ${Number(preview.affected_pending_count || 0)} 条仍是待补映射`;
  $("mappingConflictSummary").textContent = formatMappingConflictSummary
    ? formatMappingConflictSummary(preview)
    : "已存在同来源映射规则，覆盖后会按新规则重处理相关记录。";
  dialog.classList.remove("hidden");
  return new Promise((resolve) => {
    mappingConflictPromptResolver = resolve;
  });
}

function renderPendingMappings(payload) {
  const container = $("pendingMappings");
  if (!container) {
    return;
  }
  pendingMappingsCache = Array.isArray(payload.pending) ? payload.pending : [];
  renderPendingMappingSummary();
  if (!pendingMappingsCache.length) {
    container.innerHTML = '<div class="mapping-item">当前没有待补映射</div>';
    return;
  }
  container.innerHTML = pendingMappingsCache
    .map((item) => {
      const record = item.payload || {};
      const companyName = pendingRecordCompany(record);
      const groupName = record["隶属集团"] || "";
      return `
        <div class="mapping-item pending">
          <div class="mapping-title">${escapeHtml(item.project_code || "无编号")} · ${escapeHtml(record["项目名称"] || "未命名项目")}</div>
          <div class="job-meta">公司：${escapeHtml(companyName || "未识别")} · 当前集团：${escapeHtml(groupName || "空")}</div>
          <div class="inline-actions">
            <button class="action ghost" data-use-pending="${escapeHtml(item.record_id)}" data-company="${escapeHtml(companyName)}">导入规则</button>
            <button class="action primary" data-reprocess="${escapeHtml(item.record_id)}">仅重处理</button>
          </div>
        </div>
      `;
    })
    .join("");

  container.querySelectorAll("[data-use-pending]").forEach((node) => {
    node.addEventListener("click", async () => {
      selectedPendingRecordId = node.dataset.usePending;
      selectedPendingCompanyName = node.dataset.company || "";
      $("mappingRuleKindInput").value = "transferor_group";
      $("mappingSourceInput").value = selectedPendingCompanyName;
      $("mappingTargetInput").value = "";
      $("mappingNotesInput").value = "";
      syncMappingRuleLabels();
      await switchPanel("mappings");
    });
  });
  container.querySelectorAll("[data-reprocess]").forEach((node) => {
    node.addEventListener("click", async () => {
      await reprocessRecord(node.dataset.reprocess);
    });
  });
}

function renderMappingDrafts() {
  const container = $("mappingDrafts");
  if (!container) {
    return;
  }
  if (!mappingDraftState.length) {
    container.innerHTML = "";
    return;
  }
  container.innerHTML = mappingDraftState
    .map((draft, index) => `
      <div class="mapping-draft-item" data-draft-index="${index}">
        <div class="mapping-title">${escapeHtml(draft.project_code || "无编号")} · ${escapeHtml(draft.project_name || "未命名项目")}</div>
        <div class="job-meta">公司：${escapeHtml(draft.company_name || "未识别")} · 当前集团：${escapeHtml(draft.group_name || "空")}</div>
        <div class="mapping-draft-grid">
          <label>
            <span>规则类型</span>
            <select data-draft-field="ruleKind">
              ${Object.entries(MAPPING_RULE_CONFIG)
                .map(([ruleKey, config]) => `<option value="${escapeHtml(ruleKey)}" ${ruleKey === draft.ruleKind ? "selected" : ""}>${escapeHtml(config.title)}</option>`)
                .join("")}
            </select>
          </label>
          <label>
            <span>来源名称</span>
            <input type="text" data-draft-field="sourceName" value="${escapeHtml(draft.sourceName || "")}" />
          </label>
          <label>
            <span>目标值</span>
            <input type="text" data-draft-field="targetValue" value="${escapeHtml(draft.targetValue || "")}" />
          </label>
          <label>
            <span>备注</span>
            <input type="text" data-draft-field="notes" value="${escapeHtml(draft.notes || "")}" />
          </label>
        </div>
      </div>
    `)
    .join("");

  container.querySelectorAll("[data-draft-index]").forEach((node) => {
    const index = Number(node.dataset.draftIndex || -1);
    node.querySelectorAll("[data-draft-field]").forEach((fieldNode) => {
      const syncDraftValue = () => {
        if (!Number.isInteger(index) || !mappingDraftState[index]) {
          return;
        }
        const fieldName = fieldNode.dataset.draftField;
        if (fieldName === "ruleKind") {
          mappingDraftState[index].ruleKind = fieldNode.value;
          if (!mappingDraftState[index].sourceName || mappingDraftState[index].sourceName === buildDraftSourceValue(mappingDraftState[index].previousRuleKind, mappingDraftState[index].rawRecord)) {
            mappingDraftState[index].sourceName = buildDraftSourceValue(fieldNode.value, mappingDraftState[index].rawRecord);
          }
          mappingDraftState[index].previousRuleKind = fieldNode.value;
          renderMappingDrafts();
          return;
        }
        mappingDraftState[index][fieldName] = fieldNode.value;
      };
      if (fieldNode.tagName === "SELECT") {
        fieldNode.addEventListener("change", syncDraftValue);
      } else {
        fieldNode.addEventListener("input", syncDraftValue);
        fieldNode.addEventListener("change", syncDraftValue);
      }
    });
  });
}

function renderMappingEntries(entries) {
  const container = $("mappingEntries");
  const summaryNode = $("mappingEntriesSummary");
  const tableWrap = $("mappingEntriesTableWrap");
  const keyword = String($("mappingSearchInput")?.value || "").trim().toLowerCase();
  const ruleKind = String($("mappingRuleFilterInput")?.value || "all").trim();
  if (!container) {
    return;
  }
  mappingEntriesCache = Array.isArray(entries) ? entries : [];
  const filtered = mappingEntriesCache.filter((entry) => {
    const meta = entry.metadata || {};
    const entryKind = `${meta.match_field || "transferor"}_${meta.target_field === "group_name" ? "group" : "type"}`;
    if (ruleKind !== "all" && entryKind !== ruleKind) {
      return false;
    }
    if (!keyword) {
      return true;
    }
    const targetValue = entry.group_name || entry.source_type || "";
    const haystack = [
      entry.company_name || "",
      targetValue,
      meta.notes || "",
      meta.match_field || "",
      meta.target_field || "",
    ].join(" ").toLowerCase();
    return haystack.includes(keyword);
  });
  if (summaryNode) {
    summaryNode.textContent = keyword
      ? `共 ${mappingEntriesCache.length} 条规则，当前命中 ${filtered.length} 条`
      : `共 ${mappingEntriesCache.length} 条规则，支持按规则类型和关键字筛选`;
  }
  if (tableWrap) {
    tableWrap.classList.toggle("hidden", !mappingEntriesExpanded);
  }
  if (!filtered.length) {
    container.innerHTML = '<div class="mapping-item">当前没有单独维护的映射规则；已录入记录也可能是网页本身已提供完整类型和集团信息</div>';
    return;
  }
  container.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>规则类型</th>
          <th>来源名称</th>
          <th>目标值</th>
          <th>备注</th>
          <th>最近更新</th>
        </tr>
      </thead>
      <tbody>
        ${filtered
          .map((entry) => {
            const meta = entry.metadata || {};
            const matchField = String(meta.match_field || "transferor");
            const targetField = String(meta.target_field || (entry.group_name ? "group_name" : "source_type"));
            const title = `${matchField === "group" ? "集团" : "转让方"} -> ${targetField === "group_name" ? "集团" : "类型"}`;
            const targetValue = targetField === "group_name" ? entry.group_name : entry.source_type;
            const notes = String(meta.notes || "").trim();
            return `
              <tr>
                <td>${escapeHtml(title)}</td>
                <td>${escapeHtml(entry.company_name || "")}</td>
                <td>${escapeHtml(targetValue || "")}</td>
                <td>${escapeHtml(notes)}</td>
                <td>${escapeHtml(entry.updated_at || "")}</td>
              </tr>
            `;
          })
          .join("")}
      </tbody>
    </table>
  `;
}

function renderRecords(payload) {
  const container = $("recordsTable");
  const summary = $("recordsSummary");
  const pageIndicator = $("recordsPageIndicator");
  const prevPageButton = $("recordsPrevPageBtn");
  const nextPageButton = $("recordsNextPageBtn");
  const pageSizeInput = $("recordsPageSizeInput");
  if (!container || !summary || !pageIndicator || !prevPageButton || !nextPageButton || !pageSizeInput || !desktopState) {
    return;
  }
  desktopState.records.page = Number(payload.page || 1);
  desktopState.records.pageSize = Number(payload.page_size || desktopState.records.pageSize || 50);
  desktopState.records.pageCount = Number(payload.page_count || 0);
  desktopState.records.totalCount = Number(payload.total_count || 0);

  summary.textContent = formatRecordsSummary(payload);
  pageIndicator.textContent = `第 ${desktopState.records.page} / ${Math.max(desktopState.records.pageCount, 1)} 页`;
  prevPageButton.disabled = desktopState.records.page <= 1;
  nextPageButton.disabled = !payload.has_more;
  pageSizeInput.value = String(desktopState.records.pageSize);
  container.innerHTML = buildRecordsTableMarkup(payload, { escapeHtml });

  container.querySelectorAll("[data-open-archive]").forEach((node) => {
    node.addEventListener("click", async () => {
      if (node.dataset.openArchive) {
        await window.peapDesktop.openPath(node.dataset.openArchive);
      }
    });
  });
  container.querySelectorAll("[data-locate-record]").forEach((node) => {
    node.addEventListener("click", async () => {
      if (node.dataset.locateRecord) {
        await window.peapDesktop.showItemInFolder(node.dataset.locateRecord);
      }
    });
  });
}

function resetRecordsPagination() {
  if (!desktopState) {
    return;
  }
  desktopState.records.page = 1;
}

function renderBrowserRuntime(browser, installState = null) {
  const stateNode = $("browserRuntimeState");
  const metaNode = $("browserRuntimeMeta");
  if (!stateNode || !metaNode) {
    return;
  }
  if (!browser) {
    stateNode.textContent = "未检测";
    stateNode.className = "runtime-state";
    metaNode.textContent = "";
    return;
  }

  const hasError = Boolean(browser.error);
  const installed = Boolean(browser.installed);
  stateNode.textContent = installed
    ? "浏览器已就绪"
    : hasError
      ? "浏览器状态异常"
      : "浏览器未安装";
  stateNode.className = `runtime-state ${installed ? "ok" : hasError ? "error" : "warn"}`;

  const lines = [];
  if (browser.browser_cache_dir) {
    lines.push(`缓存目录：${browser.browser_cache_dir}`);
  }
  if (browser.executable_path) {
    lines.push(`可执行文件：${browser.executable_path}`);
  }
  if (browser.driver_executable) {
    lines.push(`Playwright Driver：${browser.driver_executable}`);
  }
  const installText = formatInstallState(installState);
  if (installText) {
    lines.push(`安装状态：${installText}`);
  }
  if (browser.error) {
    lines.push(`状态详情：${browser.error}`);
  }
  metaNode.textContent = lines.join("\n");
}

function renderProductReadiness(readiness, browser, installState) {
  const heroNode = $("heroRuntimeStatus");
  const gateNode = $("startupGate");
  const gateBody = $("startupGateBody");
  const gateMeta = $("startupGateMeta");
  if (!heroNode || !gateNode || !gateBody || !gateMeta) {
    return;
  }

  const ready = Boolean(readiness && readiness.download_ready);
  const issuesText = formatRuntimeIssues(readiness);
  const hasError = Boolean(issuesText || (browser && browser.error));
  const installStatus = String(installState?.status || "idle");
  const isInstalling = installStatus === "running";

  heroNode.textContent = isInstalling
    ? "正在准备浏览器运行环境"
    : ready
      ? "运行环境已就绪"
      : hasError
        ? "运行环境缺失或异常"
        : "运行环境未完成";
  heroNode.className = `hero-status ${isInstalling ? "warn" : ready ? "ok" : hasError ? "error" : "warn"}`;

  gateBody.textContent = isInstalling
    ? "正在后台安装浏览器。安装完成后，相关动作会自动恢复可用。"
    : ready
      ? "运行环境已就绪，可以直接开始任务。"
      : "当前浏览器运行环境未就绪。为避免任务中途失败，一键执行已临时禁用。";
  const metaLines = [];
  if (browser?.browser_cache_dir) {
    metaLines.push(`缓存目录：${browser.browser_cache_dir}`);
  }
  const installText = formatInstallState(installState);
  if (installText) {
    metaLines.push(`安装状态：${installText}`);
  }
  if (issuesText) {
    metaLines.push(`详情：${issuesText}`);
  }
  gateMeta.textContent = metaLines.join("\n");

  setDownloadActionAvailability(
    ready && !isInstalling,
    ready ? "" : "浏览器运行环境未就绪，请先在提示层或设置页完成安装",
  );

  ["installBrowserRuntimeBtn", "startupInstallBtn"].forEach((id) => {
    const node = $(id);
    if (!node) return;
    node.disabled = isInstalling;
  });

  const shouldShowGate = !ready && !startupGateDismissed;
  gateNode.classList.toggle("hidden", !shouldShowGate);
  document.body.classList.toggle("gate-open", shouldShowGate);
}

async function maybeAutoInstallBrowser() {
  if (
    startupAutoInstallAttempted ||
    !productReadiness ||
    productReadiness.download_ready ||
    (browserInstallState && browserInstallState.status === "running")
  ) {
    return;
  }
  startupAutoInstallAttempted = true;
  try {
    await startBrowserInstall("auto");
  } catch (error) {
    // status already rendered
  }
}

function applyRuntimeState(browser, readiness, installState = null) {
  runtimeBrowser = browser || null;
  productReadiness = readiness || null;
  browserInstallState = installState || browserInstallState || null;
  renderBrowserRuntime(runtimeBrowser, browserInstallState);
  renderProductReadiness(productReadiness, runtimeBrowser, browserInstallState);
  void maybeAutoInstallBrowser();
}

async function loadOverview() {
  const payload = await api("/api/overview");
  renderOverview(payload);
}

async function loadJobs() {
  const payload = await api("/api/jobs");
  const jobs = payload.jobs || [];
  if (!selectedJobId && jobs.length) {
    selectedJobId = jobs[0].job_id;
  }
  renderJobs(jobs);
}

async function loadJobEvents() {
  if (!selectedJobId) {
    renderEvents([]);
    return;
  }
  const payload = await api(`/api/jobs/${selectedJobId}/events`);
  renderEvents(payload.events || []);
}

async function loadMappings() {
  const payload = await api("/api/mappings");
  renderPendingMappings(payload);
  renderMappingDrafts();
  renderMappingEntries(payload.entries || []);
}

async function loadRecords() {
  if (!desktopState) {
    throw new Error("前端状态尚未初始化");
  }
  const state = $("recordsStateFilter")?.value || "all";
  const projectType = $("recordsProjectTypeFilter")?.value || "equity_transfer";
  const keyword = String($("recordsKeywordInput")?.value || "").trim();
  const dateFrom = String($("recordsDateFromInput")?.value || "").trim();
  const dateTo = String($("recordsDateToInput")?.value || "").trim();
  const query = buildRecordsQuery({
    state,
    projectType,
    keyword,
    dateFrom,
    dateTo,
    page: desktopState.records.page,
    pageSize: desktopState.records.pageSize,
  });
  const payload = await api(`/api/records?${query.toString()}`);
  renderRecords(payload);
}

async function loadSettings() {
  const basic = await api("/api/settings/basic");
  $("basicExchangeInput").value = basic.default_exchange || "all";
  $("basicProjectTypeInput").value = basic.default_project_type || "all";
  $("basicConcurrencyInput").value = String(basic.default_concurrency || 1);
  $("basicWorkspaceRootInput").value = basic.workspace_root || basic.app_home || "";
  $("basicArchiveRootInput").value = basic.archive_root || "";
  $("basicExportRootInput").value = basic.export_root || "";

  const advanced = await api("/api/settings/advanced");
  $("advancedAppHomeInput").value = advanced.app_home || "";
  $("advancedDbInput").value = advanced.streaming_db || "";
  $("advancedPostprocessConfigInput").value = advanced.postprocess_config || "";
  $("advancedLogDirInput").value = advanced.log_dir || "";
  $("advancedCacheDirInput").value = advanced.cache_dir || "";
  $("advancedRawAutoRootInput").value = advanced.raw_auto_root || "";
  $("advancedRawManualRootInput").value = advanced.raw_manual_root || "";
  $("advancedBrowserCacheDirInput").value = advanced.browser_cache_dir || "";
  $("advancedSaveJsonInput").checked = Boolean(advanced.save_json);

  if (!actionDefaultsInitialized) {
    $("exchangeInput").value = basic.default_exchange || "all";
    $("projectTypeInput").value = basic.default_project_type || "all";
    $("concurrencyInput").value = String(basic.default_concurrency || 1);
    actionDefaultsInitialized = true;
  }

  const runtime = await api("/api/runtime/dependencies");
  applyRuntimeState(
    runtime.browser || null,
    runtime.product_readiness || null,
    runtime.browser_install || null,
  );
}

async function loadInitial() {
  try {
    await Promise.all([loadOverview(), loadSettings(), loadJobs(), loadJobEvents()]);
  } catch (error) {
    setStatus("settingsResult", `加载失败：${error.message}`, true);
  }
}

function buildJobRequestPayload() {
  return {
    start_date: $("startDateInput").value,
    end_date: $("endDateInput").value,
    exchange: $("exchangeInput").value,
    project_type: $("projectTypeInput").value,
    concurrency: Number($("concurrencyInput").value || 1),
  };
}

async function launchOneClick() {
  setStatus("runResult", "");
  try {
    const payload = await api("/api/jobs/one-click", {
      method: "POST",
      body: JSON.stringify(buildJobRequestPayload()),
    });
    selectedJobId = payload.job_id || selectedJobId;
    setStatus("runResult", `已开始一键执行：${payload.job_id || ""}。任务完成后，如需表格请点击“导出 Excel”。`);
    await loadOverview();
    await loadJobs();
    await loadJobEvents();
    await switchPanel("overview");
  } catch (error) {
    setStatus("runResult", `启动失败：${error.message}`, true);
  }
}

async function forceStopCurrentJob() {
  const latestJob = overviewCache?.latest_job || null;
  if (!latestJob || String(latestJob.status || "") !== "running") {
    setStatus("runResult", "当前没有执行中的任务");
    return;
  }
  const confirmed = window.confirm("强制停止会直接重启后台；当前任务会被标记为已中断。是否继续？");
  if (!confirmed) {
    return;
  }
  backendRestartInProgress = true;
  setDownloadActionAvailability(false, "后台正在重启");
  setStatus("runResult", "正在强制停止当前任务并重启后台...");
  try {
    await window.peapDesktop.restartBackend();
    await waitForBackendAvailability();
    await loadInitial();
    await switchPanel("overview");
    setStatus("runResult", "当前任务已强制停止，后台已重启");
  } catch (error) {
    setStatus("runResult", `强制停止失败：${error.message}`, true);
  } finally {
    backendRestartInProgress = false;
    setDownloadActionAvailability(
      Boolean(productReadiness && productReadiness.download_ready) && String(browserInstallState?.status || "") !== "running",
      "浏览器运行环境未就绪，请先在提示层或设置页完成安装",
    );
  }
}

async function runExport() {
  try {
    const payload = await api("/api/exports", {
      method: "POST",
      body: JSON.stringify({
        date_from: $("exportDateFromInput").value,
        date_to: $("exportDateToInput").value,
      }),
    });
    selectedJobId = payload.job_id || selectedJobId;
    if (payload.status === "empty") {
      setStatus("exportResult", String(payload.message || "当前条件下没有可导出的记录"));
    } else {
      setStatus("exportResult", payload.message || `导出完成：${(payload.artifacts || []).join("，")}`);
    }
    await loadOverview();
    await loadJobs();
    await loadJobEvents();
  } catch (error) {
    setStatus("exportResult", `导出失败：${error.message}`, true);
  }
}

async function launchManualImport() {
  try {
    const defaultPath = $("advancedRawManualRootInput")?.value || overviewCache?.workspace_root || "";
    const inputDir = await window.peapDesktop.pickDirectory(defaultPath);
    if (!inputDir) {
      return;
    }
    const payload = await api("/api/jobs/manual-import", {
      method: "POST",
      body: JSON.stringify({ input_dir: inputDir }),
    });
    selectedJobId = payload.job_id || selectedJobId;
    setStatus("manualImportResult", `已开始手动导入解析：${payload.job_id || ""}，共发现 ${Number(payload.discovered_count || 0)} 个候选文件`);
    await loadOverview();
    await loadJobs();
    await loadJobEvents();
    await switchPanel("overview");
  } catch (error) {
    setStatus("manualImportResult", `启动失败：${error.message}`, true);
  }
}

function importPendingMappingsToDrafts() {
  if (!pendingMappingsCache.length) {
    setStatus("mappingResult", "当前没有待补映射可导入", true);
    return;
  }
  const existingKeys = new Set(mappingDraftState.map((item) => item.recordId));
  const additions = pendingMappingsCache
    .filter((item) => !existingKeys.has(item.record_id))
    .map((item) => {
      const rawRecord = item.payload || {};
      const ruleKind = buildDraftRuleKind(rawRecord);
      return {
        recordId: item.record_id,
        project_code: item.project_code || rawRecord["项目编号"] || "",
        project_name: rawRecord["项目名称"] || "",
        company_name: pendingRecordCompany(rawRecord),
        group_name: rawRecord["隶属集团"] || "",
        rawRecord,
        ruleKind,
        previousRuleKind: ruleKind,
        sourceName: buildDraftSourceValue(ruleKind, rawRecord),
        targetValue: "",
        notes: item.project_code || rawRecord["项目编号"] || "",
      };
    });
  mappingDraftState = [...mappingDraftState, ...additions];
  renderMappingDrafts();
  setStatus("mappingResult", `已导入 ${additions.length} 条待补项，请直接在列表里填写`);
}

async function saveDraftMappings() {
  const filledDrafts = mappingDraftState
    .map((draft) => ({
      ...draft,
      sourceName: String(draft.sourceName || "").trim(),
      targetValue: String(draft.targetValue || "").trim(),
      notes: String(draft.notes || "").trim(),
    }))
    .filter((draft) => draft.sourceName && draft.targetValue);
  if (!filledDrafts.length) {
    setStatus("mappingResult", "请先在导入列表中至少填写一条完整规则", true);
    return;
  }
  const saveBtn = $("saveDraftMappingsBtn");
  const originalLabel = saveBtn?.textContent || "保存已填写规则";
  if (saveBtn) {
    saveBtn.disabled = true;
    saveBtn.textContent = "正在保存...";
  }
  setStatus("mappingResult", `正在逐条检查并保存 ${filledDrafts.length} 条已填写规则...`);
  try {
    const result = await runBatchMappingUpsertFlow({
      drafts: filledDrafts,
      mappingRuleConfig: MAPPING_RULE_CONFIG,
      previewMapping: async (payload) => api("/api/mappings/preview", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
      saveMapping: async (payload) => api("/api/mappings", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
      confirmOverwrite: showMappingConflictDialog,
    });
    mappingDraftState = mappingDraftState.filter((draft) => !result.savedRecordIds.has(draft.recordId));
    renderMappingDrafts();
    if (result.refreshJobs.length) {
      selectedJobId = result.refreshJobs[result.refreshJobs.length - 1].job_id || selectedJobId;
    }
    const affectedCount = result.refreshJobs.reduce((sum, item) => sum + Number(item.affected_count || 0), 0);
    const parts = [`已保存 ${result.savedCount} 条规则`];
    if (result.refreshJobs.length) {
      parts.push(`启动 ${result.refreshJobs.length} 个映射回刷任务`);
    }
    if (affectedCount) {
      parts.push(`共影响 ${affectedCount} 条记录`);
    }
    if (result.skippedOverwriteCount) {
      parts.push(`跳过 ${result.skippedOverwriteCount} 条未确认覆盖规则`);
    }
    if (result.failedCount) {
      const failureHint = result.failureMessages[0] ? `首个失败：${result.failureMessages[0]}` : "";
      parts.push(`另有 ${result.failedCount} 条保存失败${failureHint ? `，${failureHint}` : ""}`);
    }
    setStatus("mappingResult", parts.join("，"), result.savedCount === 0 && result.failedCount > 0);
    await loadMappings();
    await loadOverview();
    await loadJobs();
    await loadJobEvents();
  } finally {
    if (saveBtn) {
      saveBtn.disabled = false;
      saveBtn.textContent = originalLabel;
    }
  }
}

async function saveMappingAndMaybeReprocess() {
  try {
    const sourceName = String($("mappingSourceInput").value || "").trim();
    const targetValue = String($("mappingTargetInput").value || "").trim();
    if (!sourceName || !targetValue) {
      setStatus("mappingResult", "请先填写完整的来源名称和目标值", true);
      return;
    }
    const result = await runMappingUpsertFlow({
      draft: {
        ruleKind: $("mappingRuleKindInput").value,
        sourceName,
        targetValue,
        notes: $("mappingNotesInput").value,
      },
      mappingRuleConfig: MAPPING_RULE_CONFIG,
      previewMapping: async (payload) => api("/api/mappings/preview", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
      saveMapping: async (payload) => api("/api/mappings", {
        method: "POST",
        body: JSON.stringify(payload),
      }),
      confirmOverwrite: showMappingConflictDialog,
    });
    if (result.cancelled) {
      setStatus("mappingResult", "已取消覆盖保存；原规则保持不变");
      return;
    }
    const payload = result.response || {};
    if (payload.job_id) {
      selectedJobId = payload.job_id;
    }
    const actionLabel = result.preview?.mode === "overwrite"
      ? "映射规则已覆盖"
      : result.preview?.mode === "update"
        ? "映射规则已更新"
        : "映射规则已保存";
    setStatus(
      "mappingResult",
      payload.job_id
        ? `${actionLabel}，已启动映射回刷任务：${payload.job_id}，影响 ${Number(payload.affected_count || 0)} 条记录`
        : `${actionLabel}，当前没有匹配到需要回刷的记录`,
    );
    await loadMappings();
    await loadOverview();
    await loadJobs();
    await loadJobEvents();
    if (currentPanel === "records") {
      await loadRecords();
    }
  } catch (error) {
    setStatus("mappingResult", `保存失败：${error.message}`, true);
  }
}

async function reprocessPendingMappings() {
  try {
    const payload = await api("/api/mappings/reprocess-pending", {
      method: "POST",
      body: JSON.stringify({}),
    });
    if (payload.job_id) {
      selectedJobId = payload.job_id || selectedJobId;
      setStatus("mappingResult", `已启动待补映射批量重处理：${payload.job_id}，共 ${Number(payload.affected_count || 0)} 条记录`);
    } else {
      setStatus("mappingResult", "当前没有待补映射需要重处理");
    }
    await loadMappings();
    await loadOverview();
    await loadJobs();
    await loadJobEvents();
  } catch (error) {
    setStatus("mappingResult", `批量重处理失败：${error.message}`, true);
  }
}

async function reprocessRecord(recordId) {
  try {
    const payload = await api(`/api/records/${recordId}/reprocess`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    setStatus("mappingResult", `已重处理：${statusLabel(payload.state)}`);
    await loadMappings();
    await loadOverview();
    if (currentPanel === "records") {
      await loadRecords();
      await loadJobs();
      await loadJobEvents();
    }
  } catch (error) {
    setStatus("mappingResult", `重处理失败：${error.message}`, true);
  }
}

async function saveSettings() {
  try {
    await api("/api/settings/basic", {
      method: "POST",
      body: JSON.stringify({
        default_exchange: $("basicExchangeInput").value,
        default_project_type: $("basicProjectTypeInput").value,
        default_concurrency: Number($("basicConcurrencyInput").value || 1),
      }),
    });
    await api("/api/settings/advanced", {
      method: "POST",
      body: JSON.stringify({
        postprocess_config: $("advancedPostprocessConfigInput").value,
        save_json: $("advancedSaveJsonInput").checked,
      }),
    });
    setStatus("settingsResult", "设置已保存");
    await loadSettings();
  } catch (error) {
    setStatus("settingsResult", `保存失败：${error.message}`, true);
  }
}

async function checkBrowserRuntime() {
  startupGateDismissed = false;
  setStatus("settingsResult", "正在检测浏览器运行环境...");
  try {
    const payload = await api("/api/runtime/dependencies");
    applyRuntimeState(
      payload.browser || null,
      payload.product_readiness || null,
      payload.browser_install || null,
    );
    const installed = Boolean(payload.browser && payload.browser.installed);
    setStatus("settingsResult", installed ? "浏览器已就绪" : "浏览器未安装");
  } catch (error) {
    setStatus("settingsResult", `检测失败：${error.message}`, true);
  }
}

async function startBrowserInstall(trigger = "manual") {
  try {
    const payload = await api("/api/runtime/install-browser", {
      method: "POST",
      body: JSON.stringify({
        browser_name: "chromium",
        trigger,
      }),
    });
    browserInstallState = payload || null;
    renderBrowserRuntime(runtimeBrowser, browserInstallState);
    renderProductReadiness(productReadiness, runtimeBrowser, browserInstallState);
    if (payload.status === "running") {
      setStatus("settingsResult", "已开始后台安装浏览器");
      return payload;
    }
    await checkBrowserRuntime();
    return payload;
  } catch (error) {
    setStatus("settingsResult", `安装失败：${error.message}`, true);
    throw error;
  }
}

async function installBrowserRuntime() {
  startupGateDismissed = false;
  setStatus("settingsResult", "正在安装浏览器，首次下载可能较慢...");
  try {
    await startBrowserInstall("manual");
  } catch (error) {
    // status already rendered
  }
}

async function locateDbFile() {
  if (overviewCache?.db_path) {
    await window.peapDesktop.showItemInFolder(overviewCache.db_path);
  }
}

async function openConfiguredPath(inputId, { locate = false } = {}) {
  const node = $(inputId);
  const targetPath = String(node?.value || "").trim();
  if (!targetPath) {
    return;
  }
  if (locate) {
    await window.peapDesktop.showItemInFolder(targetPath);
    return;
  }
  await window.peapDesktop.openPath(targetPath);
}

function syncMappingRuleLabels() {
  const ruleKind = $("mappingRuleKindInput").value;
  const config = MAPPING_RULE_CONFIG[ruleKind] || MAPPING_RULE_CONFIG.transferor_group;
  $("mappingSourceLabel").textContent = config.sourceLabel;
  $("mappingTargetLabel").textContent = config.targetLabel;
}

function bindUi() {
  document.querySelectorAll(".rail-button").forEach((node) => {
    node.addEventListener("click", () => {
      void switchPanel(node.dataset.panel);
    });
  });
  $("runOneClickBtn").addEventListener("click", launchOneClick);
  $("forceStopBtn").addEventListener("click", forceStopCurrentJob);
  $("runExportBtn").addEventListener("click", runExport);
  $("runManualImportBtn").addEventListener("click", launchManualImport);
  $("statPendingMappingCard").addEventListener("click", () => {
    void switchPanel("mappings");
  });
  $("saveMappingBtn").addEventListener("click", saveMappingAndMaybeReprocess);
  $("runPendingMappingRefreshBtn").addEventListener("click", () => {
    void reprocessPendingMappings();
  });
  $("saveDraftMappingsBtn").addEventListener("click", () => {
    void saveDraftMappings();
  });
  $("clearMappingBtn").addEventListener("click", () => {
    selectedPendingRecordId = "";
    selectedPendingCompanyName = "";
    $("mappingSourceInput").value = "";
    $("mappingTargetInput").value = "";
    $("mappingNotesInput").value = "";
    mappingDraftState = [];
    renderMappingDrafts();
  });
  $("mappingRuleKindInput").addEventListener("change", syncMappingRuleLabels);
  $("mappingSearchInput").addEventListener("input", () => {
    renderMappingEntries(mappingEntriesCache);
  });
  $("mappingRuleFilterInput").addEventListener("change", () => {
    renderMappingEntries(mappingEntriesCache);
  });
  $("toggleSavedMappingsBtn").addEventListener("click", () => {
    mappingEntriesExpanded = !mappingEntriesExpanded;
    $("toggleSavedMappingsBtn").textContent = mappingEntriesExpanded ? "收起规则表" : "展开规则表";
    renderMappingEntries(mappingEntriesCache);
  });
  $("mappingConflictCancelBtn").addEventListener("click", () => {
    closeMappingConflictDialog(false);
  });
  $("mappingConflictConfirmBtn").addEventListener("click", () => {
    closeMappingConflictDialog(true);
  });
  $("mappingConflictDialog").addEventListener("click", (event) => {
    if (event.target === $("mappingConflictDialog")) {
      closeMappingConflictDialog(false);
    }
  });
  $("importPendingMappingBtn").addEventListener("click", () => {
    importPendingMappingsToDrafts();
  });
  $("saveBasicSettingsBtn").addEventListener("click", saveSettings);
  $("saveAdvancedSettingsBtn").addEventListener("click", saveSettings);
  $("basicWorkspaceRootInput").addEventListener("click", async () => {
    await openConfiguredPath("basicWorkspaceRootInput");
  });
  $("basicArchiveRootInput").addEventListener("click", async () => {
    await openConfiguredPath("basicArchiveRootInput");
  });
  $("basicExportRootInput").addEventListener("click", async () => {
    await openConfiguredPath("basicExportRootInput");
  });
  $("checkBrowserRuntimeBtn").addEventListener("click", checkBrowserRuntime);
  $("installBrowserRuntimeBtn").addEventListener("click", installBrowserRuntime);
  $("startupCheckBtn").addEventListener("click", checkBrowserRuntime);
  $("startupInstallBtn").addEventListener("click", installBrowserRuntime);
  $("startupOpenSettingsBtn").addEventListener("click", async () => {
    startupGateDismissed = true;
    renderProductReadiness(productReadiness, runtimeBrowser, browserInstallState);
    await switchPanel("settings");
  });
  $("startupDismissBtn").addEventListener("click", () => {
    startupGateDismissed = true;
    renderProductReadiness(productReadiness, runtimeBrowser, browserInstallState);
  });
  $("refreshBtn").addEventListener("click", async () => {
    await loadOverview();
    await refreshCurrentPanel();
  });
  $("refreshRecordsBtn").addEventListener("click", loadRecords);
  $("recordsStateFilter").addEventListener("change", () => {
    resetRecordsPagination();
    void loadRecords();
  });
  $("recordsProjectTypeFilter").addEventListener("change", () => {
    resetRecordsPagination();
    void loadRecords();
  });
  $("recordsDateFromInput").addEventListener("change", () => {
    resetRecordsPagination();
    void loadRecords();
  });
  $("recordsDateToInput").addEventListener("change", () => {
    resetRecordsPagination();
    void loadRecords();
  });
  $("recordsKeywordInput").addEventListener("change", () => {
    resetRecordsPagination();
    void loadRecords();
  });
  $("recordsKeywordInput").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      resetRecordsPagination();
      void loadRecords();
    }
  });
  $("recordsPrevPageBtn").addEventListener("click", () => {
    if (!desktopState || desktopState.records.page <= 1) {
      return;
    }
    desktopState.records.page -= 1;
    void loadRecords();
  });
  $("recordsNextPageBtn").addEventListener("click", () => {
    if (!desktopState) {
      return;
    }
    desktopState.records.page += 1;
    void loadRecords();
  });
  $("recordsPageSizeInput").addEventListener("change", () => {
    if (!desktopState) {
      return;
    }
    desktopState.records.pageSize = Number($("recordsPageSizeInput")?.value || desktopState.records.pageSize || 50);
    resetRecordsPagination();
    void loadRecords();
  });
  $("openArchiveBtn").addEventListener("click", async () => {
    if (overviewCache?.archive_root) {
      await window.peapDesktop.openPath(overviewCache.archive_root);
    }
  });
  $("openWorkspaceBtn").addEventListener("click", async () => {
    if (overviewCache?.app_home) {
      await window.peapDesktop.openPath(overviewCache.app_home);
    }
  });
  $("openExportBtn").addEventListener("click", async () => {
    if (overviewCache?.export_root) {
      await window.peapDesktop.openPath(overviewCache.export_root);
    }
  });
  $("openDbBtn").addEventListener("click", locateDbFile);
  $("locateDbBtn").addEventListener("click", locateDbFile);
  $("advancedAppHomeInput").addEventListener("click", async () => {
    await openConfiguredPath("advancedAppHomeInput");
  });
  $("advancedDbInput").addEventListener("click", async () => {
    await openConfiguredPath("advancedDbInput", { locate: true });
  });
  $("advancedLogDirInput").addEventListener("click", async () => {
    await openConfiguredPath("advancedLogDirInput");
  });
  $("advancedCacheDirInput").addEventListener("click", async () => {
    await openConfiguredPath("advancedCacheDirInput");
  });
  $("advancedRawAutoRootInput").addEventListener("click", async () => {
    await openConfiguredPath("advancedRawAutoRootInput");
  });
  $("advancedRawManualRootInput").addEventListener("click", async () => {
    await openConfiguredPath("advancedRawManualRootInput");
  });
  $("advancedBrowserCacheDirInput").addEventListener("click", async () => {
    await openConfiguredPath("advancedBrowserCacheDirInput");
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && mappingConflictPromptResolver) {
      closeMappingConflictDialog(false);
    }
  });
}

async function bootstrap() {
  const [
    apiModule,
    recordsModule,
    stateModule,
    mappingsModule,
    pollingModule,
  ] = await Promise.all([
    import("./renderer/api.mjs"),
    import("./renderer/records.mjs"),
    import("./renderer/state.mjs"),
    import("./renderer/mappings.mjs"),
    import("./renderer/polling.mjs"),
  ]);
  desktopState = stateModule.createDesktopState();
  buildRecordsQuery = recordsModule.buildRecordsQuery;
  formatRecordsSummary = recordsModule.formatRecordsSummary;
  buildRecordsTableMarkup = recordsModule.buildRecordsTableMarkup;
  isMappingInteractionActive = mappingsModule.isMappingInteractionActive;
  runMappingUpsertFlow = mappingsModule.runMappingUpsertFlow;
  runBatchMappingUpsertFlow = mappingsModule.runBatchMappingUpsertFlow;
  formatMappingConflictSummary = mappingsModule.formatMappingConflictSummary;
  waitForDesktopBackendAvailability = apiModule.waitForDesktopBackendAvailability;
  createPollLoop = pollingModule.createPollLoop;
  startPolling = pollingModule.startPolling;

  const backendConfig = await window.peapDesktop.getBackendConfig();
  desktopState.backendUrl = String(backendConfig?.backendUrl || "");
  desktopState.backendApiToken = String(backendConfig?.apiToken || "");
  apiClient = apiModule.createApiClient({
    baseUrl: desktopState.backendUrl,
    apiToken: desktopState.backendApiToken,
  });
  defaultDates();
  bindUi();
  syncMappingRuleLabels();
  $("recordsPageSizeInput").value = String(desktopState.records.pageSize);
  setDownloadActionAvailability(false, "正在检查浏览器运行环境");
  await waitForBackendAvailability();
  await loadInitial();
  const pollLoop = createPollLoop({
    isPaused: () => backendRestartInProgress,
    loadOverview,
    loadJobs,
    loadJobEvents,
    getCurrentPanel: () => currentPanel,
    loadRecords,
    loadMappings,
    isMappingEditorActive,
    onError: (error) => {
      console.error(error);
    },
  });
  startPolling({ pollLoop, intervalMs: 1500 });
}

bootstrap().catch((error) => {
  console.error(error);
  setStatus("settingsResult", `启动失败：${error.message}`, true);
});
