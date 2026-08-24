import { API } from "./api.js";
import {
  MAPPING_RULES,
  businessTypeLabel,
  exchangeDisplayLabel,
  recordFamilyLabel,
  MAPPING_SOURCE_TYPES,
} from "./src/constants/index.js";
import {
  $, $$, num, text, escapeHtml, display,
  formatTimeAgo, formatJobTime,
} from "./src/utils/index.js";
import {
  createInitialMappingDraft,
  formatActionErrorMessage,
  jobTypeLabel, jobStatusLabel,
  isActive, isTerminal, stateDotClass,
} from "./src/state/index.js";
import { runActiveJobGuardedAction } from "./src/state/activeJobActionGuard.js";
import { createActionModals } from "./src/actions/modals.js";
import { createMappingsPanel } from "./src/panels/mappings.js";
import { createTasksPanel } from "./src/panels/tasks.js";
import { createRecordsPanel } from "./src/panels/records.js";
import { createReviewsPanel } from "./src/panels/reviews.js";
import { createExportHistoryPanel } from "./src/panels/exportHistory.js";
import {
  buildAdvancedSettingsSavePayload,
  buildBasicSettingsSavePayload,
  buildSettingsViewModel,
} from "./src/panels/settingsState.js";
import {
  buildActionableScopeFromRuntime,
  describeDefaultScopeRuntime,
  describeRecordsBrowseRuntime,
  buildRecordsScopeFromBrowseRuntime,
  resolveRecordsBrowseRuntime,
  resolveDefaultScopeRuntime,
} from "./src/state/defaultScopeRuntime.js";
import { resolveSettingsDefaultScopeEditor } from "./src/state/settingsDefaultScopeEditor.js";
import {
  pollDelayForPanel as pollDelayForPanelState,
  shouldAutoPollPanel as shouldPanelAutoPoll,
} from "./src/state/pollingPolicy.js";
import {
  buildJobEventErrorText,
  buildJobEventLogText,
  eventStageLabel,
  hasJobEventActivity,
  isJobEventFailure,
} from "./src/presenters/jobEventsPresentation.mjs";
import { resolveBrowserBackendConfig } from "./src/backendConfig.js";
import { normalizeOverviewResource } from "./src/contracts/overview.js";
import { normalizeJobEventList } from "./src/contracts/jobEvents.js";
import {
  buildFamilyStatsPrimaryStats,
  buildOverviewPrimaryStats,
  buildOverviewProgressFallback,
  buildOverviewRecentJobView,
  buildOverviewViewModel,
} from "./src/presenters/overviewPresentation.mjs";
import { normalizeCatalogResource, resolveActionableDefaultScope } from "./src/contracts/catalog.js";

