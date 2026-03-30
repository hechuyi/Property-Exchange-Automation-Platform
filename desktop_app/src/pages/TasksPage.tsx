import { Alert, Space } from "antd";
import { useEffect, useMemo, useState } from "react";
import { formatCapacityNotice } from "../features/tasks/formatters";
import { TaskActivityPanel } from "../features/tasks/TaskActivityPanel";
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
        <Alert
          showIcon
          type="info"
          message="任务历史"
          description="日常监控已移到工作台，这里保留为次级历史视图。"
        />
        <TaskActivityPanel
          jobs={jobs}
          jobsLoading={jobsQuery.isLoading}
          selectedJobId={selectedJobId}
          onSelectJob={setSelectedJobId}
          events={events}
          eventsLoading={eventsQuery.isLoading}
          capacityNotice={capacityNotice}
          jobListTestId={PAGE_TEST_IDS.tasks.jobList}
          eventListTestId={PAGE_TEST_IDS.tasks.eventList}
        />
      </Space>
    </div>
  );
}
