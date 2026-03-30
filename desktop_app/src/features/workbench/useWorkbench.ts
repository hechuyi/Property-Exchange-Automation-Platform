import { useEffect, useMemo, useState } from "react";
import { summarizeRuntimeState } from "../overview/runtime";
import { useOverviewActions, useOverviewData } from "../overview/useOverview";
import { describeExportState } from "../records/exportState";
import {
  formatCapacityNotice,
  formatProgressHint,
  formatProgressMeta,
  progressPreset,
} from "../tasks/formatters";
import { useJobEventsQuery, useJobsQuery } from "../tasks/useTasksData";

const ACTIVE_JOB_STATUSES = new Set([
  "accepted",
  "queued",
  "pending",
  "running",
  "in_progress",
  "processing",
]);

function isActiveJobStatus(status: unknown) {
  return ACTIVE_JOB_STATUSES.has(String(status || "").trim().toLowerCase());
}

export function useWorkbench() {
  const overviewQuery = useOverviewData();
  const actions = useOverviewActions();
  const jobsQuery = useJobsQuery();
  const jobs = useMemo(() => jobsQuery.data || [], [jobsQuery.data]);
  const [selectedJobId, setSelectedJobId] = useState("");

  useEffect(() => {
    if (!selectedJobId && jobs.length > 0) {
      setSelectedJobId(String(jobs[0]?.job_id || ""));
      return;
    }
    if (selectedJobId && jobs.length > 0 && !jobs.some((job) => String(job?.job_id || "") === selectedJobId)) {
      setSelectedJobId(String(jobs[0]?.job_id || ""));
    }
  }, [jobs, selectedJobId]);

  const eventsQuery = useJobEventsQuery(selectedJobId);
  const overview = (overviewQuery.data || {}) as Record<string, unknown>;
  const latestProgress = (overview.latest_progress || {}) as Record<string, unknown>;
  const latestJob = (overview.latest_job || null) as Record<string, unknown> | null;
  const recentJobs = Array.isArray(overview.recent_jobs)
    ? overview.recent_jobs.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    : [];
  const progressView = progressPreset(latestProgress);
  const progressMeta = formatProgressMeta(latestProgress, latestJob, overview);
  const progressHint = formatProgressHint(latestProgress, latestJob, overview);
  const exportStatusText = describeExportState(actions.exportState);
  const isJobRunning = isActiveJobStatus(latestJob?.status)
    || recentJobs.some((job) => isActiveJobStatus(job.status))
    || jobs.some((job) => isActiveJobStatus(job.status));
  const pendingMappingCount = Number(overview.pending_mapping_count || 0);
  const runtimeSummary = useMemo(
    () =>
      summarizeRuntimeState({
        browserRuntime: (overview.browser_runtime || null) as Record<string, unknown> | null,
        productReadiness: (overview.product_readiness || null) as Record<string, unknown> | null,
        browserInstall: (overview.browser_install || null) as Record<string, unknown> | null,
      }),
    [overview.browser_install, overview.browser_runtime, overview.product_readiness],
  );

  return {
    overviewQuery,
    actions,
    progressView,
    progressMeta,
    progressHint,
    exportStatusText,
    isJobRunning,
    pendingMappingCount,
    runtimeSummary,
    taskActivity: {
      jobs,
      jobsLoading: jobsQuery.isLoading,
      selectedJobId,
      setSelectedJobId,
      events: eventsQuery.data?.events || [],
      eventsLoading: eventsQuery.isLoading,
      capacityNotice: formatCapacityNotice({
        returnedCount: eventsQuery.data?.returnedCount || 0,
        totalCount: eventsQuery.data?.totalCount || 0,
        noun: "事件",
      }),
    },
  };
}
