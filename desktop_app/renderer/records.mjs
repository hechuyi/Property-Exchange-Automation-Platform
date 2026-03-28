function countValue(value) {
  const numeric = Number(value || 0);
  return Number.isFinite(numeric) ? numeric : 0;
}

export function buildRecordsQuery({
  recordFamily = "listing",
  state = "all",
  projectType = "all",
  keyword = "",
  dateFrom = "",
  dateTo = "",
  page = 1,
  pageSize = 50,
} = {}) {
  const query = new URLSearchParams({
    record_family: recordFamily,
    state,
    project_type: projectType,
    page: String(page),
    page_size: String(pageSize),
  });
  if (keyword) {
    query.set("keyword", String(keyword).trim());
  }
  if (dateFrom) {
    query.set("date_from", String(dateFrom).trim());
  }
  if (dateTo) {
    query.set("date_to", String(dateTo).trim());
  }
  return query;
}

export function formatRecordsSummary(payload = {}) {
  const summary = payload.summary || {};
  const stateCounts = summary.filtered_state_counts || summary.state_counts || {};
  return [
    `共 ${countValue(summary.total_count ?? payload.total_count)} 条`,
    `第 ${countValue(payload.page || summary.page || 1)} / ${countValue(payload.page_count || summary.page_count || 0)} 页`,
    `本页 ${countValue(summary.visible_count)} 条`,
    countValue(stateCounts.ready) > 0 ? `已录入 ${countValue(stateCounts.ready)} 条` : "",
    countValue(stateCounts.pending_mapping) > 0 ? `待补映射 ${countValue(stateCounts.pending_mapping)} 条` : "",
    countValue(stateCounts.skipped) > 0 ? `已跳过 ${countValue(stateCounts.skipped)} 条` : "",
    countValue(stateCounts.parse_failed) > 0 ? `解析失败 ${countValue(stateCounts.parse_failed)} 条` : "",
    countValue(stateCounts.postprocess_failed) > 0 ? `处理失败 ${countValue(stateCounts.postprocess_failed)} 条` : "",
    countValue(stateCounts.conflict) > 0 ? `归档重名 ${countValue(stateCounts.conflict)} 条` : "",
  ].filter(Boolean).join(" · ");
}

export function formatPendingMappingsSummary(payload = {}) {
  const pending = Array.isArray(payload.pending) ? payload.pending : [];
  const returnedCount = countValue(payload.returned_count ?? pending.length);
  const totalCount = countValue(payload.total_count ?? returnedCount);
  const base = `当前 ${returnedCount} 条待补项`;
  if (!payload.truncated || totalCount <= returnedCount) {
    return base;
  }
  const remainingCount = totalCount - returnedCount;
  return `${base}；只显示前 ${returnedCount} 条待补项，仍有剩余 ${remainingCount} 条`;
}

export function buildRecordsTableMarkup(payload, { escapeHtml }) {
  const rows = Array.isArray(payload.rows) ? payload.rows : [];
  const columns = Array.isArray(payload.columns) ? payload.columns : [];
  if (!rows.length) {
    return '<div class="mapping-item">当前筛选条件下没有记录</div>';
  }

  const detailColumns = columns.filter((column) => !["项目编号", "项目名称", "项目类型", "交易所", "挂牌开始日期", "挂牌日期"].includes(column));
  const tableHead = detailColumns
    .map((column) => `<th>${escapeHtml(column)}</th>`)
    .join("");
  const tableBody = rows
    .map((row) => {
      const cells = detailColumns
        .map((column) => `<td>${escapeHtml(row.values?.[column] || "")}</td>`)
        .join("");
      const archiveDisabled = row.archive_path ? "" : "disabled";
      const locateTarget = row.archive_path || row.source_file || "";
      return `
        <tr>
          <td>
            <div class="record-status-wrap">
              <span class="record-status ${escapeHtml(row.state)}">${escapeHtml(row.status_label)}</span>
              ${row.status_detail ? `<div class="record-status-detail">${escapeHtml(row.status_detail)}</div>` : ""}
            </div>
          </td>
          <td>${escapeHtml(row.project_code || "")}</td>
          <td>${escapeHtml(row.project_name || "")}</td>
          <td>${escapeHtml(row.project_type || "")}</td>
          <td>${escapeHtml(row.exchange || "")}</td>
          <td>${escapeHtml(row.listing_date || row.values?.["挂牌开始日期"] || row.values?.["挂牌日期"] || "")}</td>
          ${cells}
          <td>${escapeHtml(row.updated_at || "")}</td>
          <td>
            <div class="table-actions">
              <button class="action ghost" type="button" data-open-archive="${escapeHtml(row.archive_path || "")}" ${archiveDisabled}>打开归档</button>
              <button class="action ghost" type="button" data-locate-record="${escapeHtml(locateTarget)}">定位文件</button>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");
  return `
    <table>
      <thead>
        <tr>
          <th>录入状态</th>
          <th>项目编号</th>
          <th>项目名称</th>
          <th>业务类型</th>
          <th>交易所</th>
          <th>挂牌日期</th>
          ${tableHead}
          <th>最近更新</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>${tableBody}</tbody>
    </table>
  `;
}