/* ── App ── */
(function () {
  "use strict";

  // Helpers, constants, and state utilities are now imported from
  // src/constants, src/utils, and src/state modules above.

  function getMappingRule(ruleKind) {
    return MAPPING_RULES[String(ruleKind || "").trim()] || MAPPING_RULES.transferor_group;
  }

  function deriveRuleKind(matchField, targetField) {
    return Object.entries(MAPPING_RULES).find(([, spec]) => spec.matchField === matchField && spec.targetField === targetField)?.[0] || "transferor_group";
  }

  function buildMappingDraftPayload() {
    const payload = {
      rule_kind: mappingDraft.rule_kind,
      source_name: text(mappingDraft.source_name).trim(),
      target_value: text(mappingDraft.target_value).trim(),
      notes: text(mappingDraft.notes).trim(),
      confirm_overwrite: Boolean(mappingDraft.confirm_overwrite),
    };
    const entryId = text(mappingDraft.entry_id).trim();
    if (entryId) payload.entry_id = entryId;
    return payload;
  }

  function fillMappingDraft(ruleLike = {}, notes = "") {
    const ruleKind = text(ruleLike.rule_kind).trim() || deriveRuleKind(text(ruleLike.match_field).trim(), text(ruleLike.target_field).trim());
    mappingDraft = {
      entry_id: text(ruleLike.entry_id).trim(),
      rule_kind: ruleKind,
      source_name: text(ruleLike.source_name).trim(),
      target_value: text(ruleLike.target_value).trim(),
      notes: text(notes || ruleLike.notes).trim(),
      confirm_overwrite: false,
    };
    mappingPreview = null;
  }

  function buildConflictResolutionNotes(item, resolution) {
    const projectCode = display(item?.project_code);
    const resolutionLabel = display(resolution?.label || resolution?.title || resolution?.target_value);
    return text(`人工裁决 ${projectCode}：${resolutionLabel}`);
  }

  function recordCellValue(row, column) {
    const values = row.display_values || {};
    if (column === "交易所") {
      return exchangeDisplayLabel(row.exchange_code || values[column], values[column] || row.exchange_label);
    }
    return values[column];
  }

  function buildMappingTargetControl(rule, currentValue) {
    const normalizedValue = text(currentValue).trim();
    if (rule.targetField !== "source_type") {
      return `<input type="text" id="mapping-target-value" value="${escapeHtml(normalizedValue)}" placeholder="请输入${escapeHtml(rule.targetLabel)}">`;
    }
    const options = [...MAPPING_SOURCE_TYPES];
    if (normalizedValue && !options.includes(normalizedValue)) options.unshift(normalizedValue);
    return `<select id="mapping-target-value"><option value="" ${normalizedValue ? "" : "selected"}>请选择类型</option>${options.map((value) => {
      const isLegacy = !MAPPING_SOURCE_TYPES.includes(value);
      const label = isLegacy ? `${value}（历史值）` : value;
      return `<option value="${escapeHtml(value)}" ${normalizedValue === value ? "selected" : ""}>${escapeHtml(label)}</option>`;
    }).join("")}</select>`;
  }

  function buildPathFieldControl({
    inputId,
    value,
    readOnly = false,
    placeholder = "",
    browseButtonId = "",
    browseButtonLabel = "选择",
    openButtonId = "",
    openButtonLabel = "打开目录",
    openReveal = false,
  }) {
    return `<div class="path-control">
      <input type="text" id="${inputId}" value="${escapeHtml(text(value))}" ${readOnly ? "readonly" : ""} ${placeholder ? `placeholder="${escapeHtml(placeholder)}"` : ""}>
      <div class="path-actions">
        ${browseButtonId ? `<button class="btn btn-sm" type="button" id="${browseButtonId}">${browseButtonLabel}</button>` : ""}
        ${openButtonId ? `<button class="btn btn-sm btn-open-path" type="button" id="${openButtonId}" data-input-id="${inputId}" data-open-reveal="${openReveal ? "true" : "false"}">${openButtonLabel}</button>` : ""}
      </div>
    </div>`;
  }

  async function chooseLocalPath(payload = {}) {
    const result = await API.chooseLocalPath(payload);
    return result && result.selected ? text(result.path).trim() : "";
  }

  async function openLocalPath(path, { reveal = false } = {}) {
    const normalizedPath = text(path).trim();
    if (!normalizedPath) return;
    await API.openLocalPath({ path: normalizedPath, reveal });
  }

  function buildExportBusinessError(result = {}) {
    const status = text(result?.status).trim().toLowerCase();
    const fieldMissingBlockedRecords = num(result?.field_missing_blocked_records);
    const failureMessage = text(result?.failure_message || result?.message).trim();
    const failureCode = text(result?.failure_code).trim();
    const emptyReasonCode = text(result?.empty_reason_code).trim();
    if (fieldMissingBlockedRecords > 0) {
      const diagnostics = Array.isArray(result?.field_missing_diagnostics)
        ? result.field_missing_diagnostics
        : [];
      const firstMissingField = diagnostics
        .flatMap((item) => Array.isArray(item?.missing_fields) ? item.missing_fields : [])
        .map((item) => text(item?.export_field || item?.field || item?.canonical_field || item?.message).trim())
        .find(Boolean);
      return [
        `有 ${fieldMissingBlockedRecords} 条记录因导出字段缺失被阻断`,
        firstMissingField ? `首个缺失字段：${firstMissingField}` : "",
      ].filter(Boolean).join("；");
    }
    if (status === "failed") {
      return failureMessage || failureCode || "导出业务状态为 failed。";
    }
    if (status === "empty") {
      return failureMessage || emptyReasonCode || "当前条件下没有可导出的记录。";
    }
    if (status !== "completed" && status !== "success") {
      return status ? `导出返回未知业务状态：${status}。` : "导出返回缺少业务状态。";
    }
    return "";
  }

  /* ── State ── */
  let currentPanel = "overview";
  let overview = {};
  let jobs = [];
  let records = { rows: [], summary: {}, display_columns: [] };
  let reviewProblems = { rows: [], summary: {}, total_count: 0 };
  let reviewProblemLoading = false;
  let reviewProblemError = "";
  let reviewProblemFilters = { problem_kind: "all", record_family: "all", business_id: "all", exchange: "all", state: "all", keyword: "", page: 1, page_size: 50 };
  let reviewProblemRequestSeq = 0;
  let exportHistory = { rows: [] };
  let exportHistoryDetail = null;
  let exportHistoryLoading = false;
  let exportHistoryError = "";
  let exportHistoryMessage = "";
  let selectedExportHistoryId = "";
  let mappings = { sections: [], summary: {}, entries: [] };
  let catalog = {};
  let settings = { basic: {}, advanced: {}, runtime: {} };
  let catalogLoadError = "";
  let basicSettingsLoadError = "";
  let recordPage = 1;
  let recordPageSize = 50;
  let recordFilters = { record_family: "", state: "all", business_id: "all", exchange: "all", keyword: "", date_from: "", date_to: "" };
  let pollTimer = null;
  let overviewEventSource = null;
  let errorMsg = "";
  let currentEvents = [];
  let mappingDraft = createInitialMappingDraft();
  let mappingPreview = null;
  let mappingMessage = "";
  let settingsMessage = "";
  let settingsFormDraft = null;
  let familyStats = null;
  let activeFamilyTab = "";
  let panelNavigationSeq = 0;
  let overviewRequestSeq = 0;
  let overviewStreamRevision = 0;
  let familyStatsLoadPromise = null;

  function buildSettingsFormDraft() {
    const {
      defaults,
      basicPaths,
      processing,
      ingestPaths,
    } = buildSettingsViewModel(settings);
    const defaultScopeEditor = resolveSettingsDefaultScopeEditor({
      catalog,
      basicSettings: settings.basic,
    });
    const selectedFamilyId = text(defaultScopeEditor.selected_family_id).trim();
    return {
      default_exchange: text(defaults.default_exchange || "all").trim() || "all",
      default_concurrency: text(defaults.default_concurrency || 4).trim() || "4",
      retention_count: text(defaults.retention_count || 20).trim() || "20",
      record_family: selectedFamilyId,
      business_id: text(defaultScopeEditor.selected_business_id || "").trim() || (selectedFamilyId ? "all" : ""),
      scope_exchange: text(defaultScopeEditor.selected_exchange || "all").trim() || "all",
      archive_root: text(basicPaths.archive_root).trim(),
      export_root: text(basicPaths.export_root).trim(),
      postprocess_config: text(processing.postprocess_config).trim(),
      raw_manual_root: text(ingestPaths.raw_manual_root).trim(),
      raw_auto_root: text(ingestPaths.raw_auto_root || basicPaths.archive_root).trim(),
      save_json: Boolean(processing.save_json),
    };
  }

  function ensureSettingsFormDraft() {
    if (!settingsFormDraft) {
      settingsFormDraft = buildSettingsFormDraft();
    }
    return settingsFormDraft;
  }

  function patchSettingsFormDraft(patch = {}) {
    settingsFormDraft = {
      ...ensureSettingsFormDraft(),
      ...patch,
    };
    return settingsFormDraft;
  }

  function catalogSourceOptions({ includeAll = true } = {}) {
    const normalizedCatalog = normalizeCatalogResource(catalog || {});
    const sourceOptions = normalizedCatalog.sources
      .map((source) => ({
        source_id: text(source.source_id).trim(),
        source_label: text(source.source_label || source.source_id).trim(),
      }))
      .filter((source) => source.source_id);
    return includeAll
      ? [{ source_id: "all", source_label: "全部交易所" }, ...sourceOptions]
      : sourceOptions;
  }
  const actionModals = createActionModals({
    $,
    API,
    escapeHtml,
    text,
    display,
    chooseLocalPath,
    getCatalog: () => catalog,
    getSettings: () => settings,
    getOverview: () => overview,
    runHistorical: handleHistorical,
    runOneClick: handleOneClick,
    runManualImport: submitManualImport,
  });
  const tasksPanel = createTasksPanel({
    $,
    $$,
    API,
    escapeHtml,
    num,
    formatJobTime,
    jobTypeLabel,
    jobStatusLabel,
    stateDotClass,
    getJobs: () => jobs,
    setJobs: (nextJobs) => { jobs = nextJobs; },
    onRetry: handleJobRetry,
  });
  const recordsPanel = createRecordsPanel({
    $,
    $$,
    API,
    escapeHtml,
    display,
    formatJobTime,
    num,
    buildRecordsScope,
    recordCellValue,
    handleExport,
    onReprocess: handleRecordReprocess,
    getCatalog: () => catalog,
    getOverview: () => overview,
    setOverview: (nextOverview) => { overview = nextOverview; },
    getRecords: () => records,
    setRecords: (nextRecords) => { records = nextRecords; },
    getRecordFilters: () => recordFilters,
    setRecordFilters: (nextFilters) => { recordFilters = nextFilters; },
    getRecordPage: () => recordPage,
    setRecordPage: (nextPage) => { recordPage = nextPage; },
    getRecordsBrowseRuntime: () => currentRecordsBrowseRuntime(),
  });
  const exportHistoryPanel = createExportHistoryPanel({
    $,
    $$,
    API,
    escapeHtml,
    display,
    formatJobTime,
    num,
    getHistory: () => exportHistory,
    setHistory: (nextHistory) => { exportHistory = nextHistory || { rows: [] }; },
    getDetail: () => exportHistoryDetail,
    setDetail: (nextDetail) => { exportHistoryDetail = nextDetail || null; },
    getLoading: () => exportHistoryLoading,
    setLoading: (nextLoading) => { exportHistoryLoading = Boolean(nextLoading); },
    getError: () => exportHistoryError,
    setError: (nextError) => { exportHistoryError = text(nextError).trim(); },
    getMessage: () => exportHistoryMessage,
    setMessage: (nextMessage) => { exportHistoryMessage = text(nextMessage).trim(); },
    getSelectedExportId: () => selectedExportHistoryId,
    setSelectedExportId: (nextId) => { selectedExportHistoryId = text(nextId).trim(); },
  });
  const mappingsPanel = createMappingsPanel({
    $,
    $$,
    MAPPING_RULES,
    escapeHtml,
    display,
    formatJobTime,
    num,
    getMappings: () => mappings,
    getMappingDraft: () => mappingDraft,
    getMappingPreview: () => mappingPreview,
    getMappingMessage: () => mappingMessage,
    buildMappingTargetControl,
    onRuleKindChange(nextRuleKind) {
      mappingDraft.rule_kind = nextRuleKind;
      mappingPreview = null;
      renderMappings();
    },
    onDraftInput(field, value) {
      mappingDraft = {
        ...mappingDraft,
        [field]: value,
      };
    },
    async onDraftPreview() {
      try {
        mappingMessage = "";
        mappingPreview = await API.previewMapping(buildMappingDraftPayload());
        mappingDraft.confirm_overwrite = false;
        renderMappings();
      } catch (e) {
        mappingMessage = `失败: ${e.message}`;
        renderMappings();
      }
    },
    async onDraftSave() {
      try {
        mappingMessage = "";
        const editingEntryId = text(mappingDraft.entry_id).trim();
        const result = editingEntryId
          ? await API.updateMapping(editingEntryId, buildMappingDraftPayload())
          : await API.saveMapping(buildMappingDraftPayload());
        const actionLabel = editingEntryId ? "更新" : "保存";
        mappingMessage = result.job_id
          ? `规则已${actionLabel}，已启动回刷任务（影响 ${num(result.affected_count)} 条记录）`
          : `规则已${actionLabel}${result.scope_miss ? "，但当前没有命中记录" : ""}`;
        mappingPreview = null;
        mappingDraft = { ...createInitialMappingDraft(), rule_kind: mappingDraft.rule_kind };
        await loadMappings();
        await refresh();
      } catch (e) {
        mappingMessage = `失败: ${e.message}`;
        renderMappings();
      }
    },
    onDraftReset() {
      mappingDraft = createInitialMappingDraft();
      mappingPreview = null;
      mappingMessage = "";
      renderMappings();
    },
    onUseSuggestion({ item, resolution }) {
      fillMappingDraft(resolution, item.status_detail || "");
      mappingMessage = `已带入 ${display(item.project_code || item.record_id)} 的规则建议，请先预览再保存。`;
      renderMappings();
    },
    async onResolveConflict({ item, resolution }) {
      const confirmed = window.confirm(
        `将把 ${display(item.project_code)} 裁决为“${display(resolution.title || resolution.rule_kind)} · ${display(resolution.target_value)}”。系统会保存 authoritative 映射规则并立即回刷相关记录，是否继续？`,
      );
      if (!confirmed) return;
      try {
        mappingMessage = "";
        const result = await API.resolveMappingConflict({
          record_id: item.record_id,
          selected_resolution: resolution,
          notes: buildConflictResolutionNotes(item, resolution),
        });
        mappingPreview = null;
        mappingDraft = createInitialMappingDraft();
        mappingMessage = result.job_id
          ? `已裁决 ${display(item.project_code)}，并启动映射回刷任务（影响 ${num(result.affected_count)} 条记录）`
          : `已裁决 ${display(item.project_code)}，规则已保存`;
        await loadMappings();
        await refresh();
      } catch (e) {
        mappingMessage = `失败: 冲突裁决失败: ${e.message}`;
        renderMappings();
      }
    },
    async onSectionAction(section) {
      try {
        mappingMessage = "";
        if (section.cta_kind === "reprocess_pending") {
          await API.reprocessPendingMappings();
        } else {
          return;
        }
        await loadMappings();
        await refresh();
      } catch (e) {
        mappingMessage = `失败: 映射回刷失败: ${e.message}`;
        renderMappings();
      }
    },
    onEditEntry(entry) {
      fillMappingDraft(entry, entry.notes || "");
      mappingMessage = `正在编辑规则：${display(entry.source_name)} → ${display(entry.target_value)}`;
      renderMappings();
    },
    async onDeleteEntry(entry) {
      const confirmed = window.confirm(
        `将删除规则“${display(entry.rule_title)} · ${display(entry.source_name)} → ${display(entry.target_value)}”。系统会回刷受影响记录，是否继续？`,
      );
      if (!confirmed) return;
      try {
        const editingEntryId = text(mappingDraft.entry_id).trim();
        mappingMessage = "";
        mappingPreview = null;
        const result = await API.deleteMapping(entry.entry_id);
        if (!result.deleted) {
          throw new Error(`删除规则未成功${result.entry_id ? `: ${result.entry_id}` : ""}`);
        }
        if (editingEntryId && editingEntryId === text(entry.entry_id).trim()) {
          mappingDraft = { ...createInitialMappingDraft(), rule_kind: mappingDraft.rule_kind };
        }
        mappingMessage = result.job_id
          ? `规则已删除，已启动回刷任务（影响 ${num(result.affected_count)} 条记录）`
          : "规则已删除";
        await loadMappings();
        await refresh();
      } catch (e) {
        mappingMessage = `失败: 删除规则失败: ${e.message}`;
        renderMappings();
      }
    },
    async onUndo(undo) {
      const startupSessionId = text(undo?.startup_session_id).trim();
      if (!undo?.available || !startupSessionId) return;
      const confirmed = window.confirm("将撤销本次启动后最近一次映射规则变更，并回刷受影响记录，是否继续？");
      if (!confirmed) return;
      try {
        mappingMessage = "";
        const result = await API.undoMapping(startupSessionId);
        if (!result.undone) throw new Error("撤销操作未完成");
        mappingPreview = null;
        mappingDraft = createInitialMappingDraft();
        mappingMessage = "已撤销上次规则变更";
        await loadMappings();
        await refresh();
      } catch (e) {
        mappingMessage = `失败: 撤销规则变更失败: ${e.message}`;
        renderMappings();
      }
    },
  });

  function currentDefaultScopeRuntime(surface = "records") {
    const runtime = resolveDefaultScopeRuntime({
      catalog,
      basicSettings: settings.basic,
      surface,
      error: catalogLoadError || basicSettingsLoadError || null,
    });
    return {
      ...runtime,
      message: describeDefaultScopeRuntime(runtime),
    };
  }

  function currentRecordsBrowseRuntime() {
    const runtime = resolveRecordsBrowseRuntime({
      catalog,
      basicSettings: settings.basic,
      error: catalogLoadError || null,
    });
    return {
      ...runtime,
      message: describeRecordsBrowseRuntime(runtime),
    };
  }

  function buildRecordsScope() {
    return buildRecordsScopeFromBrowseRuntime(currentRecordsBrowseRuntime(), recordFilters, {
      page: recordPage,
      page_size: recordPageSize,
    });
  }

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
      case "reviews": renderReviews(); break;
      case "export-history": renderExportHistory(); break;
      case "mappings": renderMappings(); break;
      case "settings": renderSettings(); break;
    }
  }

  /* ── Overview ── */
  function renderOverview({ skipAnimation = false } = {}) {
    const el = $("#panel-overview");
    const overviewView = buildOverviewViewModel(overview);
    const latestJob = overview.latest_job || null;
    const recentJobs = Array.isArray(overview.recent_jobs) ? overview.recent_jobs : [];
    const stateCounts = overviewView.stateCounts;
    const latestProgress = overview.latest_progress || {};
    const browserRt = overviewView.browser;
    const browserInstall = overviewView.install;
    const primaryStats = buildOverviewPrimaryStats(overviewView);
    const jobRunning = latestJob && isActive(latestJob.status);

    const isInstalling = browserInstall.status === "running";
    const browserReady = browserRt.installed;
    const headline = overviewView.headline;
    const browserState = overviewView.browserState;
    const runtimeIssues = overviewView.runtimeIssues;

    const progress = latestProgress;
    const pct = isTerminal(latestJob?.status) ? 100 : Math.max(0, Math.min(100, num(progress.phase_percent)));
    const progressHintText = buildOverviewProgressFallback(latestJob || {}, progress, { num });
    const anim = skipAnimation ? "" : "animate-in ";
    const animD = (n) => skipAnimation ? "" : `animate-in delay-${n} `;

    el.innerHTML = `
      <div class="${anim}">
        <h1 class="page-title">总览</h1>
      </div>

      <!-- 快捷操作 -->
      <section class="${animD(1)}">
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
          <button class="action-btn" id="btn-archive-reprocess">
            <div class="icon">&#8635;</div>
            <span class="label">重新解析+后处理</span>
            <span class="sublabel">从归档文件重建数据库记录</span>
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
      <section class="${animD(2)}">
        <p class="section-label">记录统计</p>
        <div class="stats-grid">
          ${primaryStats.map((item) => `
          <div class="stat-card">
            <div class="stat-label">${item.label}</div>
            <div class="stat-value"${item.tone === "warning" ? ' style="color:var(--warning)"' : ""}>${item.value}</div>
            <div class="stat-sub">${item.sublabel}</div>
          </div>`).join("")}
          <div class="stat-card">
            <div class="stat-label">最新任务</div>
            <div class="stat-value" style="font-size:20px">${jobRunning ? "进行中" : latestJob ? "已完成" : "无"}</div>
            <div class="stat-sub">${latestJob ? formatTimeAgo(latestJob.created_at) : "暂无任务记录"}</div>
          </div>
        </div>
      </section>

      ${familyStats ? `
      <!-- 按业务类别统计 -->
      <section class="${animD(3)}">
        <p class="section-label">按业务类别统计</p>
        <div class="card">
          <div style="display:flex;gap:var(--space-2);margin-bottom:var(--space-4)">
            ${Object.entries(familyStats).map(([familyId, info]) => `
              <button class="btn btn-sm${activeFamilyTab === familyId ? " active" : ""}" data-family-tab="${escapeHtml(familyId)}">${escapeHtml(info.label)}</button>
            `).join("")}
          </div>
          <div id="family-stats-content">
            ${(() => {
              const activeStats = familyStats[activeFamilyTab];
              if (!activeStats) return '<div style="font-size:13px;color:var(--text-muted)">暂无数据</div>';
              const familyPrimaryStats = buildFamilyStatsPrimaryStats(activeStats.stateCounts);
              return `<div class="stats-grid">
                ${familyPrimaryStats.map((item) => `
                <div class="stat-card">
                  <div class="stat-label">${item.label}</div>
                  <div class="stat-value"${item.tone === "warning" ? ' style="color:var(--warning)"' : ""}>${item.value}</div>
                  <div class="stat-sub">${item.sublabel}</div>
                </div>`).join("")}
              </div>`;
            })()}
          </div>
        </div>
      </section>` : ""}

      <!-- 当前任务进度 -->
      ${latestJob ? `
      <section class="progress-section ${animD(3)}">
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
                if (isJobEventFailure(ev)) {
                  return `<div class="progress-log-item error">
                    <span class="progress-log-stage">${eventStageLabel(ev)}</span>
                    <span class="progress-log-msg">${buildJobEventErrorText(ev)}</span>
                  </div>`;
                }

                if (ev.status === "running" && !currentEntry) {
                  currentEntry = `<div class="progress-log-item">
                    <span class="progress-log-stage">${eventStageLabel(ev)}</span>
                    <span class="progress-log-msg">${buildJobEventLogText(ev, { num })}</span>
                  </div>`;
                  continue;
                }

                if (ev.status === "done" && hasJobEventActivity(ev)) {
                  doneCount++;
                  currentEntry = (currentEntry || "") + `<div class="progress-log-item">
                    <span class="progress-log-stage">${eventStageLabel(ev)}</span>
                    <span class="progress-log-msg">${buildJobEventLogText(ev, { num })}</span>
                  </div>`;
                  if (doneCount >= maxDone) break;
                }
              }
              return currentEntry || "";
            })()}
          </div>` : `
          <div class="progress-hint">
            ${escapeHtml(progressHintText)}
          </div>`}
        </div>
      </section>` : ""}

      <!-- 最近任务 -->
      <section class="jobs-section ${animD(4)}">
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
              const recentJob = buildOverviewRecentJobView(job, { num });
              const badge = recentJob.badge;
              const meta = badge.compact || badge.tone === "plain"
                ? `<span class="job-count">${escapeHtml(display(badge.text))}</span>`
                : `<span class="badge ${badge.tone === "failed" ? "failed" : "ready"}"><span class="badge-dot"></span>${escapeHtml(display(badge.text))}</span>`;
              return `<li class="job-item">
                <span class="job-status-dot ${stateDotClass(s)}"></span>
                <div class="job-info">
                  <div class="job-type">${jobTypeLabel(job.job_type)}</div>
                  <div class="job-time">${formatJobTime(job.created_at)} · ${formatTimeAgo(job.created_at)}</div>
                  ${recentJob.metaText ? `<div class="job-time">${escapeHtml(recentJob.metaText)}</div>` : ""}
                </div>
                ${meta}
              </li>`;
            }).join("")}
          </ul>`}
        </div>
      </section>

      <!-- 运行环境 -->
      <section class="${animD(5)}">
        <p class="section-label">运行环境</p>
        <div class="card">
          <div style="font-size:15px;font-weight:600;color:var(--text);margin-bottom:4px">${headline}</div>
          <div style="font-size:13px;color:var(--text-muted);margin-bottom:8px">${browserState}</div>
          ${runtimeIssues.map((d) => `<div style="font-size:12px;color:var(--text-faint);margin-bottom:2px">${d}</div>`).join("")}
        </div>
      </section>
    `;

    $("#btn-oneclick").addEventListener("click", actionModals.showOneClickModal);
    $("#btn-historical").addEventListener("click", actionModals.showHistoricalModal);
    $("#btn-import").addEventListener("click", handleManualImport);
    $("#btn-archive-reprocess").addEventListener("click", handleArchiveReprocess);
    $("#btn-export").addEventListener("click", () => handleExport());

    $$("[data-family-tab]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const familyId = btn.dataset.familyTab;
        if (familyId && familyId !== activeFamilyTab) {
          activeFamilyTab = familyId;
          renderOverview();
        }
      });
    });
  }

  /* ── Tasks ── */
  function renderTasks() {
    tasksPanel.render();
  }

  async function loadJobs() {
    await tasksPanel.load();
  }

  function renderTasksList() {
    tasksPanel.renderList();
  }

  /* ── Records ── */
  function renderRecordsLayout() {
    recordsPanel.renderLayout();
  }

  function updateRecordsTable() {
    recordsPanel.updateTable();
  }

  async function loadRecords({ includeOverview = false } = {}) {
    await recordsPanel.load({ includeOverview });
  }

  function renderRecords() {
    recordsPanel.render();
  }

  const reviewsPanel = createReviewsPanel({
    $,
    escapeHtml,
    display,
    formatJobTime,
    getReviewProblems: () => reviewProblems,
    getLoading: () => reviewProblemLoading,
    getError: () => reviewProblemError,
    getFilters: () => reviewProblemFilters,
    onFilterChange: async (key, value) => {
      reviewProblemFilters = { ...reviewProblemFilters, [key]: value, page: key === "page" ? value : 1 };
      await loadReviewProblems();
    },
  });

  function renderReviews() {
    reviewsPanel.render();
  }

  function renderExportHistory() {
    exportHistoryPanel.render();
  }

  async function loadExportHistory() {
    await exportHistoryPanel.load();
  }

  async function loadReviewProblems() {
    const requestSeq = ++reviewProblemRequestSeq;
    reviewProblemLoading = true;
    reviewProblemError = "";
    if (currentPanel === "reviews") renderReviews();
    try {
      const nextReviewProblems = await API.listReviewProblems(reviewProblemFilters);
      if (requestSeq !== reviewProblemRequestSeq) return;
      reviewProblems = nextReviewProblems;
    } catch (e) {
      if (requestSeq !== reviewProblemRequestSeq) return;
      console.error("Review problems load failed:", e);
      reviewProblemError = e && e.message ? e.message : "load failed";
    } finally {
      if (requestSeq !== reviewProblemRequestSeq) return;
      reviewProblemLoading = false;
      if (currentPanel === "reviews") renderReviews();
    }
  }

  /* ── Mappings ── */
  function renderMappings() {
    mappingsPanel.render();
  }

  async function loadMappings() {
    try {
      mappings = await API.listMappings();
      if (currentPanel === "mappings") renderMappings();
    } catch (e) {
      console.error("Mappings load failed:", e);
      mappings = { sections: [], summary: {}, entries: [] };
      mappingPreview = null;
      mappingMessage = `失败: ${e.message}`;
      if (currentPanel === "mappings") renderMappings();
    }
  }

  /* ── Settings ── */
  function renderSettings() {
    const el = $("#panel-settings");
    el.innerHTML = `
      <div class="animate-in"><h1 class="page-title">设置</h1></div>
      <section class="card animate-in delay-1">
        <div style="font-size:13px;color:var(--text-muted);padding:12px 0">加载中...</div>
      </section>
    `;
    loadSettings({ preserveDraft: true });
  }

  function renderSettingsLoadFailure(message) {
    $("#panel-settings").innerHTML = `
      <div class="animate-in"><h1 class="page-title">设置</h1></div>
      <section class="card animate-in delay-1">
        <div class="alert alert-danger">${escapeHtml(message)}</div>
      </section>
    `;
  }

  async function loadSettings({ preserveDraft = false, throwOnFailure = false } = {}) {
    try {
      const [basic, advanced, runtime, nextCatalog] = await Promise.all([
        API.getSettingsBasic(),
        API.getSettingsAdvanced(),
        API.getRuntimeDependencies(),
        API.getCatalog(),
      ]);
      catalog = nextCatalog || {};
      catalogLoadError = "";
      basicSettingsLoadError = "";
      settings = {
        basic: basic || {},
        advanced: advanced || {},
        runtime: runtime || {},
      };
      if (!preserveDraft) {
        settingsFormDraft = null;
      }
      renderSettingsForm();
    } catch (e) {
      const message = `加载失败: ${e.message}`;
      renderSettingsLoadFailure(message);
      if (throwOnFailure) {
        throw Object.assign(new Error(message), { settingsReloadFailed: true });
      }
    }
  }

  async function loadCatalog() {
    try {
      catalog = await API.getCatalog();
      catalogLoadError = "";
    } catch (e) {
      catalog = {};
      catalogLoadError = e.message;
      console.error("Catalog load failed:", e);
    }
  }

  async function loadBasicSettings() {
    try {
      settings = {
        ...settings,
        basic: await API.getSettingsBasic(),
      };
      settingsFormDraft = null;
      basicSettingsLoadError = "";
    } catch (e) {
      settings = {
        ...settings,
        basic: {},
      };
      basicSettingsLoadError = e.message;
      console.error("Basic settings load failed:", e);
    }
  }

  function renderSettingsForm() {
    const {
      defaults,
      defaultScope,
      basicPaths,
      processing,
      ingestPaths,
      runtimePaths,
      browser,
      install,
      readiness,
    } = buildSettingsViewModel(settings);
    const settingsDraft = ensureSettingsFormDraft();
    const storedPreference = defaultScope.stored_preference || {};
    const effectiveDefaultScope = defaultScope.effective_default_scope || {};
    const staleDefaultMetadata = defaultScope.stale_default_metadata || {};
    const defaultScopeEditor = resolveSettingsDefaultScopeEditor({
      catalog,
      basicSettings: settings.basic,
      selectedFamilyId: settingsDraft.record_family,
      selectedBusinessId: settingsDraft.business_id,
      selectedExchange: settingsDraft.scope_exchange,
    });
    const familyOptions = Array.isArray(defaultScopeEditor.family_options) ? defaultScopeEditor.family_options : [];
    const selectedFamilyId = text(defaultScopeEditor.selected_family_id).trim();
    const businessOptions = Array.isArray(defaultScopeEditor.business_options) ? defaultScopeEditor.business_options : [];
    const selectedBusinessId = text(defaultScopeEditor.selected_business_id).trim();
    const selectedScopeExchange = text(defaultScopeEditor.selected_exchange).trim();
    const scopeExchangeOptions = Array.isArray(defaultScopeEditor.exchange_options) ? defaultScopeEditor.exchange_options : [];
    const partialScopeExchangeOptions = Array.isArray(defaultScopeEditor.partial_exchange_options) ? defaultScopeEditor.partial_exchange_options : [];
    if (
      settingsDraft.record_family !== selectedFamilyId
      || settingsDraft.business_id !== selectedBusinessId
      || settingsDraft.scope_exchange !== selectedScopeExchange
    ) {
      settingsFormDraft = {
        ...settingsDraft,
        record_family: selectedFamilyId,
        business_id: selectedBusinessId,
        scope_exchange: selectedScopeExchange,
      };
    }
    const isActionableScopeLike = (scope = {}) => {
      const recordFamily = text(scope.record_family).trim();
      const businessId = text(scope.business_id).trim();
      const exchange = text(scope.exchange).trim();
      return Boolean(recordFamily && businessId && exchange);
    };
    const bannerScope = isActionableScopeLike(effectiveDefaultScope) ? effectiveDefaultScope : storedPreference;
    const bannerIsActionable = isActionableScopeLike(bannerScope);
    const bannerBusinessLabel = businessTypeLabel(
      text(bannerScope.business_id).trim(),
      display(bannerScope.business_label || bannerScope.business_id || "未设置"),
    );
    const bannerExchangeLabel = scopeExchangeOptions.find((option) =>
      text(option.source_id).trim() === text(bannerScope.exchange || "all").trim()
    )?.source_label
      || display(bannerScope.exchange || "all");
    const defaultExchangeOptions = catalogSourceOptions({ includeAll: true });
    const businessOptionMarkup = selectedFamilyId
      ? [
          `<option value="" ${selectedBusinessId ? "" : "selected"}>不设置（清空默认执行范围）</option>`,
          ...businessOptions.map(({ business_id, business_label, business_display_label, unavailable }) => {
            const displayLabel = business_display_label || business_label || business_id;
            const rendered = unavailable ? `${displayLabel}（已失效）` : displayLabel;
            return `<option value="${escapeHtml(business_id)}" ${selectedBusinessId === business_id ? "selected" : ""}>${escapeHtml(rendered)}</option>`;
          }),
        ].join("")
      : `<option value="" selected>请先选择业务类别</option>`;
    const el = $("#panel-settings");
    el.innerHTML = `
      <div class="animate-in"><h1 class="page-title">设置</h1></div>
      ${settingsMessage ? `<div class="alert ${String(settingsMessage).startsWith("失败") ? "alert-danger" : "alert-success"} animate-in delay-1">${escapeHtml(settingsMessage)}</div>` : ""}
      <section class="card animate-in delay-1" style="margin-bottom:var(--space-4)">
        <p class="section-label">基本设置</p>
        <div class="alert" style="margin-bottom:var(--space-4)">
          <div>
            默认执行范围（用于一键执行 / 历史区间 / 导出）：<strong>${escapeHtml(bannerBusinessLabel)}</strong>
            · 交易所 <strong>${escapeHtml(bannerExchangeLabel)}</strong>
          </div>
          <div style="margin-top:6px;font-size:13px;color:var(--text-muted)">
            ${bannerIsActionable
              ? "只有在未显式选择业务类型与交易所时才会用到这里；记录页浏览不依赖默认执行范围。"
              : "当前默认执行范围未设置完整；启动任务时需显式选择业务类型与交易所。"}
          </div>
          ${staleDefaultMetadata.is_stale ? `<br>${escapeHtml(display(staleDefaultMetadata.hint || staleDefaultMetadata.reason || "默认范围已失效，请重新选择。"))}` : ""}
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>默认交易所（界面默认值）</label>
            <select id="settings-default-exchange">
              ${defaultExchangeOptions.map((option) => `<option value="${escapeHtml(text(option.source_id))}" ${text(settingsDraft.default_exchange) === text(option.source_id) ? "selected" : ""}>${escapeHtml(text(option.source_label))}</option>`).join("")}
            </select>
            <div style="margin-top:6px;font-size:12px;color:var(--text-muted)">
              用于筛选和表单默认选项；不会自动改写“默认执行范围交易所”。
            </div>
          </div>
          <div class="form-group">
            <label>默认并发数</label>
            <input type="number" id="settings-default-concurrency" min="1" step="1" value="${escapeHtml(text(settingsDraft.default_concurrency || defaults.default_concurrency || 4))}">
          </div>
          <div class="form-group">
            <label>导出历史保留数</label>
            <input type="number" id="settings-retention-count" min="1" step="1" value="${escapeHtml(text(settingsDraft.retention_count || defaults.retention_count || 20))}">
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>业务类别（默认执行范围）</label>
            <select id="settings-default-family" ${familyOptions.length <= 1 ? "disabled" : ""}>
              ${familyOptions.length
                ? familyOptions.map(({ family_id, family_label, family_display_label }) => {
                  const displayLabel = family_display_label || recordFamilyLabel(family_id, family_label || family_id);
                  return `<option value="${escapeHtml(family_id)}" ${selectedFamilyId === family_id ? "selected" : ""}>${escapeHtml(displayLabel)}</option>`;
                }).join("")
                : `<option value="">暂无可用业务类别</option>`}
            </select>
            <div style="margin-top:6px;font-size:12px;color:var(--text-muted)">
              业务类别是业务大类；用于约束下方“业务类型”的可选项。
            </div>
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>业务类型（默认执行范围）</label>
            <select id="settings-default-business">
              ${businessOptionMarkup}
            </select>
            <div style="margin-top:6px;font-size:12px;color:var(--text-muted)">
              仅当一键执行/历史区间/导出没有显式选择业务类型时才使用；选择“全部业务类型”会覆盖该业务类别下全部业务，选择“不设置”会清空默认执行范围。
            </div>
          </div>
          <div class="form-group">
            <label>交易所（默认执行范围）</label>
            <select id="settings-default-scope-exchange">
              ${scopeExchangeOptions.map((option) => `<option value="${escapeHtml(text(option.source_id))}" ${selectedScopeExchange === text(option.source_id) ? "selected" : ""}>${escapeHtml(text(option.source_label))}</option>`).join("")}
              ${partialScopeExchangeOptions.map((option) => `<option value="partial:${escapeHtml(text(option.source_id))}" disabled>${escapeHtml(text(option.source_label))}（仅部分业务）</option>`).join("")}
            </select>
            <div style="margin-top:6px;font-size:12px;color:var(--text-muted)">
              与上方“默认交易所（界面默认值）”互不影响；这里只决定默认执行范围落在哪个交易所。
            </div>
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>工作区目录</label>
            ${buildPathFieldControl({
              inputId: "settings-workspace-root",
              value: basicPaths.workspace_root,
              readOnly: true,
              openButtonId: "btn-open-workspace-root",
            })}
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>归档目录</label>
            ${buildPathFieldControl({
              inputId: "settings-archive-root",
              value: settingsDraft.archive_root || basicPaths.archive_root,
              readOnly: true,
              browseButtonId: "btn-settings-archive-root-browse",
              browseButtonLabel: "选择目录",
              openButtonId: "btn-open-archive-root",
            })}
          </div>
          <div class="form-group">
            <label>导出目录</label>
            ${buildPathFieldControl({
              inputId: "settings-export-root",
              value: settingsDraft.export_root || basicPaths.export_root,
              readOnly: true,
              browseButtonId: "btn-settings-export-root-browse",
              browseButtonLabel: "选择目录",
              openButtonId: "btn-open-export-root",
            })}
          </div>
        </div>
        <div style="display:flex;gap:var(--space-3)">
          <button class="btn btn-primary" id="btn-settings-basic-save">保存基本设置</button>
        </div>
      </section>

      <section class="card animate-in delay-2" style="margin-bottom:var(--space-4)">
        <p class="section-label">高级设置</p>
        <div class="form-row">
          <div class="form-group">
            <label>后处理配置</label>
            ${buildPathFieldControl({
              inputId: "settings-postprocess-config",
              value: settingsDraft.postprocess_config || processing.postprocess_config,
              readOnly: true,
              browseButtonId: "btn-settings-postprocess-browse",
              browseButtonLabel: "选择文件",
              openButtonId: "btn-open-postprocess-config",
              openButtonLabel: "定位文件",
              openReveal: true,
            })}
          </div>
          <div class="form-group">
            <label>数据库路径</label>
            ${buildPathFieldControl({
              inputId: "settings-streaming-db",
              value: runtimePaths.streaming_db,
              readOnly: true,
              openButtonId: "btn-open-streaming-db",
              openButtonLabel: "定位文件",
              openReveal: true,
            })}
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>手动导入目录</label>
            ${buildPathFieldControl({
              inputId: "settings-raw-manual-root",
              value: settingsDraft.raw_manual_root || ingestPaths.raw_manual_root,
              readOnly: true,
              browseButtonId: "btn-settings-raw-manual-root-browse",
              browseButtonLabel: "选择目录",
              openButtonId: "btn-open-raw-manual-root",
            })}
          </div>
          <div class="form-group">
            <label>自动归档目录（跟随归档目录）</label>
            ${buildPathFieldControl({
              inputId: "settings-raw-auto-root",
              value: settingsDraft.archive_root || ingestPaths.raw_auto_root || basicPaths.archive_root,
              readOnly: true,
              openButtonId: "btn-open-raw-auto-root",
            })}
            <div style="margin-top:6px;font-size:12px;color:var(--text-muted)">
              自动抓取产生的 HTML 会直接归档到这里；它与“归档目录”保持同一条真相，不再单独设置。
            </div>
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>日志目录</label>
            ${buildPathFieldControl({
              inputId: "settings-log-dir",
              value: runtimePaths.log_dir,
              readOnly: true,
              openButtonId: "btn-open-log-dir",
            })}
          </div>
          <div class="form-group">
            <label>浏览器缓存目录</label>
            ${buildPathFieldControl({
              inputId: "settings-browser-cache-dir",
              value: runtimePaths.browser_cache_dir,
              readOnly: true,
              openButtonId: "btn-open-browser-cache-dir",
            })}
          </div>
        </div>
        <label style="display:inline-flex;align-items:center;gap:8px;font-size:13px;color:var(--text-muted)">
          <input type="checkbox" id="settings-save-json" ${settingsDraft.save_json ? "checked" : ""}>
          保存中间 JSON
        </label>
        <div style="display:flex;gap:var(--space-3);margin-top:var(--space-4)">
          <button class="btn btn-primary" id="btn-settings-advanced-save">保存高级设置</button>
        </div>
      </section>

      <section class="card animate-in delay-3">
        <p class="section-label">运行环境</p>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:var(--space-4);margin-bottom:var(--space-4)">
          <div>
            <div style="font-size:13px;color:var(--text-muted)">浏览器状态</div>
            <div style="font-size:16px;font-weight:600;color:var(--text);margin-top:4px">${browser.installed ? "已安装" : "未安装"}</div>
          </div>
          <div>
            <div style="font-size:13px;color:var(--text-muted)">安装任务</div>
            <div style="font-size:16px;font-weight:600;color:var(--text);margin-top:4px">${escapeHtml(display(install.status || "idle"))}</div>
          </div>
          <div>
            <div style="font-size:13px;color:var(--text-muted)">下载能力</div>
            <div style="font-size:16px;font-weight:600;color:var(--text);margin-top:4px">${readiness.download_ready ? "已就绪" : "未就绪"}</div>
          </div>
        </div>
        ${(install.message || browser.error || (Array.isArray(readiness.issues) && readiness.issues.length)) ? `
        <div class="alert alert-warning" style="margin-bottom:var(--space-4)">
          ${[install.message, browser.error, ...(Array.isArray(readiness.issues) ? readiness.issues.map((item) => item.message) : [])].filter(Boolean).map((item) => escapeHtml(item)).join("<br>")}
        </div>` : ""}
        <div style="display:flex;gap:var(--space-3)">
          <button class="btn btn-primary" id="btn-install-browser" ${browser.installed ? "disabled" : ""}>${browser.installed ? "浏览器已内置" : "安装浏览器"}</button>
        </div>
      </section>
    `;

    $$(".btn-open-path", el).forEach((button) => {
      button.addEventListener("click", async () => {
        const input = document.getElementById(button.dataset.inputId || "");
        const reveal = String(button.dataset.openReveal || "").trim() === "true";
        try {
          await openLocalPath(input?.value || "", { reveal });
        } catch (e) {
          settingsMessage = `失败: 打开目录失败: ${e.message}`;
          renderSettingsForm();
        }
      });
    });

    [
      { buttonId: "btn-settings-archive-root-browse", inputId: "settings-archive-root", selection_kind: "directory", prompt: "选择归档目录", draftField: "archive_root" },
      { buttonId: "btn-settings-export-root-browse", inputId: "settings-export-root", selection_kind: "directory", prompt: "选择导出目录", draftField: "export_root" },
      { buttonId: "btn-settings-postprocess-browse", inputId: "settings-postprocess-config", selection_kind: "file", prompt: "选择后处理配置文件", draftField: "postprocess_config" },
      { buttonId: "btn-settings-raw-manual-root-browse", inputId: "settings-raw-manual-root", selection_kind: "directory", prompt: "选择手动导入目录", draftField: "raw_manual_root" },
    ].forEach(({ buttonId, inputId, selection_kind, prompt, draftField }) => {
      $("#" + buttonId)?.addEventListener("click", async () => {
        try {
          const input = $("#" + inputId);
          const selectedPath = await chooseLocalPath({
            selection_kind,
            current_path: input?.value || "",
            prompt,
          });
          if (!selectedPath || !input) return;
          patchSettingsFormDraft({ [draftField]: selectedPath });
          input.value = selectedPath;
          if (draftField === "archive_root") {
            const rawAutoInput = $("#settings-raw-auto-root");
            if (rawAutoInput) rawAutoInput.value = selectedPath;
          }
        } catch (e) {
          settingsMessage = `失败: 选择路径失败: ${e.message}`;
          renderSettingsForm();
        }
      });
    });

    $("#settings-default-family")?.addEventListener("change", () => {
      patchSettingsFormDraft({
        record_family: $("#settings-default-family").value.trim(),
        business_id: "all",
      });
      renderSettingsForm();
    });
    $("#settings-default-business")?.addEventListener("change", () => {
      patchSettingsFormDraft({ business_id: $("#settings-default-business").value.trim() });
    });
    $("#settings-default-scope-exchange")?.addEventListener("change", () => {
      patchSettingsFormDraft({ scope_exchange: $("#settings-default-scope-exchange").value });
    });
    $("#settings-default-exchange")?.addEventListener("change", () => {
      patchSettingsFormDraft({ default_exchange: $("#settings-default-exchange").value });
    });
    $("#settings-default-concurrency")?.addEventListener("input", () => {
      patchSettingsFormDraft({ default_concurrency: $("#settings-default-concurrency").value.trim() });
    });
    $("#settings-retention-count")?.addEventListener("input", () => {
      patchSettingsFormDraft({ retention_count: $("#settings-retention-count").value.trim() });
    });
    $("#settings-save-json")?.addEventListener("change", () => {
      patchSettingsFormDraft({ save_json: $("#settings-save-json").checked });
    });

    $("#btn-settings-basic-save")?.addEventListener("click", async () => {
      try {
        settingsMessage = "";
        await API.saveSettingsBasic(buildBasicSettingsSavePayload({
          stored_preference: {
            record_family: $("#settings-default-family")?.value.trim() || selectedFamilyId,
            business_id: $("#settings-default-business").value.trim(),
            exchange: $("#settings-default-scope-exchange").value,
          },
          default_exchange: $("#settings-default-exchange").value,
          default_concurrency: Number($("#settings-default-concurrency").value || 1),
          retention_count: Number($("#settings-retention-count").value || 20),
          archive_root: $("#settings-archive-root").value.trim(),
          export_root: $("#settings-export-root").value.trim(),
        }));
        await loadSettings({ throwOnFailure: true });
        settingsMessage = "基本设置已保存。";
        renderSettingsForm();
      } catch (e) {
        settingsMessage = `失败: ${e.message}`;
        if (e?.settingsReloadFailed) {
          renderSettingsLoadFailure(settingsMessage);
        } else {
          renderSettingsForm();
        }
      }
    });

    $("#btn-settings-advanced-save")?.addEventListener("click", async () => {
      try {
        settingsMessage = "";
        await API.saveSettingsAdvanced(buildAdvancedSettingsSavePayload({
          postprocess_config: $("#settings-postprocess-config").value.trim(),
          raw_manual_root: $("#settings-raw-manual-root").value.trim(),
          raw_auto_root: $("#settings-archive-root").value.trim(),
          save_json: $("#settings-save-json").checked,
        }));
        await loadSettings({ throwOnFailure: true });
        settingsMessage = "高级设置已保存。";
        renderSettingsForm();
      } catch (e) {
        settingsMessage = `失败: ${e.message}`;
        if (e?.settingsReloadFailed) {
          renderSettingsLoadFailure(settingsMessage);
        } else {
          renderSettingsForm();
        }
      }
    });

    $("#btn-install-browser")?.addEventListener("click", async () => {
      try {
        settingsMessage = "";
        await API.installBrowser();
        await loadSettings();
        settingsMessage = "浏览器安装任务已启动。";
        renderSettingsForm();
      } catch (e) {
        settingsMessage = `失败: 安装失败: ${e.message}`;
        renderSettingsForm();
      }
    });
  }

  /* ── Actions ── */
  async function handleOneClick(payload = {}) {
    try {
      errorMsg = "";
      await runActiveJobGuardedAction({
        fetchOverview: () => API.getOverview(),
        actionLabel: "一键执行",
        execute: async () => {
          await API.runOneClick(payload);
        },
      });
      await loadOverviewData();
      renderOverview();
      openOverviewStream();
    } catch (e) {
      errorMsg = formatActionErrorMessage("一键执行失败", e);
      render();
      throw e;
    }
  }

  async function handleHistorical(payload = {}) {
    try {
      errorMsg = "";
      await API.runHistorical(payload);
      await refresh();
      openOverviewStream();
    } catch (e) {
      errorMsg = `历史区间任务失败: ${e.message}`;
      render();
      throw e;
    }
  }

  function handleManualImport() {
    actionModals.showManualImportModal();
  }

  async function submitManualImport(request) {
    try {
      errorMsg = "";
      await API.runManualImport(request);
      await refresh();
      openOverviewStream();
    } catch (e) {
      errorMsg = `手动导入失败: ${e.message}`;
      render();
      throw e;
    }
  }

  async function handleArchiveReprocess() {
    try {
      errorMsg = "";
      await runActiveJobGuardedAction({
        fetchOverview: () => API.getOverview(),
        actionLabel: "重新解析",
        execute: async () => {
          await API.runArchiveReprocess();
        },
      });
      await refresh();
      openOverviewStream();
    } catch (e) {
      errorMsg = formatActionErrorMessage("重新解析+后处理失败", e);
      render();
    }
  }

  async function handleJobRetry(job) {
    const jobId = text(job?.job_id).trim();
    if (!jobId) throw new Error("任务标识缺失，无法重试。");
    await runActiveJobGuardedAction({
      fetchOverview: () => API.getOverview(),
      actionLabel: "重试任务",
      execute: async () => {
        await API.retryJob(jobId);
      },
    });
    await loadOverviewData();
    openOverviewStream();
  }

  async function handleRecordReprocess(recordId) {
    const normalizedRecordId = text(recordId).trim();
    if (!normalizedRecordId) throw new Error("记录标识缺失，无法重新处理。");
    return runActiveJobGuardedAction({
      fetchOverview: async () => {
        overview = await API.getOverview();
        return overview;
      },
      actionLabel: "重新处理记录",
      execute: async () => API.reprocessRecord(normalizedRecordId),
    });
  }

  async function handleExport(precheckError = null) {
    try {
      errorMsg = "";
      if (precheckError) {
        throw precheckError;
      }
      const runtime = currentPanel === "records"
        ? currentRecordsBrowseRuntime()
        : currentDefaultScopeRuntime("export");
      const scope = currentPanel === "records"
        ? buildRecordsScope()
        : buildActionableScopeFromRuntime(runtime);
      if (!scope) {
        throw new Error(
          currentPanel === "records"
            ? (describeRecordsBrowseRuntime(runtime) || "记录范围不可用。")
            : (describeDefaultScopeRuntime(runtime) || "默认执行范围不可用。"),
        );
      }
      const validatedScope = resolveActionableDefaultScope(catalog, scope, { surface: "export" });
      if (!validatedScope) {
        if (currentPanel === "records") {
          throw new Error("当前筛选范围不支持导出，请选择该业务可用的交易所后重试。");
        }
        throw new Error("默认执行范围不支持导出，请到设置中重新选择可导出的业务与交易所。");
      }
      await runActiveJobGuardedAction({
        fetchOverview: () => API.getOverview(),
        actionLabel: "导出",
        execute: async () => {
          const result = await API.runExport(scope, "full");
          const businessError = buildExportBusinessError(result);
          if (businessError) {
            throw new Error(businessError);
          }
        },
      });
      await refresh();
    } catch (e) {
      errorMsg = formatActionErrorMessage("导出失败", e);
      render();
    }
  }

  /* ── Data Fetch ── */
  async function loadOverviewData() {
    const requestSeq = ++overviewRequestSeq;
    const streamAtStart = overviewEventSource;
    const streamRevisionAtStart = overviewStreamRevision;
    const nextOverview = await API.getOverview();
    if (
      requestSeq !== overviewRequestSeq
      || streamAtStart !== overviewEventSource
      || streamRevisionAtStart !== overviewStreamRevision
    ) return false;
    const latestJob = nextOverview.latest_job || null;
    let nextEvents = [];
    if (latestJob && isActive(latestJob.status)) {
      const eventsData = await API.getJobEvents(latestJob.job_id);
      if (
        requestSeq !== overviewRequestSeq
        || streamAtStart !== overviewEventSource
        || streamRevisionAtStart !== overviewStreamRevision
      ) return false;
      nextEvents = normalizeJobEventList(eventsData.events);
    }
    overview = nextOverview;
    currentEvents = nextEvents;
    return true;
  }

  async function loadFamilyStats() {
    if (familyStatsLoadPromise) return familyStatsLoadPromise;
    const loadPromise = (async () => {
      const visibleFamilies = Array.isArray(catalog.visible_families) ? catalog.visible_families : [];
      if (visibleFamilies.length <= 1) {
        familyStats = null;
        return;
      }
      const result = {};
      for (const family of visibleFamilies) {
        const familyId = String(family.family_id || "").trim();
        if (!familyId) continue;
        try {
          const data = await API.listRecords({
            record_family: familyId,
            state: "all",
            page: 1,
            page_size: 1,
          });
          result[familyId] = {
            label: recordFamilyLabel(familyId, family.family_label || familyId),
            stateCounts: (data?.summary?.filtered_state_counts) || {},
          };
        } catch (e) {
          result[familyId] = {
            label: recordFamilyLabel(familyId, family.family_label || familyId),
            stateCounts: {},
            error: e.message,
          };
        }
      }
      familyStats = result;
      if (!activeFamilyTab || !result[activeFamilyTab]) {
        activeFamilyTab = visibleFamilies[0]?.family_id || "";
      }
    })();
    familyStatsLoadPromise = loadPromise;
    try {
      return await loadPromise;
    } finally {
      if (familyStatsLoadPromise === loadPromise) familyStatsLoadPromise = null;
    }
  }

  function shouldAutoPollPanel(panel = currentPanel) {
    return shouldPanelAutoPoll(panel, overview, jobs);
  }

  function pollDelayForPanel(panel = currentPanel) {
    return pollDelayForPanelState(panel, overview);
  }

  async function refresh() {
    try {
      if (currentPanel !== "overview" && currentPanel !== "tasks") return;
      if (currentPanel === "overview") {
        const overviewLoaded = await loadOverviewData();
        if (!overviewLoaded) return;
        await loadFamilyStats();
        renderOverview({ skipAnimation: true });
      } else if (currentPanel === "tasks") {
        await loadJobs();
      }
    } catch (e) {
      console.error("Overview refresh failed:", e);
    }
  }

  function startPoll() {
    stopPoll();
    if (!shouldAutoPollPanel()) return;
    const panelAtStart = currentPanel;
    const tick = async () => {
      if (currentPanel !== panelAtStart || !shouldAutoPollPanel(panelAtStart)) return;
      await refresh();
      if (currentPanel !== panelAtStart || !shouldAutoPollPanel(panelAtStart)) return;
      pollTimer = setTimeout(tick, pollDelayForPanel(panelAtStart));
    };
    pollTimer = setTimeout(tick, pollDelayForPanel(panelAtStart));
  }

  function stopPoll() {
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  }

  function closeOverviewStream() {
    const stream = overviewEventSource;
    if (!stream) return;
    // Clear the reference before close() so a synchronous error callback from
    // a browser implementation cannot schedule a reconnect for an old stream.
    overviewEventSource = null;
    overviewStreamRevision += 1;
    try {
      stream.close();
    } catch (error) {
      console.error("Overview SSE close failed:", error);
    }
  }

  function openOverviewStream() {
    closeOverviewStream();
    const latestJob = overview?.latest_job || null;
    if (!latestJob || !isActive(latestJob.status) || typeof EventSource !== "function") return;
    const runtimeConfig = resolveBrowserBackendConfig();
    const requestBase = String(runtimeConfig.baseUrl || API.base || "").trim();
    const apiToken = String(runtimeConfig.apiToken || API.apiToken || "").trim();
    const url = `${requestBase}/api/overview/stream${apiToken ? `?token=${encodeURIComponent(apiToken)}` : ""}`;
    let es;
    try {
      es = new EventSource(url);
    } catch (error) {
      console.error("Overview SSE connection failed:", error);
      if (currentPanel === "overview" && overview?.latest_job && isActive(overview.latest_job.status)) {
        startPoll();
      }
      return;
    }
    overviewStreamRevision += 1;
    overviewEventSource = es;
    es.onmessage = (event) => {
      if (es !== overviewEventSource) return;
      try {
        const frame = JSON.parse(event.data);
        const nextOverview = frame.overview ? normalizeOverviewResource(frame.overview) : overview;
        const frameJobId = text(frame.job_id).trim();
        const latestJobId = text(nextOverview?.latest_job?.job_id).trim();
        if (frameJobId && latestJobId && frameJobId !== latestJobId) return;
        overviewRequestSeq += 1;
        overviewStreamRevision += 1;
        overview = nextOverview;
        currentEvents = normalizeJobEventList(frame.events);
        if (currentPanel === "overview") renderOverview({ skipAnimation: true });
        // A normal terminal frame is the last useful frame. Close before the
        // browser's EventSource auto-reconnect path can create a request loop.
        const nextLatestJob = overview?.latest_job || null;
        if (!nextLatestJob || !isActive(nextLatestJob.status)) {
          closeOverviewStream();
          stopPoll();
          loadFamilyStats()
            .then(() => { if (currentPanel === "overview") renderOverview({ skipAnimation: true }); })
            .catch((error) => console.error("Family stats load failed:", error));
        }
      } catch (e) {
        console.error("SSE frame parse error:", e);
        const active = Boolean(overview?.latest_job && isActive(overview.latest_job.status));
        closeOverviewStream();
        if (active && currentPanel === "overview") startPoll();
      }
    };
    es.onerror = () => {
      if (es !== overviewEventSource) return;
      // Do not leave EventSource's implicit reconnect loop running after a
      // transport error. The regular overview poll is the fallback while the
      // current job remains active.
      const active = Boolean(overview?.latest_job && isActive(overview.latest_job.status));
      closeOverviewStream();
      if (active && currentPanel === "overview") startPoll();
    };
  }

  /* ── Navigation ── */
  window.switchPanel = function (panel) {
    const navigationSeq = ++panelNavigationSeq;
    return (async () => {
      currentPanel = panel;
      stopPoll();
      closeOverviewStream();
      render();
      if ((panel === "records" || panel === "reviews" || panel === "settings") && (!Array.isArray(catalog.visible_families) || catalog.visible_families.length === 0)) {
        await loadCatalog();
        if (navigationSeq !== panelNavigationSeq) return;
      }
      if ((panel === "records" || panel === "settings" || panel === "overview") && !Object.keys(settings.basic || {}).length) {
        await loadBasicSettings();
        if (navigationSeq !== panelNavigationSeq) return;
      }
      if (panel === "overview") {
        await loadOverviewData();
        if (navigationSeq !== panelNavigationSeq) return;
        await loadFamilyStats();
        if (navigationSeq !== panelNavigationSeq) return;
        openOverviewStream();
      }
      render();
      if (panel === "tasks") {
        await loadJobs();
        if (navigationSeq !== panelNavigationSeq) return;
      }
      if (panel === "export-history") {
        await loadExportHistory();
        if (navigationSeq !== panelNavigationSeq) return;
      }
      if (panel === "mappings") loadMappings();
      if (panel === "reviews") loadReviewProblems();
      if (panel === "tasks" && shouldAutoPollPanel(panel)) startPoll();
    })();
  };

  /* ── Init ── */
  const VALID_PANELS = ["overview", "tasks", "records", "reviews", "export-history", "mappings", "settings"];

  function init() {
    document.addEventListener("DOMContentLoaded", () => {
      (async () => {
        const pathPanel = window.location.pathname.replace(/^\/+|\/+$/g, "");
        if (VALID_PANELS.includes(pathPanel)) {
          await switchPanel(pathPanel);
          return;
        }
        await Promise.all([
          loadCatalog(),
          loadBasicSettings(),
          loadOverviewData(),
        ]);
        await loadFamilyStats();
        render();
        openOverviewStream();
      })();
    });
  }

  init();
})();
