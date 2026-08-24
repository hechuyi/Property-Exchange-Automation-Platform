function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function asText(value) {
  return String(value ?? "").trim();
}

function modeLabel(value) {
  const mode = asText(value);
  if (mode === "incremental") return "增量";
  if (mode === "full") return "全量";
  return mode || "—";
}

function retentionLabel(detail = {}) {
  const status = asText(detail.retention_status);
  if (status === "pruned_by_retention" || detail.pruned_by_retention) return "保留策略移除";
  if (status === "artifact_incomplete") return "文件不完整";
  if (status === "artifact_unavailable" || detail.is_tombstone) return "文件不可用";
  return "已保留";
}

function field(label, value, { escapeHtml, display }) {
  return `<div class="export-history-field">
    <div class="export-history-field-label">${escapeHtml(label)}</div>
    <div class="export-history-field-value">${escapeHtml(display(value))}</div>
  </div>`;
}

function jsonPreview(value, { escapeHtml }) {
  const source = asObject(value);
  if (!Object.keys(source).length) return "";
  return `<pre class="export-history-json">${escapeHtml(JSON.stringify(source, null, 2))}</pre>`;
}

function renderListRows(rows, selectedExportId, helpers) {
  const { escapeHtml, display, formatJobTime, num } = helpers;
  if (!rows.length) {
    return `<div class="export-history-state">暂无导出历史。</div>`;
  }
  return `<div class="export-history-list">
    ${rows.map((row) => {
      const selected = row.export_id === selectedExportId;
      const retention = retentionLabel(row);
      const badgeClass = row.openable ? "ready" : "skipped";
      return `<button class="export-history-row${selected ? " active" : ""}" type="button" data-export-id="${escapeHtml(row.export_id)}" id="export-history-row-${escapeHtml(row.export_id)}">
        <span class="export-history-row-main">
          <span class="export-history-row-title">${escapeHtml(display(row.export_id))}</span>
          <span class="export-history-row-meta">${escapeHtml(formatJobTime(row.created_at))} · ${escapeHtml(modeLabel(row.requested_export_mode))} · artifact ${num(row.artifact_count)}</span>
          <span class="export-history-row-meta">${escapeHtml(display(row.cursor_id))}</span>
        </span>
        <span class="badge ${badgeClass}"><span class="badge-dot"></span>${escapeHtml(retention)}</span>
      </button>`;
    }).join("")}
  </div>`;
}

