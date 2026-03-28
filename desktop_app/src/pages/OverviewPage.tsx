import { Alert, Button, Card, Progress, Space, Typography } from "antd";
import { useMemo } from "react";
import { summarizeRuntimeState } from "../features/overview/runtime";
import { useOverviewActions, useOverviewData } from "../features/overview/useOverview";
import { describeExportState } from "../features/records/exportState";
import { formatProgressHint, formatProgressMeta, progressPreset } from "../features/tasks/formatters";
import { PAGE_TEST_IDS } from "../testing/selectors";

export default function OverviewPage() {
  const overviewQuery = useOverviewData();
  const actions = useOverviewActions();

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
  const isJobRunning = String(latestJob?.status || "").trim() === "running"
    || recentJobs.some((job) => String(job.status || "").trim() === "running");
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

  return (
    <div data-testid={PAGE_TEST_IDS.overview.page}>
      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        <Card id="homePrimaryActions" title="主要操作" data-testid={PAGE_TEST_IDS.overview.primaryActions}>
          <Space wrap>
            <Button id="runOneClickBtn" loading={actions.runOneClick.isPending} onClick={() => actions.runOneClick.mutate()}>
              一键执行
            </Button>
            <Button
              id="runManualImportBtn"
              loading={actions.runManualImport.isPending}
              onClick={() => actions.runManualImport.mutate()}
            >
              手动导入
            </Button>
            <Button id="runExportBtn" loading={actions.runExport.isPending} onClick={() => actions.runExport.mutate()}>
              导出 Excel
            </Button>
            <Button
              id="forceStopBtn"
              danger
              disabled={!isJobRunning}
              loading={actions.forceStop.isPending}
              onClick={() => actions.forceStop.mutate()}
            >
              强制停止
            </Button>
          </Space>
          {actions.exportState.kind === "failed" ? (
            <Alert showIcon type="error" message={exportStatusText || "导出失败"} style={{ marginTop: 12 }} />
          ) : null}
          {actions.manualImportError ? (
            <Alert showIcon type="error" message={actions.manualImportError} style={{ marginTop: 12 }} />
          ) : null}
          {actions.exportState.kind !== "idle" && actions.exportState.kind !== "failed" ? (
            <Typography.Text role="status" style={{ display: "block", marginTop: 12 }}>
              {exportStatusText}
            </Typography.Text>
          ) : null}
        </Card>

        <Card id="statPendingMappingCard" size="small" title="待补映射">
          <Typography.Text strong>{pendingMappingCount}</Typography.Text>
        </Card>

        <Card title="进度" data-testid={PAGE_TEST_IDS.overview.progressCard}>
          {overviewQuery.isLoading ? (
            <Typography.Text type="secondary">加载中…</Typography.Text>
          ) : (
            <Space direction="vertical" style={{ width: "100%" }} size={8}>
              <Typography.Text strong>{String(latestProgress.phase_label || "暂无任务")}</Typography.Text>
              <Typography.Text>{`${Math.max(0, Math.min(100, Math.round(progressView.width)))}%`}</Typography.Text>
              <Progress percent={Math.max(0, Math.min(100, Math.round(progressView.width)))} showInfo={false} />
              <Typography.Text>{progressMeta || "暂无任务数据"}</Typography.Text>
              <Typography.Text type="secondary">{progressHint || "暂无提示"}</Typography.Text>
            </Space>
          )}
        </Card>

        <Card title="运行环境" data-testid={PAGE_TEST_IDS.overview.runtimeCard}>
          <Space direction="vertical" size={8}>
            <Typography.Text strong>{runtimeSummary.headline}</Typography.Text>
            <Typography.Text>{runtimeSummary.browserState}</Typography.Text>
            {runtimeSummary.detailLines.map((line) => (
              <Typography.Text key={line} type="secondary">
                {line}
              </Typography.Text>
            ))}
          </Space>
        </Card>

        {overviewQuery.isError ? <Alert type="error" showIcon message={String(overviewQuery.error)} /> : null}
      </Space>
    </div>
  );
}
