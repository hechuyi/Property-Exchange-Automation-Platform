function countValue(value: unknown) {
  const numeric = Number(value || 0);
  return Number.isFinite(numeric) ? numeric : 0;
}

type RecordsSummaryPayload = {
  total_count?: number;
  page?: number;
  page_count?: number;
  summary?: {
    total_count?: number;
    visible_count?: number;
    page?: number;
    page_count?: number;
    filtered_state_counts?: Record<string, unknown>;
    state_counts?: Record<string, unknown>;
  };
};

export function formatRecordsSummary(payload: RecordsSummaryPayload = {}) {
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
  ]
    .filter(Boolean)
    .join(" · ");
}