function renderDetail(detail, helpers) {
  const { escapeHtml, display, num } = helpers;
  if (!detail || !detail.export_id) {
    return `<div class="export-history-state">选择左侧历史导出查看 manifest 摘要。</div>`;
  }
  const manifest = asObject(detail.manifest);
  const cursorValue = asObject(detail.cursor_value);
  const cursorBasis = asObject(manifest.cursor_basis);
  const scope = asObject(manifest.scope);
  const canOpen = Boolean(detail.openable);
  return `
    <div class="export-history-detail-head">
      <div>
        <div class="export-history-kind">${escapeHtml(modeLabel(detail.requested_export_mode || manifest.requested_export_mode))}</div>
        <h2 class="export-history-title">${escapeHtml(display(detail.export_id))}</h2>
        <div class="export-history-subtitle">${escapeHtml(retentionLabel(detail))} · 保留 ${num(detail.retention_count)} 次 · watermark ${num(detail.revision_watermark || manifest.revision_watermark)}</div>
      </div>
      <span class="badge ${canOpen ? "ready" : "skipped"}"><span class="badge-dot"></span>${canOpen ? "可打开" : "不可打开"}</span>
    </div>

    <div class="export-history-actions">
      <button class="btn btn-primary" id="btn-export-history-open" type="button" ${canOpen ? "" : "disabled"}>打开</button>
      <input id="export-history-download-dir" type="text" placeholder="下载到默认导出目录，或填写目标目录">
      <button class="btn" id="btn-export-history-download" type="button" ${canOpen ? "" : "disabled"}>下载</button>
    </div>

    <div class="export-history-field-grid">
      ${field("effective_export_mode", manifest.effective_export_mode || detail.requested_export_mode, { escapeHtml, display })}
      ${field("export_profile_id", manifest.export_profile_id, { escapeHtml, display })}
      ${field("canonical_scope_hash", manifest.canonical_scope_hash, { escapeHtml, display })}
      ${field("cursor_id", detail.cursor_id || manifest.cursor_id, { escapeHtml, display })}
      ${field("schema_version", manifest.schema_version, { escapeHtml, display })}
      ${field("header_version", manifest.header_version, { escapeHtml, display })}
      ${field("included_count", manifest.included_count, { escapeHtml, display })}
      ${field("excluded_count", manifest.excluded_count, { escapeHtml, display })}
      ${field("field_missing_blocked_records", manifest.field_missing_blocked_records, { escapeHtml, display })}
      ${field("artifact_checksum", manifest.artifact_checksum, { escapeHtml, display })}
      ${field("scope", [scope.record_family, scope.business_id, scope.exchange].filter(Boolean).join(" / "), { escapeHtml, display })}
      ${field("artifact", (detail.existing_artifacts || detail.artifacts || [])[0], { escapeHtml, display })}
      ${field("missing_artifacts", (detail.missing_artifacts || []).join(", "), { escapeHtml, display })}
    </div>

    <div class="export-history-section">
      <div class="export-history-section-title">cursor basis</div>
      <div class="export-history-field-grid">
        ${field("basis_export_id", cursorBasis.export_id, { escapeHtml, display })}
        ${field("eligible_set_hash", cursorBasis.eligible_set_hash, { escapeHtml, display })}
        ${field("last_successful_export_id", cursorValue.last_successful_export_id, { escapeHtml, display })}
        ${field("last_successful_revision_watermark", cursorValue.last_successful_revision_watermark, { escapeHtml, display })}
      </div>
      ${jsonPreview(cursorBasis, { escapeHtml })}
    </div>
  `;
}

function renderDetailFailure(error, helpers) {
  const { escapeHtml } = helpers;
  return `
    <div class="export-history-state error">详情加载失败：${escapeHtml(error)}</div>
    <div class="export-history-actions">
      <button class="btn btn-primary" id="btn-export-history-open" type="button" disabled>打开</button>
      <input id="export-history-download-dir" type="text" placeholder="下载到默认导出目录，或填写目标目录">
      <button class="btn" id="btn-export-history-download" type="button" disabled>下载</button>
    </div>
  `;
}

