import { Alert, Button, Card, Progress, Space, Typography } from "antd";
import { TaskActivityPanel } from "../features/tasks/TaskActivityPanel";
import { formatProgressTitle } from "../features/tasks/formatters";
import { useWorkbench } from "../features/workbench/useWorkbench";
import { PAGE_TEST_IDS } from "../testing/selectors";

export default function WorkbenchPage() {
  const {
    overviewQuery,
    actions,
    progressView,
    progressMeta,
    progressHint,
    exportStatusText,
    isJobRunning,
    pendingMappingCount,
    runtimeSummary,
    taskActivity,
  } = useWorkbench();
  const latestProgress = (overviewQuery.data?.latest_progress as Record<string, unknown> | undefined) || {};
  const latestJob = (overviewQuery.data?.latest_job as Record<string, unknown> | null | undefined) || null;
  const progressTitle = formatProgressTitle(latestProgress, latestJob, (overviewQuery.data || {}) as Record<string, unknown>);

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

        <div
          style={{
            display: "grid",
            gap: 16,
            gridTemplateColumns: "minmax(0, 1.4fr) minmax(320px, 1fr)",
            alignItems: "start",
          }}
        >
          <Space direction="vertical" size={16} style={{ width: "100%" }}>
            <Card id="statPendingMappingCard" size="small" title="待补映射">
              <Typography.Text strong>{pendingMappingCount}</Typography.Text>
            </Card>

            <Card title="进度" data-testid={PAGE_TEST_IDS.overview.progressCard}>
              {overviewQuery.isLoading ? (
                <Typography.Text type="secondary">加载中…</Typography.Text>
              ) : (
                <Space direction="vertical" style={{ width: "100%" }} size={8}>
                  <Typography.Text strong>{progressTitle}</Typography.Text>
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
          </Space>
          <div>
            <TaskActivityPanel
              caption="日常监控已内联到工作台，任务页只保留为次级历史视图。"
              jobs={taskActivity.jobs}
              jobsLoading={taskActivity.jobsLoading}
              selectedJobId={taskActivity.selectedJobId}
              onSelectJob={taskActivity.setSelectedJobId}
              events={taskActivity.events}
              eventsLoading={taskActivity.eventsLoading}
              capacityNotice={taskActivity.capacityNotice}
            />
          </div>
        </div>

        {overviewQuery.isError ? <Alert type="error" showIcon message={String(overviewQuery.error)} /> : null}
      </Space>
    </div>
  );
}
