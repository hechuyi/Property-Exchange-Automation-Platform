import { Card, Empty, Space, Typography } from "antd";
import { useEffect, useMemo, useState } from "react";
import {
  formatCapacityNotice,
  formatEventDetail,
  formatEventTitle,
  formatJobMeta,
  formatJobTitle,
} from "../features/tasks/formatters";
import { useJobEventsQuery, useJobsQuery } from "../features/tasks/useTasksData";
import { PAGE_TEST_IDS } from "../testing/selectors";

export default function TasksPage() {
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
  const events = eventsQuery.data?.events || [];
  const capacityNotice = formatCapacityNotice({
    returnedCount: eventsQuery.data?.returnedCount || 0,
    totalCount: eventsQuery.data?.totalCount || 0,
    noun: "事件",
  });

  return (
    <div data-testid={PAGE_TEST_IDS.tasks.page}>
      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        <Card title="任务列表" data-testid={PAGE_TEST_IDS.tasks.jobList}>
          {jobs.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无任务" />
          ) : (
            <Space direction="vertical" style={{ width: "100%" }} size={8}>
              {jobs.map((job) => {
                const jobId = String(job.job_id || "");
                return (
                  <button
                    key={jobId}
                    type="button"
                    aria-label={formatJobTitle(job)}
                    onClick={() => setSelectedJobId(jobId)}
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
        </Card>

        <Card title="任务事件" data-testid={PAGE_TEST_IDS.tasks.eventList}>
          <Space direction="vertical" style={{ width: "100%" }} size={8}>
            {capacityNotice ? <Typography.Text type="warning">{capacityNotice}</Typography.Text> : null}
            {selectedJobId && events.length === 0 ? (
              <Typography.Text type="secondary">{eventsQuery.isLoading ? "加载中…" : "暂无事件"}</Typography.Text>
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
        </Card>
      </Space>
    </div>
  );
}
