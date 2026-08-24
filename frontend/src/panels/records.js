import {
  listSurfaceBusinesses,
  listSurfaceSourceOptions,
  normalizeCatalogResource,
  resolveActionableDefaultScope,
} from "../contracts/catalog.js";
import { businessTypeLabel, FAMILY_EMPTY_STATE_MESSAGES, recordFamilyLabel, recordStateLabel } from "../constants/index.js";

function asText(value, fallback = "") {
  const text = String(value ?? "").trim();
  return text || fallback;
}

function selectedBusinessId(filters = {}) {
  return asText(filters.business_id, "all");
}

function getRecordBusinessOptions({ getCatalog, recordFamily }) {
  if (!recordFamily) return [];
  const catalog = typeof getCatalog === "function" ? getCatalog() : null;
  const businesses = listSurfaceBusinesses(catalog || {}, { record_family: recordFamily, surface: "records" })
    .filter((item) => item.supported);
  const localized = businesses.map((item) => ({
    ...item,
      business_label: businessTypeLabel(item.business_id, item.business_label || item.business_id),
  }));
  return [{ business_id: "all", business_label: "全部业务" }, ...localized];
}

function getRecordExchangeOptions({
  getCatalog,
  recordFamily,
  businessId,
}) {
  if (!recordFamily) return [{ source_id: "all", source_label: "全部交易所" }];
  const catalog = typeof getCatalog === "function" ? getCatalog() : null;
  return listSurfaceSourceOptions(catalog || {}, {
    record_family: recordFamily,
    business_id: businessId || "all",
    surface: "records",
    all_business_source_mode: "union",
    include_all: true,
    all_label: "全部交易所",
  });
}

function resolveSelectedExchange(options = [], candidate = "") {
  const normalizedCandidate = asText(candidate, "all");
  const optionValues = new Set(options.map((option) => asText(option.source_id)).filter(Boolean));
  if (optionValues.has(normalizedCandidate)) {
    return normalizedCandidate;
  }
  if (optionValues.has("all")) {
    return "all";
  }
  return asText(options[0]?.source_id);
}

function artifactMissingLabel(reason = "") {
  switch (asText(reason)) {
    case "artifact_path_unresolved":
    case "authoritative_artifact_missing":
      return "文件路径不可访问";
    case "artifact_provenance_unresolved":
      return "来源文件关联不足";
    case "artifact_path_missing":
    case "artifact_path_undeclared":
      return "未记录来源文件";
    default:
      return "文件未定位";
  }
}

const INSPECTION_ONLY_EVIDENCE_STATUSES = new Set(["present_unverified", "stale_reference", "invalid_shell", "identity_mismatch"]);
const REPROCESSABLE_RECORD_STATES = new Set([
  "field_missing",
  "pending_review",
  "pending_mapping",
  "mapping_conflict",
  "conflict",
  "parse_failed",
  "postprocess_failed",
]);

export function isRecordReprocessable(row = {}) {
  return REPROCESSABLE_RECORD_STATES.has(asText(row.state));
}

function recordEvidenceVerdict(row = {}) {
  const verdict = row && typeof row.evidence_verdict === "object" && row.evidence_verdict !== null
    ? row.evidence_verdict
    : null;
  if (!verdict) return null;
  return {
    status: asText(verdict.status),
    openablePath: asText(verdict.inspection_openable_path),
    reasonCode: asText(verdict.reason_code),
    safeEvidence: verdict.safe_evidence && typeof verdict.safe_evidence === "object" ? verdict.safe_evidence : {},
  };
}

function rowHasNormalOpenAction(row = {}) {
  const verdict = recordEvidenceVerdict(row);
  if (!verdict?.openablePath) return false;
  if (verdict.status === "verified") return true;
  if (verdict.status !== "shared_official_page") return false;
  return asText(verdict.safeEvidence?.page_kind) === "shared_official_page";
}

function rowHasInspectionOnlyEvidence(row = {}) {
  const verdict = recordEvidenceVerdict(row);
  return Boolean(verdict?.openablePath && INSPECTION_ONLY_EVIDENCE_STATUSES.has(verdict.status));
}

function rowArtifactUnavailableReason(row = {}) {
  const verdict = recordEvidenceVerdict(row);
  return verdict?.reasonCode || row.artifact_missing_reason;
}

