type ExportResponsePayload = Record<string, unknown>;

export type ExportTerminalState =
  | {
      kind: "in_progress";
      status: string;
      jobId: string;
      message: string;
    }
  | {
      kind: "completed";
      status: string;
      jobId: string;
      message: string;
      artifactsCount: number;
    }
  | {
      kind: "empty";
      status: string;
      jobId: string;
      message: string;
      emptyReasonCode: string;
      scopeStateCounts: Record<string, number>;
    }
  | {
      kind: "failed";
      status: string;
      jobId: string;
      message: string;
    };

export type ExportViewState = { kind: "idle" } | { kind: "loading" } | ExportTerminalState;

function toRecord(value: unknown): ExportResponsePayload {
  if (!value || typeof value !== "object") {
    return {};
  }
  return value as ExportResponsePayload;
}

function toText(value: unknown): string {
  return String(value || "").trim();
}

function toScopeStateCounts(value: unknown): Record<string, number> {
  if (!value || typeof value !== "object") {
    return {};
  }
  const entries = Object.entries(value as Record<string, unknown>).map(([key, count]) => [
    key,
    Number.isFinite(Number(count)) ? Number(count) : 0,
  ]);
  return Object.fromEntries(entries);
}

function defaultMessage(status: string): string {
  if (status === "queued" || status === "pending" || status === "running" || status === "in_progress") {
    return "导出任务已提交，正在处理中";
  }
  if (status === "empty" || status === "success_with_warnings") {
    return "当前条件下没有可导出的记录";
  }
  if (status === "completed" || status === "done" || status === "success") {
    return "导出完成";
  }
  if (status === "failed" || status === "error") {
    return "导出失败";
  }
  return "导出返回未知状态";
}

function normalizeStatus(payload: ExportResponsePayload): string {
  return toText(payload.status).toLowerCase();
}

function toArtifactsCount(payload: ExportResponsePayload): number {
  return Array.isArray(payload.artifacts) ? payload.artifacts.length : 0;
}

export function resolveExportTerminalState(rawPayload: unknown): ExportTerminalState {
  const payload = toRecord(rawPayload);
  const status = normalizeStatus(payload);
  const jobId = toText(payload.job_id);
  const message = toText(payload.message) || defaultMessage(status);
  const artifactsCount = toArtifactsCount(payload);

  if (status === "failed" || status === "error") {
    return {
      kind: "failed",
      status,
      jobId,
      message,
    };
  }

  if (status === "empty" || status === "success_with_warnings") {
    return {
      kind: "empty",
      status,
      jobId,
      message,
      emptyReasonCode: toText(payload.empty_reason_code) || "unknown",
      scopeStateCounts: toScopeStateCounts(payload.scope_state_counts),
    };
  }

  if (status === "completed" || status === "done" || status === "success") {
    if (artifactsCount <= 0) {
      return {
        kind: "empty",
        status,
        jobId,
        message,
        emptyReasonCode: toText(payload.empty_reason_code) || "unknown",
        scopeStateCounts: toScopeStateCounts(payload.scope_state_counts),
      };
    }
    return {
      kind: "completed",
      status,
      jobId,
      message,
      artifactsCount,
    };
  }

  if (status === "queued" || status === "pending" || status === "running" || status === "in_progress" || status === "processing") {
    return {
      kind: "in_progress",
      status,
      jobId,
      message,
    };
  }

  if (jobId) {
    return {
      kind: "in_progress",
      status: status || "queued",
      jobId,
      message: message || "导出任务已提交，正在处理中",
    };
  }

  return {
    kind: "failed",
    status: status || "unknown",
    jobId,
    message: message || "导出返回未知状态",
  };
}

export function createExportFailureState(error: unknown): ExportTerminalState {
  const message = toText(error) || "导出失败";
  return {
    kind: "failed",
    status: "failed",
    jobId: "",
    message,
  };
}

export function describeExportState(state: ExportViewState): string {
  if (state.kind === "idle") {
    return "";
  }
  if (state.kind === "loading") {
    return "导出请求提交中…";
  }

  const jobIdSuffix = state.jobId ? `（任务 ${state.jobId}）` : "";

  if (state.kind === "in_progress") {
    return `导出任务进行中：${state.message}${jobIdSuffix}`;
  }
  if (state.kind === "completed") {
    return `导出已完成：${state.message}${jobIdSuffix}`;
  }
  if (state.kind === "empty") {
    const pendingCount = state.scopeStateCounts.pending_mapping ?? 0;
    const skippedCount = state.scopeStateCounts.skipped ?? 0;
    const countsDetail = pendingCount > 0 || skippedCount > 0 ? `（待补映射 ${pendingCount}，已跳过 ${skippedCount}）` : "";
    return `导出结果为空：${state.message}（${state.emptyReasonCode}）${countsDetail}${jobIdSuffix}`;
  }
  return `导出失败：${state.message}${jobIdSuffix}`;
}