export function createExportHistoryPanel({
  $,
  $$ = (selector, ctx = globalThis.document) => ctx?.querySelectorAll ? [...ctx.querySelectorAll(selector)] : [],
  API,
  escapeHtml,
  display,
  formatJobTime,
  num,
  getHistory,
  setHistory,
  getDetail,
  setDetail,
  getLoading,
  setLoading,
  getError,
  setError,
  getMessage,
  setMessage,
  getSelectedExportId,
  setSelectedExportId,
}) {
  let detailError = "";
  let requestSeq = 0;

  function selectedExportId() {
    return asText(getSelectedExportId?.());
  }

  async function selectExport(exportId) {
    const normalizedId = asText(exportId);
    if (!normalizedId) return;
    const currentRequestSeq = ++requestSeq;
    setSelectedExportId?.(normalizedId);
    setLoading?.(false);
    setDetail?.(null);
    detailError = "";
    setError?.("");
    setMessage?.("");
    render();
    try {
      const detail = await API.getExportHistoryDetail(normalizedId);
      if (currentRequestSeq !== requestSeq) return;
      setDetail?.(detail);
    } catch (error) {
      if (currentRequestSeq !== requestSeq) return;
      detailError = error?.message || "导出历史详情加载失败";
      setDetail?.(null);
    }
    render();
  }

  async function load() {
    const currentRequestSeq = ++requestSeq;
    setLoading?.(true);
    setDetail?.(null);
    detailError = "";
    setError?.("");
    setMessage?.("");
    render();
    try {
      const history = await API.listExportHistory(100);
      if (currentRequestSeq !== requestSeq) return;
      const rows = Array.isArray(history.rows) ? history.rows : [];
      setHistory?.(history);
      const currentSelected = selectedExportId();
      const nextSelected = rows.some((row) => row.export_id === currentSelected)
        ? currentSelected
        : rows[0]?.export_id || "";
      if (nextSelected) {
        setSelectedExportId?.(nextSelected);
        try {
          const detail = await API.getExportHistoryDetail(nextSelected);
          if (currentRequestSeq !== requestSeq) return;
          setDetail?.(detail);
        } catch (error) {
          if (currentRequestSeq !== requestSeq) return;
          detailError = error?.message || "导出历史详情加载失败";
          setDetail?.(null);
        }
      } else {
        setSelectedExportId?.("");
        setDetail?.(null);
      }
    } catch (error) {
      if (currentRequestSeq !== requestSeq) return;
      setDetail?.(null);
      setError?.(error?.message || "导出历史加载失败");
    } finally {
      if (currentRequestSeq !== requestSeq) return;
      setLoading?.(false);
      render();
    }
  }

  async function openSelected() {
    const detail = getDetail?.();
    const exportId = selectedExportId() || asText(detail?.export_id);
    if (!detail || detail.export_id !== exportId || !detail.openable) return;
    if (!exportId) return;
    try {
      const result = await API.openExportHistory(exportId);
      setMessage?.(result.opened ? `已打开 ${display(result.path || exportId)}` : "该导出历史不可打开。");
    } catch (error) {
      setMessage?.(`失败: 打开导出失败: ${error.message}`);
    }
    render();
  }

  async function downloadSelected() {
    const detail = getDetail?.();
    const exportId = selectedExportId() || asText(detail?.export_id);
    if (!detail || detail.export_id !== exportId || !detail.openable) return;
    if (!exportId) return;
    const outputDir = asText($("#export-history-download-dir")?.value);
    try {
      const result = await API.downloadExportHistory(exportId, outputDir);
      setMessage?.(result.downloaded ? `已下载 ${num(result.artifacts?.length)} 个文件。` : "该导出历史不可下载。");
    } catch (error) {
      setMessage?.(`失败: 下载导出失败: ${error.message}`);
    }
    render();
  }

  function render() {
    const el = $("#panel-export-history");
    const history = asObject(getHistory?.());
    const rows = Array.isArray(history.rows) ? history.rows : [];
    const detail = getDetail?.();
    const loading = Boolean(getLoading?.());
    const error = asText(getError?.());
    const currentDetailError = asText(detailError);
    const message = asText(getMessage?.());
    const selectedId = selectedExportId();

    el.innerHTML = `
      <div class="animate-in"><h1 class="page-title">导出历史</h1></div>
      ${message ? `<div class="alert ${message.startsWith("失败") ? "alert-danger" : "alert-success"} animate-in delay-1">${escapeHtml(message)}</div>` : ""}
      <div class="export-history-layout animate-in delay-1">
        <section class="card export-history-list-card">
          <div class="jobs-header">
            <span class="jobs-header-title">历史导出</span>
            <button class="btn btn-sm" id="btn-export-history-refresh" type="button">刷新</button>
          </div>
          ${loading ? `<div class="export-history-state">正在加载导出历史…</div>` : ""}
          ${!loading && error && !currentDetailError ? `<div class="export-history-state error">导出历史加载失败：${escapeHtml(error)}</div>` : ""}
          ${!loading && (!error || currentDetailError) ? renderListRows(rows, selectedId, { escapeHtml, display, formatJobTime, num }) : ""}
        </section>
        <section class="card export-history-detail-card">
          ${currentDetailError ? renderDetailFailure(currentDetailError, { escapeHtml }) : renderDetail(detail, { escapeHtml, display, num })}
        </section>
      </div>
    `;

    $("#btn-export-history-refresh")?.addEventListener("click", load);
    $("#btn-export-history-open")?.addEventListener("click", openSelected);
    $("#btn-export-history-download")?.addEventListener("click", downloadSelected);
    $$(".export-history-row", el).forEach((button) => {
      button.addEventListener("click", () => selectExport(button.dataset.exportId));
    });
  }

  return {
    render,
    load,
    selectExport,
  };
}
