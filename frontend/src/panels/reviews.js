import { REVIEW_PROBLEM_KINDS, REVIEW_PROBLEM_LABELS } from "../contracts/reviewProblems.js";

function asText(value) {
  return String(value ?? "").trim();
}

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function evidenceValue(evidence, key) {
  const value = asObject(evidence)[key];
  if (Array.isArray(value)) return value.map((item) => asText(typeof item === "object" ? item.label || item.name || item.field || "" : item)).filter(Boolean).join("、");
  return asText(value);
}

function metricCard(label, value) {
  return `<div class="stat-card">
    <div class="stat-label">${label}</div>
    <div class="stat-value" style="font-size:20px">${value}</div>
  </div>`;
}

function paginationView(data, filters) {
  const totalCount = Number.parseInt(data.total_count ?? data.summary?.total_count ?? 0, 10) || 0;
  const returnedCount = Number.parseInt(data.returned_count ?? 0, 10) || 0;
  const page = Math.max(1, Number.parseInt(filters.page, 10) || 1);
  const pageSize = Math.max(1, Number.parseInt(filters.page_size, 10) || returnedCount || 50);
  const pageCount = Math.max(1, Math.ceil(totalCount / pageSize));
  if (totalCount <= pageSize && page <= 1 && !data.truncated) return "";
  return `<section class="card review-pagination">
    <span>第 ${page} 页 / 共 ${pageCount} 页</span>
    <span>共 ${totalCount} 条，当前 ${returnedCount} 条</span>
    <button class="btn btn-sm" id="review-prev-page" type="button" ${page <= 1 ? "disabled" : ""}>上一页</button>
    <button class="btn btn-sm" id="review-next-page" type="button" ${page >= pageCount ? "disabled" : ""}>下一页</button>
  </section>`;
}

function field(label, value, { escapeHtml, display }) {
  return `<div class="review-field">
    <div class="review-field-label">${escapeHtml(label)}</div>
    <div class="review-field-value">${escapeHtml(display(value))}</div>
  </div>`;
}

function artifactDisplay(row) {
  const verdict = asObject(row.evidence_verdict);
  const verdictStatus = asText(verdict.status);
  const verdictPath = asText(verdict.inspection_openable_path);
  if (["verified", "present_unverified", "shared_official_page"].includes(verdictStatus) && verdictPath) {
    return verdictPath.split(/[\\/]+/).filter(Boolean).at(-1) || verdictPath;
  }
  if (verdictStatus && !["verified", "present_unverified", "shared_official_page"].includes(verdictStatus)) {
    return `文件未定位：${artifactReasonLabel(row.artifact_missing_reason || verdict.reason_code)}`;
  }
  if (asText(row.artifact_status) === "unresolved" || asText(row.artifact_missing_reason)) {
    if (asText(row.problem_kind) === "source_artifact_unavailable") {
      const reason = asText(row.reason_code).startsWith("source_artifact_") ? row.reason_code : row.artifact_missing_reason;
      return `原网页不可用：${artifactReasonLabel(reason)}`;
    }
    return `文件未定位：${artifactReasonLabel(row.artifact_missing_reason)}`;
  }
  return row.archive_path || row.source_file || row.artifact_missing_reason || "";
}

function artifactReasonLabel(reason) {
  switch (asText(reason)) {
    case "source_artifact_invalid":
      return "不是完整原网页";
    case "source_artifact_missing":
      return "原网页文件缺失";
    case "artifact_path_unresolved":
      return "原路径不可访问";
    case "artifact_provenance_unresolved":
      return "来源信息不足";
    case "artifact_path_missing":
      return "未记录来源路径";
    default:
      return "缺少可打开的本地文件";
  }
}

function renderEvidence(row, helpers) {
  const { escapeHtml, display } = helpers;
  const evidence = asObject(row.evidence);
  const entries = [
    ["缺失字段", evidenceValue(evidence, "missing_fields")],
    ["记录标记业务大类", evidenceValue(evidence, "payload_record_family")],
    ["当前处理业务大类", evidenceValue(evidence, "context_record_family")],
    ["投资方明细识别结果", evidenceValue(evidence, "investor_detail_result")],
  ].filter(([, value]) => asText(value));
  if (!entries.length) return "";
  return `<div class="review-section">
    <div class="review-section-title">系统证据</div>
    <div class="review-field-grid">${entries.map(([label, value]) => field(label, value, { escapeHtml, display })).join("")}</div>
  </div>`;
}

