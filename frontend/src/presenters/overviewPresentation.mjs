import {
  buildJobMetaText,
  buildProgressHintText,
  buildRecentJobBadge,
} from "./jobPresentation.mjs";

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function asText(value) {
  return String(value || "").trim();
}

export function buildOverviewViewModel(overview = {}) {
  const source = asObject(overview);
  const recordSummary = asObject(source.record_summary);
  const stateCounts = asObject(recordSummary.state_counts);
  const runtime = asObject(source.runtime);
  const browser = asObject(runtime.browser);
  const install = asObject(runtime.install);
  const readiness = asObject(runtime.readiness);
  const browserReady = Boolean(browser.installed);
  const isInstalling = asText(install.status) === "running";
  const headline = isInstalling
    ? "正在准备浏览器运行环境"
    : browserReady
      ? "运行环境已就绪"
      : "运行环境缺失或异常";
  const browserState = Object.keys(browser).length === 0
    ? "浏览器未检测"
    : isInstalling
      ? "浏览器正在安装"
      : browserReady
        ? "浏览器已就绪"
        : asText(browser.error)
          ? "浏览器状态异常"
          : "浏览器未安装";
  const runtimeIssues = [];

  if (asText(install.message)) runtimeIssues.push(asText(install.message));
  if (asText(browser.error)) runtimeIssues.push(asText(browser.error));
  asArray(readiness.issues).forEach((issue) => {
    const message = asText(asObject(issue).message);
    if (message) runtimeIssues.push(message);
  });

  return {
    stateCounts,
    pendingMappingCount: Number.parseInt(recordSummary.pending_mapping_count, 10) || Number.parseInt(stateCounts.pending_mapping, 10) || 0,
    pendingReviewCount:
      (Number.parseInt(recordSummary.pending_review_count, 10) || Number.parseInt(stateCounts.pending_review, 10) || 0)
      + (Number.parseInt(recordSummary.field_missing_count, 10) || Number.parseInt(stateCounts.field_missing, 10) || 0),
    browser,
    install,
    readiness,
    headline,
    browserState,
    runtimeIssues,
  };
}

export function buildOverviewPrimaryStats(overviewView = {}) {
  const source = asObject(overviewView);
  const stateCounts = asObject(source.stateCounts);
  return [
    {
      key: "ready",
      label: "已录入",
      value: Number.parseInt(stateCounts.ready, 10) || 0,
      tone: "default",
      sublabel: "ready 状态",
    },
    {
      key: "pending_mapping",
      label: "待补映射",
      value: Number.parseInt(source.pendingMappingCount, 10) || Number.parseInt(stateCounts.pending_mapping, 10) || 0,
      tone: "warning",
      sublabel: "需要补充规则",
    },
    {
      key: "pending_review",
      label: "待人工复核",
      value: Number.parseInt(source.pendingReviewCount, 10) || Number.parseInt(stateCounts.pending_review, 10) || 0,
      tone: "warning",
      sublabel: "需要人工复核",
    },
  ];
}

export function buildFamilyStatsPrimaryStats(stateCounts = {}) {
  const source = asObject(stateCounts);
  return [
    {
      key: "ready",
      label: "已录入",
      value: Number.parseInt(source.ready, 10) || 0,
      tone: "default",
      sublabel: "ready 状态",
    },
    {
      key: "pending_mapping",
      label: "待补映射",
      value: Number.parseInt(source.pending_mapping, 10) || 0,
      tone: "warning",
      sublabel: "需要补充规则",
    },
    {
      key: "pending_review",
      label: "待人工复核",
      value: Number.parseInt(source.pending_review, 10) || 0,
      tone: "warning",
      sublabel: "需要人工复核",
    },
  ];
}

export function buildOverviewProgressFallback(job = {}, progress = {}, { num } = {}) {
  const status = asText(asObject(job).status);
  if (["success", "success_with_warnings", "failed", "interrupted"].includes(status)) {
    return buildJobMetaText(job, { num }) || buildProgressHintText(progress, { num }) || "暂无事件进度";
  }
  return buildProgressHintText(progress, { num }) || buildJobMetaText(job, { num }) || "暂无事件进度";
}

export function buildOverviewRecentJobView(job = {}, { num } = {}) {
  return {
    badge: buildRecentJobBadge(job, { num }),
    metaText: buildJobMetaText(job, { num }),
  };
}
