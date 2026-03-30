import { Card, Empty, Space, Typography } from "antd";
import { formatEventDetail, formatEventTitle, formatJobMeta, formatJobTitle } from "./formatters";
import type { JobEventItem, JobItem } from "./useTasksData";

const JOB_SCROLL_STYLE = {
  maxHeight: 240,
  overflowY: "auto" as const,
  paddingRight: 4,
};

const EVENT_SCROLL_STYLE = {
  maxHeight: 320,
  overflowY: "auto" as const,
  paddingRight: 4,
};

type TaskActivityPanelProps = {
  caption?: string;
  jobs: JobItem[];
  jobsLoading?: boolean;
  selectedJobId: string;
  onSelectJob: (jobId: string) => void;
  events: JobEventItem[];
  eventsLoading?: boolean;
  capacityNotice?: string;
  jobListTestId?: string;
  eventListTestId?: string;
};

export function TaskActivityPanel({
  caption = "",
  jobs,
  jobsLoading = false,
  selectedJobId,
  onSelectJob,
  events,
  eventsLoading = false,
  capacityNotice = "",
  jobListTestId,
  eventListTestId,
}: TaskActivityPanelProps) {
  return (
    <Card title="任务活动" data-testid="task-activity-panel">
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        {caption ? <Typography.Text type="secondary">{caption}</Typography.Text> : null}
        <Card size="small" title="最近任务" data-testid={jobListTestId}>
          <div data-testid="task-activity-job-scroll" style={JOB_SCROLL_STYLE}>
            {jobs.length === 0 ? (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={jobsLoading ? "正在同步任务…" : "暂无任务"}
              />
            ) : (
              <Space direction="vertical" style={{ width: "100%" }} size={8}>
                {jobs.map((job) => {
                  const jobId = String(job.job_id || "");
                  return (
                    <button
                      key={jobId}
                      type="button"
                      aria-label={formatJobTitle(job)}
                      onClick={() => onSelectJob(jobId)}
                      style={{
                        width: "100%",
                        textAlign: "left",
                        border: "1px solid #d9d9d9",
                        borderRadius: 6,
                        padding: 12,
                        background: jobId === selectedJobId ? "#e6f4ff" : "#fff",
                        cursor: "pointer",
                      }}
                    >
                      <Space direction="vertical" size={4} style={{ width: "100%" }}>
                        <Typography.Text strong>{formatJobTitle(job)}</Typography.Text>
                        <Typography.Text type="secondary">{formatJobMeta(job)}</Typography.Text>
                      </Space>
                    </button>
                  );
                })}
              </Space>
            )}
          </div>
        </Card>
        <Card size="small" title="任务明细" data-testid={eventListTestId}>
          <div data-testid="task-activity-event-scroll" style={EVENT_SCROLL_STYLE}>
            <Space direction="vertical" style={{ width: "100%" }} size={8}>
              {capacityNotice ? <Typography.Text type="warning">{capacityNotice}</Typography.Text> : null}
              {selectedJobId && events.length === 0 ? (
                <Typography.Text type="secondary">{eventsLoading ? "加载中…" : "暂无事件"}</Typography.Text>
              ) : null}
              {!selectedJobId ? <Typography.Text type="secondary">选择任务后查看明细</Typography.Text> : null}
              {events.map((event, index) => (
                <div key={`${String(event.id || event.project_code || "event")}-${index}`}>
                  <Typography.Text strong>{formatEventTitle(event)}</Typography.Text>
                  <br />
                  <Typography.Text type="secondary">{formatEventDetail(event)}</Typography.Text>
                </div>
              ))}
            </Space>
          </div>
        </Card>
      </Space>
    </Card>
  );
}
