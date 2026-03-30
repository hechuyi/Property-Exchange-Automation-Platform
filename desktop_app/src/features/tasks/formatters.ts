const JOB_TYPE_LABELS: Record<string, string> = {
  one_click: "一键执行",
  download_ingest: "历史区间任务",
  export_excel: "导出 Excel",
  manual_import: "手动导入解析",
  mapping_refresh: "映射回刷",
};

const JOB_STATUS_LABELS: Record<string, string> = {
  running: "执行中",
  success: "已完成",
  success_with_warnings: "已完成，但有待处理项",
  interrupted: "已中断",
  failed: "执行失败",
};

const STAGE_LABELS: Record<string, string> = {
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

const TERMINAL_JOB_STATUSES = new Set(["success", "success_with_warnings", "interrupted", "failed"]);
const TERMINAL_PHASE_CODES = new Set(["completed", "completed_with_warnings", "interrupted", "failed"]);

function countValue(value: unknown) {
  const numeric = Number(value || 0);
  return Number.isFinite(numeric) ? numeric : 0;
}

function jobTypeLabel(jobType: unknown) {
  return JOB_TYPE_LABELS[String(jobType || "").trim()] || String(jobType || "").trim() || "任务";
}

function jobStatusLabel(status: unknown) {
  return JOB_STATUS_LABELS[String(status || "").trim()] || String(status || "").trim() || "未知";
}

function stageLabel(stage: unknown) {
  return STAGE_LABELS[String(stage || "").trim()] || String(stage || "").trim() || "任务更新";
}

function joinParts(parts: Array<string>, fallback = "") {
  return parts.filter(Boolean).join(" · ") || fallback;
}

function isTerminalProgress(progressView: Record<string, unknown> = {}) {
  const jobStatus = String(progressView.job_status || "").trim();
  const phaseCode = String(progressView.phase_code || "").trim();
  return Boolean(progressView.is_terminal || TERMINAL_JOB_STATUSES.has(jobStatus) || TERMINAL_PHASE_CODES.has(phaseCode));
}

function terminalProgressMeta(progressView: Record<string, unknown> = {}, latestJob: Record<string, unknown> | null = null, overview: Record<string, unknown> = {}) {
  const jobType = String(latestJob?.job_type || progressView.job_type || "").trim();
  const statusLabel = jobStatusLabel(String(latestJob?.status || progressView.job_status || "").trim());
  const downloaded = countValue(progressView.downloaded_count);
  const persisted = countValue(progressView.persisted_count);
  const skipped = countValue(progressView.skipped_count);
  const pending = countValue(progressView.pending_mapping_count);
  const exceptions = countValue(progressView.exception_count);
  const archiveCompleted = countValue(progressView.archive_completed_count);
  const overallCounts = (overview.record_state_counts as Record<string, unknown>) || {};
  const overallPending = countValue((overview.pending_mapping_count as unknown) || overallCounts.pending_mapping);
  const overallSkipped = countValue(overallCounts.skipped);

  if (jobType === "export_excel") {
    return exportMeta(progressView, latestJob);
  }
  if (jobType === "manual_import") {
    return joinParts([
      `${statusLabel} · 手动导入`,
      `已处理文件 ${downloaded} 个`,
      `已写回 ${persisted} 条`,
      pending > 0 ? `待补映射 ${pending} 条` : "",
      exceptions > 0 ? `异常 ${exceptions} 条` : "",
    ]);
  }
  if (jobType === "mapping_refresh") {
    return joinParts([
      `${statusLabel} · 映射回刷`,
      `已回刷 ${downloaded} 条`,
      `已写回 ${persisted} 条`,
      pending > 0 ? `待补映射 ${pending} 条` : "",
      exceptions > 0 ? `异常 ${exceptions} 条` : "",
    ]);
  }
  return joinParts([
    `${statusLabel} · 已保存网页 ${downloaded} 条`,
    `已存档 ${archiveCompleted} 条`,
    `已跳过 ${skipped} 条`,
    `待补映射 ${pending} 条`,
    `异常 ${exceptions} 条`,
    overallPending > 0 ? `当前待补映射 ${overallPending} 条` : "",
    overallSkipped > 0 ? `累计已跳过 ${overallSkipped} 条` : "",
  ]);
}

function terminalProgressHint(progressView: Record<string, unknown> = {}, latestJob: Record<string, unknown> | null = null) {
  const jobType = String(latestJob?.job_type || progressView.job_type || "").trim();
  const status = String(latestJob?.status || progressView.job_status || "").trim();
  if (jobType === "export_excel") {
    if (status === "failed") return "导出失败，请在工作台查看任务活动。";
    if (status === "interrupted") return "导出已中断，请在工作台查看任务活动。";
    if (status === "success_with_warnings") return "导出已结束，但当前条件下没有形成新的导出文件。";
    return "导出已完成，可以从导出目录直接打开文件。";
  }
  if (jobType === "manual_import") {
    if (status === "failed") return "手动导入失败，请在工作台查看任务活动。";
    if (status === "interrupted") return "手动导入已中断，请在工作台查看任务活动。";
    return "手动导入已完成，可在工作台查看任务活动。";
  }
  if (jobType === "mapping_refresh") {
    if (status === "failed") return "映射回刷失败，请在工作台查看任务活动。";
    if (status === "interrupted") return "映射回刷已中断，请在工作台查看任务活动。";
    return "映射回刷已完成，可在工作台查看任务活动。";
  }
  if (status === "failed") return "任务执行失败，请在工作台查看任务活动。";
  if (status === "interrupted") return "任务已中断，请在工作台查看任务活动。";
  if (status === "success_with_warnings") return "任务已完成，但仍有待补映射或失败项；请先处理后再导出 Excel。";
  const downloaded = countValue(progressView.downloaded_count);
  const persisted = countValue(progressView.persisted_count);
  return downloaded <= 0 && persisted <= 0
    ? "最近一次任务已完成，但当前范围没有形成新的可录入结果。"
    : "最近一次任务已完成；如需表格，请点击导出 Excel。";
}

function exportMeta(_progressView: Record<string, unknown> = {}, latestJob: Record<string, unknown> | null = null) {
  const exportSummary = latestJob && typeof latestJob.summary === "object" ? (latestJob.summary as Record<string, unknown>) : {};
  return joinParts([
    `新增 ${countValue(exportSummary.new_records)} 条`,
    `变更 ${countValue(exportSummary.changed_records)} 条`,
    `生成文件 ${(Array.isArray(exportSummary.artifacts) ? exportSummary.artifacts.length : 0)} 个`,
  ]);
}

function genericDownloadMeta(progressView: Record<string, unknown> = {}) {
  const phaseCode = String(progressView.phase_code || "").trim();
  const downloaded = countValue(progressView.downloaded_count);
  const persisted = countValue(progressView.persisted_count);
  const skipped = countValue(progressView.skipped_count);
  const pending = countValue(progressView.pending_mapping_count);
  const exceptions = countValue(progressView.exception_count);
  const taskIndex = countValue(progressView.current_index);
  const taskTotal = countValue(progressView.current_total);
  const currentTaskLabel = String(progressView.current_item_label || "").trim();

  if (phaseCode === "prepare_tasks") {
    return joinParts(
      taskTotal > 0
        ? [
            `扫描进度 ${Math.min(taskIndex, taskTotal)}/${taskTotal}`,
            currentTaskLabel ? `当前对象 ${currentTaskLabel}` : "",
          ]
        : [currentTaskLabel ? `当前对象 ${currentTaskLabel}` : "正在整理下载范围"],
    );
  }
  if (phaseCode === "save_pages") {
    return joinParts([
      taskTotal > 0 ? `任务进度 ${Math.min(taskIndex, taskTotal)}/${taskTotal}` : "",
      currentTaskLabel ? `当前下载 ${currentTaskLabel}` : "",
      `已保存网页 ${downloaded} 条`,
      pending > 0 ? `待补映射 ${pending} 条` : "",
      exceptions > 0 ? `异常 ${exceptions} 条` : "",
    ]);
  }
  if (phaseCode === "completed" || phaseCode === "completed_with_warnings") {
    if (downloaded <= 0 && persisted <= 0 && exceptions <= 0) {
      return "本次没有新增网页写入数据库";
    }
  }
  return joinParts([
    `已保存网页 ${downloaded} 条`,
    `已跳过 ${skipped} 条`,
    `待补映射 ${pending} 条`,
    `异常 ${exceptions} 条`,
  ]);
}

function genericHint(progressView: Record<string, unknown> = {}, latestJob: Record<string, unknown> | null = null) {
  const phaseCode = String(progressView.phase_code || "").trim();
  const metadata = latestJob && typeof latestJob.metadata === "object" ? (latestJob.metadata as Record<string, unknown>) : {};
  const start = String(metadata.start_date || "").trim();
  const end = String(metadata.end_date || "").trim();
  const dateText = start && end ? (start === end ? start : `${start} 至 ${end}`) : start || end;
  let hint = "暂无任务时，可以直接选择日期范围后执行一键任务。";
  if (phaseCode === "prepare_tasks") {
    hint = String(progressView.current_item_label || "").trim()
      ? `系统正在扫描 ${String(progressView.current_item_label || "").trim()} 的可下载页面。`
      : "系统正在扫描当前筛选范围内的可下载页面。";
  } else if (phaseCode === "save_pages") {
    hint = String(progressView.current_item_label || "").trim()
      ? `系统正在下载并保存 ${String(progressView.current_item_label || "").trim()} 的页面。`
      : "系统正在保存已扫描到的页面。";
  } else if (phaseCode === "completed") {
    hint = countValue(progressView.downloaded_count) <= 0 && countValue(progressView.persisted_count) <= 0
      ? "最近一次任务已完成，但当前范围没有形成新的可录入结果。"
      : "最近一次任务已完成；如需表格，请点击导出 Excel。";
  } else if (phaseCode === "completed_with_warnings") {
    hint = "任务已完成，但仍有待补映射或失败项；请先处理后再导出 Excel。";
  } else if (phaseCode === "failed") {
    hint = "任务失败时，可在工作台的任务活动中查看详细原因。";
  }
  return dateText ? `${hint} 当前日期范围：${dateText}。` : hint;
}

export function progressPreset(progressView: Record<string, unknown> = {}) {
  if (isTerminalProgress(progressView)) {
    return { width: 100, active: false };
  }
  const explicitPercent = Number(progressView.phase_percent || 0);
  if (explicitPercent > 0) {
    return {
      width: Math.max(4, Math.min(100, explicitPercent)),
      active: true,
    };
  }
  const phaseCode = String(progressView.phase_code || "").trim();
  if (phaseCode === "prepare_tasks") return { width: 24, active: true };
  if (phaseCode === "save_pages") return { width: 56, active: true };
  if (phaseCode === "archive_pending") return { width: 72, active: true };
  if (phaseCode === "exporting") return { width: 92, active: true };
  return { width: 10, active: false };
}

export function formatProgressMeta(
  progressView: Record<string, unknown> = {},
  latestJob: Record<string, unknown> | null = null,
  overview: Record<string, unknown> = {},
) {
  const jobType = String(latestJob?.job_type || progressView.job_type || "").trim();
  if (isTerminalProgress(progressView)) return terminalProgressMeta(progressView, latestJob, overview);
  if (jobType === "export_excel") return exportMeta(progressView, latestJob);
  return genericDownloadMeta(progressView);
}

export function formatProgressHint(
  progressView: Record<string, unknown> = {},
  latestJob: Record<string, unknown> | null = null,
  overview: Record<string, unknown> = {},
) {
  const jobType = String(latestJob?.job_type || progressView.job_type || "").trim();
  if (isTerminalProgress(progressView)) return terminalProgressHint(progressView, latestJob);
  if (jobType === "export_excel") return terminalProgressHint(progressView, latestJob, overview);
  return genericHint(progressView, latestJob);
}

export function formatJobTitle(job: Record<string, unknown> = {}) {
  return `${jobTypeLabel(job.job_type)} · ${jobStatusLabel(job.status)}`;
}

export function formatJobMeta(job: Record<string, unknown> = {}) {
  const summary = job && typeof job.summary === "object" && job.summary ? (job.summary as Record<string, unknown>) : {};
  const skippedCount = countValue(summary.skipped_count);
  const pendingCount = countValue(summary.pending_mapping_count);
  const downloadedCount = countValue(job.downloaded_count);
  const persistedCount = countValue(job.persisted_count);
  const exceptionCount = countValue(job.exception_count);
  if (String(job.job_type || "").trim() === "manual_import") {
    return joinParts([
      `已处理文件 ${downloadedCount} · 已写入 ${persistedCount} · 异常 ${exceptionCount}`,
      skippedCount > 0 ? `已跳过 ${skippedCount}` : "",
      pendingCount > 0 ? `待补映射 ${pendingCount}` : "",
    ]);
  }
  if (String(job.job_type || "").trim() === "mapping_refresh") {
    return joinParts([
      `已回刷 ${downloadedCount} 条 · 已写回 ${persistedCount} 条 · 异常 ${exceptionCount}`,
      skippedCount > 0 ? `已跳过 ${skippedCount}` : "",
      pendingCount > 0 ? `待补映射 ${pendingCount}` : "",
    ]);
  }
  if (String(job.job_type || "").trim() === "export_excel") {
    const artifactCount = Array.isArray(summary.artifacts) ? summary.artifacts.length : 0;
    return `新增 ${countValue(summary.new_records)} · 变更 ${countValue(summary.changed_records)} · 文件 ${artifactCount}`;
  }
  return joinParts([
    `已保存网页 ${downloadedCount} · 已写入 ${persistedCount} · 异常 ${exceptionCount}`,
    skippedCount > 0 ? `已跳过 ${skippedCount}` : "",
    pendingCount > 0 ? `待补映射 ${pendingCount}` : "",
  ]);
}

export function formatCapacityNotice({ returnedCount = 0, totalCount = 0, noun = "" }: { returnedCount?: number; totalCount?: number; noun?: string } = {}) {
  const visibleCount = countValue(returnedCount);
  const overallCount = countValue(totalCount);
  if (visibleCount <= 0 || overallCount <= visibleCount) return "";
  const remainingCount = overallCount - visibleCount;
  const nounText = String(noun || "").trim();
  return `只显示前 ${visibleCount} 条${nounText}，仍有剩余 ${remainingCount} 条`;
}

export function formatEventTitle(event: Record<string, unknown> = {}) {
  const code = String(event.project_code || "").trim();
  const status = String(event.status || "").trim();
  if (status === "skipped") {
    return code ? `已跳过 · ${code}` : "已跳过";
  }
  if (TERMINAL_JOB_STATUSES.has(status)) {
    return code ? `${jobStatusLabel(status)} · ${code}` : jobStatusLabel(status);
  }
  const stage = String(event.stage || "").trim();
  const stageText = stageLabel(stage);
  return code ? `${stageText} · ${code}` : stageText;
}

const EVENT_ERROR_LABELS: Record<string, string> = {
  mapping_refresh_failed: "映射回刷失败，请在工作台查看任务活动。",
  manual_import_failed: "手动导入失败，请在工作台查看任务活动。",
  export_failed: "导出失败，请在工作台查看任务活动。",
};

export function formatEventDetail(event: Record<string, unknown> = {}) {
  const payload = event.payload && typeof event.payload === "object" ? (event.payload as Record<string, unknown>) : {};
  const payloadLabel = String(payload.label || "").trim();
  if (payloadLabel) return payloadLabel;
  const errorType = String(event.error_type || "").trim();
  if (EVENT_ERROR_LABELS[errorType]) return EVENT_ERROR_LABELS[errorType];
  const status = String(event.status || "").trim();
  if (status === "failed") return "任务执行失败，请在工作台查看任务活动。";
  if (status === "interrupted") return "任务已中断，请在工作台查看任务活动。";
  if (status) return jobStatusLabel(status);
  return "";
}
