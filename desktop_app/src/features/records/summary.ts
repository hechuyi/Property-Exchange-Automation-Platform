function countValue(value: unknown) {
  const numeric = Number(value || 0);
  return Number.isFinite(numeric) ? numeric : 0;
}

function sumCounts(stateCounts: Record<string, unknown>, keys: string[]) {
  return keys.reduce((total, key) => total + countValue(stateCounts[key]), 0);
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
  const readyCount = sumCounts(stateCounts, ["ready"]);
  const pendingMappingCount = sumCounts(stateCounts, ["pending_mapping", "mapping_conflict"]);
  const skippedCount = sumCounts(stateCounts, ["skipped"]);
  const attentionCount = sumCounts(stateCounts, ["parse_failed", "postprocess_failed", "conflict"]);

  return [
    `共 ${countValue(summary.total_count ?? payload.total_count)} 条`,
    `第 ${countValue(payload.page || summary.page || 1)} / ${countValue(payload.page_count || summary.page_count || 0)} 页`,
    `本页 ${countValue(summary.visible_count)} 条`,
    readyCount > 0 ? `已就绪 ${readyCount} 条` : "",
    pendingMappingCount > 0 ? `待补映射 ${pendingMappingCount} 条` : "",
    skippedCount > 0 ? `已跳过 ${skippedCount} 条` : "",
    attentionCount > 0 ? `需人工处理 ${attentionCount} 条` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}