function renderRow(row, helpers) {
  const { escapeHtml, display, formatJobTime } = helpers;
  const badgeLabel = asText(row.problem_kind) === "source_artifact_unavailable"
    ? row.problem_label
    : (row.status_label || "待人工复核");
  return `<article class="card review-card">
    <div class="review-card-head">
      <div>
        <div class="review-kind">${escapeHtml(row.problem_label)}</div>
        <h2 class="review-title">${escapeHtml(display(row.project_code || row.project_name || "未命名记录"))}</h2>
        <div class="review-subtitle">${escapeHtml(display(row.project_name))}</div>
      </div>
      <span class="badge pending"><span class="badge-dot"></span>${escapeHtml(display(badgeLabel))}</span>
    </div>
    <div class="review-field-grid">
      ${field("项目编号", row.project_code, { escapeHtml, display })}
      ${field("项目名称", row.project_name, { escapeHtml, display })}
      ${field("业务大类", row.record_family_label, { escapeHtml, display })}
      ${field("业务类型", row.business_label, { escapeHtml, display })}
      ${field("交易所", row.exchange_label || row.exchange_code, { escapeHtml, display })}
      ${field("原网页", artifactDisplay(row), { escapeHtml, display })}
      ${field("最近更新", row.updated_at ? formatJobTime(row.updated_at) : "", { escapeHtml, display })}
    </div>
    <div class="review-section">
      <div class="review-field-grid">
        ${field("原因", row.business_explanation, { escapeHtml, display })}
        ${field("影响", row.business_impact, { escapeHtml, display })}
        ${field("处理方向", row.suggested_review, { escapeHtml, display })}
      </div>
    </div>
    ${renderEvidence(row, helpers)}
  </article>`;
}

export function createReviewsPanel({
  $,
  escapeHtml,
  display,
  formatJobTime,
  getReviewProblems,
  getLoading,
  getError,
  getFilters,
  onFilterChange,
}) {
  function render() {
    const el = $("#panel-reviews");
    const data = asObject(getReviewProblems?.());
    const rows = Array.isArray(data.rows) ? data.rows : [];
    const summary = asObject(data.summary);
    const loading = Boolean(getLoading?.());
    const error = asText(getError?.());
    const filters = asObject(getFilters?.());
    el.innerHTML = `
      <div class="animate-in"><h1 class="page-title">待复核问题</h1></div>
      <section class="animate-in delay-1" style="margin-bottom:var(--space-4)">
        <div class="stats-grid" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr))">
          ${metricCard("全部", summary.total_count || data.total_count || 0)}
          ${REVIEW_PROBLEM_KINDS.map((kind) => metricCard(REVIEW_PROBLEM_LABELS[kind], summary[`${kind}_count`] || 0)).join("")}
        </div>
      </section>
      <section class="card review-filters animate-in delay-2">
        <select id="review-filter-kind">
          <option value="all">全部问题</option>
          ${REVIEW_PROBLEM_KINDS.map((kind) => `<option value="${kind}" ${filters.problem_kind === kind ? "selected" : ""}>${REVIEW_PROBLEM_LABELS[kind]}</option>`).join("")}
        </select>
        <select id="review-filter-state">
          <option value="all" ${filters.state === "all" ? "selected" : ""}>全部状态</option>
          <option value="pending_review" ${filters.state === "pending_review" ? "selected" : ""}>待人工复核</option>
          <option value="field_missing" ${filters.state === "field_missing" ? "selected" : ""}>字段缺失</option>
        </select>
        <input id="review-filter-keyword" type="search" value="${escapeHtml(filters.keyword || "")}" placeholder="编号、名称或来源文件">
      </section>
      ${loading ? `<section class="card review-state">正在加载待复核问题…</section>` : ""}
      ${!loading && error ? `<section class="card review-state error">待复核问题加载失败，请稍后重试或查看运行日志。</section>` : ""}
      ${!loading && !error && rows.length === 0 ? `<section class="card review-state">当前无待人工复核问题。</section>` : ""}
      ${!loading && !error && rows.length ? `<div class="review-list">${rows.map((row) => renderRow(row, { escapeHtml, display, formatJobTime })).join("")}</div>` : ""}
      ${!loading && !error ? paginationView(data, filters) : ""}
    `;
    $("#review-filter-kind")?.addEventListener("change", (event) => onFilterChange?.("problem_kind", event.target.value));
    $("#review-filter-state")?.addEventListener("change", (event) => onFilterChange?.("state", event.target.value));
    $("#review-filter-keyword")?.addEventListener("change", (event) => onFilterChange?.("keyword", event.target.value));
    $("#review-prev-page")?.addEventListener("click", () => onFilterChange?.("page", Math.max(1, (Number.parseInt(filters.page, 10) || 1) - 1)));
    $("#review-next-page")?.addEventListener("click", () => onFilterChange?.("page", (Number.parseInt(filters.page, 10) || 1) + 1));
  }
  return { render };
}