function evidenceStatusLabel(status = "") {
  switch (asText(status)) {
    case "verified":
      return "已验证";
    case "shared_official_page":
      return "共享官网页";
    case "present_unverified":
      return "未验证";
    case "stale_reference":
      return "引用失效";
    case "invalid_shell":
      return "无效壳页面";
    case "identity_mismatch":
      return "身份不匹配";
    case "undeclared":
      return "未声明";
    case "missing":
      return "缺失";
    default:
      return asText(status, "未知");
  }
}

function canonicalReadyLabel(row = {}) {
  return row.canonical_ready ? "已就绪" : "未就绪";
}

function exportEligibilityLabel(row = {}) {
  return row.export_eligible ? "可导出" : "受限";
}

export function createRecordsPanel({
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
  onReprocess,
  getCatalog,
  getDefaultScopeRuntime,
  getOverview,
  setOverview,
  getRecords,
  setRecords,
  getRecordFilters,
  setRecordFilters,
  getRecordPage,
  setRecordPage,
  getRecordsBrowseRuntime,
}) {
  const browsableStateOrder = [
    "ready",
    "field_missing",
    "pending_review",
    "pending_mapping",
    "mapping_conflict",
    "skipped",
    "conflict",
    "parse_failed",
    "postprocess_failed",
  ];
  let recordsLoadErrorMessage = "";
  let recordActionErrorMessage = "";
  let reprocessingRecordId = "";

  function emptyRecordsResource(extra = {}) {
    return {
      rows: [],
      summary: {
        filtered_state_counts: {},
        total_count: 0,
        visible_count: 0,
        page_count: 1,
      },
      display_columns: [],
      ...extra,
    };
  }

  function renderBlockedLayout(runtime = {}) {
    const el = $("#panel-records");
    const message = asText(runtime.message || runtime.reason || "记录范围不可用，请稍后重试。");
    el.innerHTML = `
      <div class="animate-in"><h1 class="page-title">记录</h1></div>
      <section class="card animate-in delay-1">
        <div class="alert alert-warning">${escapeHtml(message)}</div>
      </section>
    `;
  }

  function renderLayout() {
    const el = $("#panel-records");
    const runtime = typeof getRecordsBrowseRuntime === "function" ? getRecordsBrowseRuntime() : { state: "ready" };
    if (asText(runtime.state) && asText(runtime.state) !== "ready") {
      renderBlockedLayout(runtime);
      return;
    }
    const filters = getRecordFilters();
    const recordScope = buildRecordsScope();
    const catalog = typeof getCatalog === "function" ? getCatalog() : null;
    const normalizedCatalog = normalizeCatalogResource(catalog || {});
    const visibleFamilies = normalizedCatalog.visible_families || [];
    if (visibleFamilies.length <= 0) {
      renderBlockedLayout({
        message: "记录目录不可用，无法确定可浏览的业务范围。",
      });
      return;
    }
    const catalogDefaultFamilyId = asText(visibleFamilies[0]?.family_id);
    const visibleFamilyIds = new Set(visibleFamilies.map((item) => asText(item.family_id)).filter(Boolean));
    const requestedFamily = asText(filters.record_family || recordScope?.record_family);
    const recordFamily = visibleFamilyIds.has(requestedFamily) ? requestedFamily : catalogDefaultFamilyId;
    const recordFamilyDisplay = recordFamilyLabel(recordFamily, "业务");
    const businessOptions = getRecordBusinessOptions({
      getCatalog,
      recordFamily,
    });
    const businessOptionIds = new Set(businessOptions.map((item) => asText(item.business_id)).filter(Boolean));
    const initialBusinessId = selectedBusinessId(filters);
    const selectedBusiness = businessOptionIds.has(initialBusinessId) ? initialBusinessId : "all";
    const exchangeOptions = getRecordExchangeOptions({
      getCatalog,
      recordFamily,
      businessId: selectedBusiness,
    });
    const selectedExchange = resolveSelectedExchange(exchangeOptions, filters.exchange);

    el.innerHTML = `
      <div class="animate-in"><h1 class="page-title">记录</h1></div>

      <section class="card animate-in delay-1" style="margin-bottom:var(--space-4)">
        <div class="form-row">
          <div class="form-group">
            <label>业务类别</label>
            <select id="record-family">
              ${visibleFamilies.map((family) => `<option value="${escapeHtml(family.family_id)}">${escapeHtml(recordFamilyLabel(family.family_id, family.family_label))}</option>`).join("")}
            </select>
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>状态</label>
            <select id="filter-state">
              <option value="all">全部</option>
              ${browsableStateOrder.map((state) => `<option value="${escapeHtml(state)}">${escapeHtml(recordStateLabel(state))}</option>`).join("")}
            </select>
          </div>
          <div class="form-group">
            <label>${escapeHtml(`${recordFamilyDisplay}类型`)}</label>
            <select id="filter-business">
              ${businessOptions.map((option) => `<option value="${escapeHtml(option.business_id)}">${escapeHtml(option.business_label)}</option>`).join("")}
            </select>
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>交易所</label>
            <select id="filter-exchange">
              ${exchangeOptions.map((option) => `<option value="${escapeHtml(option.source_id)}">${escapeHtml(option.source_label)}</option>`).join("")}
            </select>
          </div>
          <div class="form-group">
            <label>关键词</label>
            <input type="text" id="filter-keyword" placeholder="项目编号或名称">
          </div>
        </div>
        <div class="form-row">
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

      <section class="animate-in delay-2" style="margin-bottom:var(--space-4)">
        <div class="stats-grid" style="grid-template-columns:repeat(9,1fr)">
          ${browsableStateOrder.map((key) =>
            `<div class="stat-card" id="stat-${key}">
              <div class="stat-label">${escapeHtml(recordStateLabel(key))}</div>
              <div class="stat-value" style="font-size:20px" id="statval-${key}">—</div>
            </div>`
          ).join("")}
        </div>
      </section>

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

    function applyRecordFilters() {
      setRecordFilters({
        record_family: $("#record-family")?.value || "listing",
        state: $("#filter-state")?.value || "all",
        business_id: $("#filter-business")?.value || "all",
        exchange: $("#filter-exchange")?.value || "all",
        keyword: $("#filter-keyword")?.value.trim() || "",
        date_from: $("#filter-date-from")?.value || "",
        date_to: $("#filter-date-to")?.value || "",
      });
      setRecordPage(1);
      load();
    }

    const familySelect = $("#record-family");
    if (familySelect) familySelect.value = recordFamily || "listing";

    const normalizedFilters = {
      ...filters,
      record_family: recordFamily,
      business_id: selectedBusiness,
      exchange: selectedExchange,
    };
    if (
      normalizedFilters.record_family !== filters.record_family
      || normalizedFilters.business_id !== filters.business_id
      || normalizedFilters.exchange !== filters.exchange
    ) {
      setRecordFilters(normalizedFilters);
    }
    $("#filter-state").value = normalizedFilters.state;
    $("#filter-business").value = normalizedFilters.business_id;
    $("#filter-exchange").value = normalizedFilters.exchange;
    $("#filter-keyword").value = filters.keyword || "";
    $("#filter-date-from").value = filters.date_from || "";
    $("#filter-date-to").value = filters.date_to || "";

    function applyExchangeOptions({ familyId, businessId, preferredExchange = "" }) {
      const exchangeSelect = $("#filter-exchange");
      if (!exchangeSelect) return "all";
      const options = getRecordExchangeOptions({
        getCatalog,
        recordFamily: familyId,
        businessId,
      });
      const selected = resolveSelectedExchange(options, preferredExchange);
      exchangeSelect.innerHTML = options
        .map((option) => `<option value="${escapeHtml(option.source_id)}">${escapeHtml(option.source_label)}</option>`)
        .join("");
      exchangeSelect.value = selected;
      return selected;
    }

    familySelect?.addEventListener("change", () => {
      const newFamily = familySelect.value;
      const newBusinessOptions = getRecordBusinessOptions({ getCatalog, recordFamily: newFamily });
      const businessSelect = $("#filter-business");
      let nextBusiness = "all";
      const previousBusiness = selectedBusinessId(getRecordFilters());
      if (businessSelect) {
        businessSelect.innerHTML = newBusinessOptions.map((option) =>
          `<option value="${escapeHtml(option.business_id)}">${escapeHtml(option.business_label)}</option>`
        ).join("");
        const hasPreviousBusiness = newBusinessOptions.some((option) => option.business_id === previousBusiness);
        nextBusiness = hasPreviousBusiness ? previousBusiness : "all";
        businessSelect.value = nextBusiness;
      }
      const nextExchange = applyExchangeOptions({
        familyId: newFamily,
        businessId: nextBusiness,
        preferredExchange: $("#filter-exchange")?.value || getRecordFilters().exchange,
      });
      const familyDisplayLabel = recordFamilyLabel(newFamily, "业务");
      const businessLabelEl = document.querySelector("#filter-business")?.closest(".form-group")?.querySelector("label");
      if (businessLabelEl) businessLabelEl.textContent = familyDisplayLabel + "类型";
      setRecordFilters({
        ...getRecordFilters(),
        record_family: newFamily,
        business_id: nextBusiness,
        exchange: nextExchange,
      });
      setRecordPage(1);
      load();
    });

    $("#btn-records-search").addEventListener("click", applyRecordFilters);
    $("#filter-state").addEventListener("change", applyRecordFilters);
    $("#filter-business").addEventListener("change", () => {
      const familyId = $("#record-family")?.value || getRecordFilters().record_family || recordFamily;
      const businessId = $("#filter-business")?.value || "all";
      applyExchangeOptions({
        familyId,
        businessId,
        preferredExchange: $("#filter-exchange")?.value || getRecordFilters().exchange,
      });
      applyRecordFilters();
    });
    $("#filter-exchange").addEventListener("change", applyRecordFilters);
    $("#filter-keyword").addEventListener("keydown", (event) => { if (event.key === "Enter") applyRecordFilters(); });
    $("#filter-date-from").addEventListener("keydown", (event) => { if (event.key === "Enter") applyRecordFilters(); });
    $("#filter-date-to").addEventListener("keydown", (event) => { if (event.key === "Enter") applyRecordFilters(); });
    $("#btn-records-export").addEventListener("click", () => {
      const scope = buildRecordsScope();
      const actionable = resolveActionableDefaultScope(
        typeof getCatalog === "function" ? getCatalog() : {},
        scope || {},
        { surface: "export" },
      );
      if (!actionable) {
        handleExport(new Error("当前筛选范围不支持导出，请选择该业务可用的交易所后重试。"));
        return;
      }
      handleExport();
    });
    $("#btn-records-prev").addEventListener("click", () => {
      if (getRecordPage() > 1) {
        setRecordPage(getRecordPage() - 1);
        load();
      }
    });
    $("#btn-records-next").addEventListener("click", () => {
      setRecordPage(getRecordPage() + 1);
      load();
    });
  }

  function updateTable() {
    const records = getRecords() || { rows: [], summary: {}, display_columns: [] };
    const overview = getOverview() || {};
    const filters = getRecordFilters();
    const recordPage = getRecordPage();
    const loadErrorMessage = asText(recordsLoadErrorMessage || records.load_error || records.error_message);
    const summary = loadErrorMessage ? emptyRecordsResource().summary : (records.summary || {});
    const totalCount = num(summary.total_count);
    const visibleCount = num(summary.visible_count);
    const pageCount = num(summary.page_count);
    const hasMore = !loadErrorMessage && recordPage < pageCount;
    const rows = loadErrorMessage ? [] : (records.rows || []);
    const columns = loadErrorMessage ? [] : (records.display_columns || []);

    const thead = $("#records-thead");
    const allHeaders = ["状态", "规范就绪", "证据状态", "导出资格", "文件", ...columns, "状态说明", "操作", "最近更新"];
    thead.innerHTML = `<tr>${allHeaders.map((header) => `<th>${escapeHtml(header)}</th>`).join("")}</tr>`;

    const familySelectEl = $("#record-family");
    if (familySelectEl) familySelectEl.value = filters.record_family || "listing";
    const familyDisplayForLabel = recordFamilyLabel(filters.record_family || "listing", "业务");
    const businessLabelForUpdate = document.querySelector("#filter-business")?.closest(".form-group")?.querySelector("label");
    if (businessLabelForUpdate) businessLabelForUpdate.textContent = familyDisplayForLabel + "类型";
    $("#filter-state").value = filters.state;
    $("#filter-business").value = selectedBusinessId(filters);
    $("#filter-exchange").value = filters.exchange;
    $("#filter-keyword").value = filters.keyword || "";
    $("#filter-date-from").value = filters.date_from || "";
    $("#filter-date-to").value = filters.date_to || "";

    const filteredCounts = (records.summary || {}).filtered_state_counts || {};
    browsableStateOrder.forEach((key) => {
      const stat = document.getElementById(`statval-${key}`);
      if (stat) stat.textContent = num(filteredCounts[key]);
    });

    const tbody = $("#records-tbody");
    const actionErrorRow = recordActionErrorMessage
      ? `<tr class="records-action-error-row"><td colspan="${allHeaders.length}"><div id="record-action-error" class="alert alert-danger" role="alert">${escapeHtml(recordActionErrorMessage)}</div></td></tr>`
      : "";
    if (rows.length === 0) {
      const currentFamily = asText(filters.record_family || "listing");
      const emptyMessage = FAMILY_EMPTY_STATE_MESSAGES[currentFamily] || "暂无记录。";
      const message = loadErrorMessage || emptyMessage;
      const messageClass = loadErrorMessage ? "alert alert-danger" : "";
      const messageStyle = loadErrorMessage
        ? ""
        : "padding:32px 0;text-align:center;color:var(--text-faint)";
      tbody.innerHTML = `${actionErrorRow}<tr><td colspan="${allHeaders.length}" class="${messageClass}" style="${messageStyle}">${escapeHtml(message)}</td></tr>`;
    } else {
      tbody.innerHTML = actionErrorRow + rows.map((row) => {
        const badgeClass = row.state === "ready" ? "ready"
          : row.state === "pending_mapping" || row.state === "mapping_conflict" || row.state === "pending_review" || row.state === "conflict" ? "pending"
          : row.state === "skipped" ? "skipped" : "failed";
        const canAcknowledgeFieldMissing = row.state === "field_missing"
          && row.attention?.requires_attention
          && !row.field_missing_acknowledgement?.acknowledged;
        const canReprocess = isRecordReprocessable(row);
        const reprocessing = reprocessingRecordId === String(row.record_id || "");
        const hasNormalOpenAction = rowHasNormalOpenAction(row);
        const hasInspectionOnlyEvidence = rowHasInspectionOnlyEvidence(row);
        const unavailableReason = rowArtifactUnavailableReason(row);
        const fileCell = hasNormalOpenAction
          ? `<button class="btn btn-sm btn-record-folder" type="button" data-record-id="${escapeHtml(row.record_id)}">定位网页文件</button>${row.local_artifact_name ? `<div class="record-path-hint">${escapeHtml(row.local_artifact_name)}</div>` : ""}`
          : hasInspectionOnlyEvidence
            ? `<span class="record-artifact-inspection-only">仅可检查证据</span>${row.local_artifact_name ? `<div class="record-path-hint">${escapeHtml(row.local_artifact_name)}</div>` : ""}`
          : row.archive_path || row.source_file || unavailableReason
            ? `<span class="record-artifact-missing">${escapeHtml(artifactMissingLabel(unavailableReason))}</span>${(row.archive_path || row.source_file) ? `<div class="record-path-hint">${escapeHtml(row.archive_path || row.source_file)}</div>` : ""}`
            : "—";
        const cells = [
          `<td><span class="badge ${badgeClass}"><span class="badge-dot"></span>${escapeHtml(recordStateLabel(row.status_label || row.state))}</span></td>`,
          `<td>${escapeHtml(canonicalReadyLabel(row))}</td>`,
          `<td>${escapeHtml(evidenceStatusLabel(row.evidence_status || row.evidence_verdict?.status))}</td>`,
          `<td>${escapeHtml(exportEligibilityLabel(row))}</td>`,
          `<td class="record-file-cell">${fileCell}</td>`,
          ...columns.map((column) => `<td>${escapeHtml(display(recordCellValue(row, column)))}</td>`),
          `<td style="max-width:320px;white-space:normal">${escapeHtml(display(row.status_detail))}</td>`,
          `<td><div style="display:flex;gap:var(--space-2);flex-wrap:wrap">${canAcknowledgeFieldMissing
            ? `<button class="btn btn-sm btn-field-missing-ack" type="button" data-record-id="${escapeHtml(row.record_id)}">确认缺失提示</button>`
            : ""}${canReprocess
              ? `<button class="btn btn-sm btn-record-reprocess" type="button" data-record-id="${escapeHtml(row.record_id)}" ${reprocessingRecordId ? "disabled" : ""}>${reprocessing ? "处理中..." : "重新处理"}</button>`
              : ""}</div></td>`,
          `<td style="white-space:nowrap">${row.updated_at ? formatJobTime(row.updated_at) : "—"}</td>`,
        ];
        return `<tr>${cells.join("")}</tr>`;
      }).join("");
    }

    $$(".btn-record-folder", tbody).forEach((button) => {
      button.addEventListener("click", async () => {
        recordActionErrorMessage = "";
        try {
          await API.revealRecordFolder(button.dataset.recordId || "");
        } catch (error) {
          console.error("Reveal record folder failed:", error);
          recordActionErrorMessage = "打开记录文件失败，请稍后重试。";
          updateTable();
        }
      });
    });
    $$(".btn-field-missing-ack", tbody).forEach((button) => {
      button.addEventListener("click", async () => {
        recordActionErrorMessage = "";
        try {
          await API.acknowledgeRecordFieldMissing(button.dataset.recordId || "");
          await load();
        } catch (error) {
          console.error("Acknowledge field_missing failed:", error);
          recordActionErrorMessage = "确认缺失提示失败，请稍后重试。";
          updateTable();
        }
      });
    });
    $$(".btn-record-reprocess", tbody).forEach((button) => {
      button.addEventListener("click", async () => {
        const recordId = String(button.dataset.recordId || "").trim();
        if (!recordId || reprocessingRecordId || typeof onReprocess !== "function") return;
        recordActionErrorMessage = "";
        reprocessingRecordId = recordId;
        updateTable();
        try {
          const result = await onReprocess(recordId);
          if (result?.error_code || result?.error_message) {
            throw new Error(result.error_message || result.error_code);
          }
          await load();
        } catch (error) {
          console.error("Record reprocess failed:", error);
          recordActionErrorMessage = `重新处理记录失败：${error.message || "请稍后重试。"}`;
        } finally {
          reprocessingRecordId = "";
          updateTable();
        }
      });
    });

    $("#records-count").textContent = loadErrorMessage
      ? "加载失败，未显示旧记录"
      : `共 ${totalCount} 条${hasMore ? `，显示 ${visibleCount} 条` : ""}`;
    $("#records-page-info").textContent = `第 ${recordPage} / ${pageCount || 1} 页`;
    $("#btn-records-prev").disabled = Boolean(loadErrorMessage) || recordPage <= 1;
    $("#btn-records-next").disabled = Boolean(loadErrorMessage) || !hasMore;
  }

  async function load({ includeOverview = false } = {}) {
    try {
      const runtime = typeof getRecordsBrowseRuntime === "function" ? getRecordsBrowseRuntime() : { state: "ready" };
      if (includeOverview) {
        setOverview(await API.getOverview());
      }
      const scope = buildRecordsScope();
      if (asText(runtime.state) !== "ready" || !scope) {
        recordsLoadErrorMessage = "";
        recordActionErrorMessage = "";
        setRecords(emptyRecordsResource());
        return;
      }
      const nextRecords = await API.listRecords(scope);
      recordsLoadErrorMessage = "";
      recordActionErrorMessage = "";
      setRecords(nextRecords);
      updateTable();
    } catch (error) {
      console.error("Records load failed:", error);
      recordsLoadErrorMessage = "记录加载失败，请稍后重试。";
      recordActionErrorMessage = "";
      setRecords(emptyRecordsResource({ load_error: recordsLoadErrorMessage }));
      updateTable();
    }
  }

  function render() {
    renderLayout();
    const runtime = typeof getRecordsBrowseRuntime === "function" ? getRecordsBrowseRuntime() : { state: "ready" };
    if (asText(runtime.state) === "ready") {
      load({ includeOverview: true });
    }
  }

  return {
    render,
    load,
    renderLayout,
    updateTable,
  };
}
