import {
  ACTIVE_POLL_MS,
  ACTIVE_STATUSES,
  TASKS_POLL_MS,
} from "../constants/index.js";

function asText(value) {
  return String(value ?? "").trim();
}

function isActiveStatus(status) {
  return ACTIVE_STATUSES.has(asText(status));
}

export function shouldAutoPollPanel(panel = "", overview = {}, jobs = []) {
  const normalizedPanel = asText(panel);
  if (normalizedPanel !== "tasks" && normalizedPanel !== "overview") return false;
  if (normalizedPanel === "tasks") {
    return Array.isArray(jobs) && jobs.some((job) => isActiveStatus(job?.status));
  }
  const latestJob = overview && typeof overview === "object" ? overview.latest_job : null;
  return Boolean(latestJob && isActiveStatus(latestJob.status));
}

export function pollDelayForPanel(panel = "", overview = {}) {
  const normalizedPanel = asText(panel);
  if (normalizedPanel === "tasks") return TASKS_POLL_MS;
  if (normalizedPanel === "overview" && shouldAutoPollPanel(normalizedPanel, overview)) {
    return ACTIVE_POLL_MS;
  }
  return TASKS_POLL_MS;
}
