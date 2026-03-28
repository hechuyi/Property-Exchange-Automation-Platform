import { useQuery } from "@tanstack/react-query";
import { useDesktopRuntime } from "../../desktop/provider";

export type JobItem = Record<string, unknown>;
export type JobEventItem = Record<string, unknown>;

export function useJobsQuery() {
  const { commands } = useDesktopRuntime();
  return useQuery({
    queryKey: ["jobs"],
    queryFn: async () => {
      const payload = (await commands.listJobs({ limit: 20 })) as { jobs?: JobItem[] };
      return Array.isArray(payload.jobs) ? payload.jobs : [];
    },
    refetchInterval: 3000,
  });
}

export function useJobEventsQuery(jobId: string) {
  const { commands } = useDesktopRuntime();
  return useQuery({
    queryKey: ["job-events", jobId],
    queryFn: async () => {
      const payload = (await commands.listJobEvents(jobId, { limit: 200 })) as {
        events?: JobEventItem[];
        truncated?: boolean;
        returned_count?: number;
        total_count?: number;
      };
      return {
        events: Array.isArray(payload.events) ? payload.events : [],
        truncated: Boolean(payload.truncated),
        returnedCount: Number(payload.returned_count || 0),
        totalCount: Number(payload.total_count || 0),
      };
    },
    enabled: Boolean(jobId),
    refetchInterval: 3000,
  });
}
