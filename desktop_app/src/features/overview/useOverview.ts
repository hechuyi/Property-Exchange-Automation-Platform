import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useDesktopRuntime } from "../../desktop/provider";
import { createExportFailureState, resolveExportTerminalState, type ExportViewState } from "../records/exportState";
import { getSharedRecordsScope } from "../records/scope";

export type OverviewPayload = Record<string, unknown>;
const SMOKE_INTERACTION_TRACE_KEY = "__PEAP_DESKTOP_SMOKE_INTERACTION_TRACE";

function getMutationErrorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === "object") {
    const detail = String((error as { detail?: unknown }).detail || "").trim();
    if (detail) {
      return detail;
    }
    const message = String((error as { message?: unknown }).message || "").trim();
    if (message) {
      return message;
    }
  }
  return fallback;
}

function appendManualImportMutationEvent(event: Record<string, unknown>) {
  const traceTarget = globalThis as typeof globalThis & Record<string, unknown>;
  const trace = traceTarget[SMOKE_INTERACTION_TRACE_KEY];
  if (!trace || typeof trace !== "object") {
    return;
  }
  const manualImport = (trace as { manualImport?: Record<string, unknown> }).manualImport;
  if (!manualImport || typeof manualImport !== "object") {
    return;
  }
  const mutationEvents = Array.isArray(manualImport.mutationEvents)
    ? manualImport.mutationEvents
    : [];
  if (!Array.isArray(manualImport.mutationEvents)) {
    manualImport.mutationEvents = mutationEvents;
  }
  mutationEvents.push({
    ts: Date.now(),
    ...event,
  });
}

function appendForceStopMutationEvent(event: Record<string, unknown>) {
  const traceTarget = globalThis as typeof globalThis & Record<string, unknown>;
  const trace = traceTarget[SMOKE_INTERACTION_TRACE_KEY];
  if (!trace || typeof trace !== "object") {
    return;
  }
  const forceStop = (trace as { forceStop?: Record<string, unknown> }).forceStop;
  if (!forceStop || typeof forceStop !== "object") {
    return;
  }
  const mutationEvents = Array.isArray(forceStop.mutationEvents)
    ? forceStop.mutationEvents
    : [];
  if (!Array.isArray(forceStop.mutationEvents)) {
    forceStop.mutationEvents = mutationEvents;
  }
  mutationEvents.push({
    ts: Date.now(),
    ...event,
  });
}

export function useOverviewData() {
  const { commands } = useDesktopRuntime();
  return useQuery({
    queryKey: ["overview"],
    queryFn: () => commands.getOverview() as Promise<OverviewPayload>,
    refetchInterval: 3000,
  });
}

export function useOverviewActions() {
  const queryClient = useQueryClient();
  const { commands } = useDesktopRuntime();
  const [exportState, setExportState] = useState<ExportViewState>({ kind: "idle" });
  const [manualImportError, setManualImportError] = useState<string>("");

  const reload = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["overview"] }),
      queryClient.invalidateQueries({ queryKey: ["jobs"] }),
      queryClient.invalidateQueries({ queryKey: ["job-events"] }),
    ]);
  };

  const runOneClick = useMutation({
    mutationFn: async () => commands.runOneClick({}),
    onSuccess: reload,
  });

  const runManualImport = useMutation({
    mutationFn: async () => {
      const inputDir = await window.peapDesktop?.pickDirectory?.();
      const normalizedInputDir = typeof inputDir === "string" ? inputDir.trim() : "";
      appendManualImportMutationEvent({
        phase: "pick_directory_resolved",
        inputDir: normalizedInputDir,
      });
      if (!normalizedInputDir) {
        appendManualImportMutationEvent({ phase: "skipped" });
        return { skipped: true };
      }
      appendManualImportMutationEvent({
        phase: "request_started",
        inputDir: normalizedInputDir,
      });
      try {
        const response = await commands.runManualImport({ input_dir: normalizedInputDir });
        const responseView = (response || {}) as Record<string, unknown>;
        const jobId = String(responseView.job_id || "").trim();
        if (!jobId) {
          appendManualImportMutationEvent({
            phase: "request_invalid_response",
            inputDir: normalizedInputDir,
            responseStatus: String(responseView.status || ""),
            responseJobType: String(responseView.job_type || ""),
          });
          throw new Error("手动导入任务启动响应缺少 job_id");
        }
        appendManualImportMutationEvent({
          phase: "request_succeeded",
          inputDir: normalizedInputDir,
          jobId,
          discoveredCount: Number(responseView.discovered_count || 0),
        });
        return response;
      } catch (error) {
        appendManualImportMutationEvent({
          phase: "request_failed",
          inputDir: normalizedInputDir,
          message: getMutationErrorMessage(error, "手动导入失败，请稍后重试。"),
        });
        throw error;
      }
    },
    onMutate: () => {
      setManualImportError("");
    },
    onSuccess: async (result) => {
      if (result && typeof result === "object" && "skipped" in result && result.skipped) {
        return;
      }
      await reload();
    },
    onError: (error) => {
      setManualImportError(getMutationErrorMessage(error, "手动导入失败，请稍后重试。"));
    },
  });

  const runExport = useMutation({
    mutationFn: async () =>
      commands.runExport({
        scope: getSharedRecordsScope(),
        mode: "rebuild",
      }),
    onMutate: () => {
      setExportState({ kind: "loading" });
    },
    onSuccess: async (response) => {
      setExportState(resolveExportTerminalState(response));
      await reload();
    },
    onError: (error) => {
      setExportState(createExportFailureState((error as Error)?.message || String(error || "导出失败")));
    },
  });

  const forceStop = useMutation({
    mutationFn: async () => {
      appendForceStopMutationEvent({ phase: "request_started" });
      if (!window.peapDesktop?.restartBackend) {
        const error = new Error("桌面桥接未提供 restartBackend");
        appendForceStopMutationEvent({
          phase: "request_failed",
          message: getMutationErrorMessage(error, "强制停止失败，请稍后重试。"),
        });
        throw error;
      }
      try {
        const response = await window.peapDesktop.restartBackend();
        appendForceStopMutationEvent({ phase: "request_succeeded" });
        return response;
      } catch (error) {
        appendForceStopMutationEvent({
          phase: "request_failed",
          message: getMutationErrorMessage(error, "强制停止失败，请稍后重试。"),
        });
        throw error;
      }
    },
    onSuccess: reload,
  });

  return {
    runOneClick,
    runManualImport,
    manualImportError,
    runExport,
    exportState,
    forceStop,
  };
}
