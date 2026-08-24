import test from "node:test";
import assert from "node:assert/strict";

import { createTasksPanel, isJobRetryable } from "../src/panels/tasks.js";

function createFakeButton(jobId) {
  const listeners = new Map();
  return {
    dataset: { jobId },
    addEventListener(type, handler) {
      listeners.set(type, handler);
    },
    trigger(type) {
      return listeners.get(type)?.();
    },
  };
}

function createPanel({ job, onRetry, listJobs = async () => ({ jobs: [job] }) }) {
  const retryButton = createFakeButton(job.job_id);
  const card = {
    innerHTML: "",
    querySelectorAll(selector) {
      return selector === ".btn-job-retry" ? [retryButton] : [];
    },
  };
  let jobs = [job];
  const panel = createTasksPanel({
    $: () => card,
    API: { listJobs },
    escapeHtml: (value) => String(value ?? ""),
    num: (value) => Number(value) || 0,
    formatJobTime: () => "",
    jobTypeLabel: () => "一键执行",
    jobStatusLabel: () => "失败",
    stateDotClass: () => "failed",
    getJobs: () => jobs,
    setJobs: (nextJobs) => { jobs = nextJobs; },
    onRetry,
  });
  return { panel, card, retryButton };
}

test("job retry eligibility consumes only the server-published action capability", () => {
  assert.equal(isJobRetryable({ actions: { retry: true }, status: "success", job_type: "unknown" }), true);
  assert.equal(isJobRetryable({ actions: { retry: false }, status: "failed", job_type: "one_click" }), false);
  assert.equal(isJobRetryable({ status: "failed", job_type: "one_click" }), false);
  assert.equal(isJobRetryable({ actions: { retry: "true" } }), false);
});

test("tasks panel retries an eligible job, disables actions while pending, and reloads on success", async () => {
  const job = { job_id: "job-retry", job_type: "one_click", status: "failed", actions: { retry: true } };
  let receivedJob = null;
  let resolveRetry;
  let reloadCount = 0;
  const { panel, card, retryButton } = createPanel({
    job,
    onRetry(value) {
      receivedJob = value;
      return new Promise((resolve) => { resolveRetry = resolve; });
    },
    listJobs: async () => {
      reloadCount += 1;
      return { jobs: [job] };
    },
  });

  panel.renderList();
  assert.match(card.innerHTML, /重试/);
  const pending = retryButton.trigger("click");
  assert.equal(receivedJob, job);
  assert.match(card.innerHTML, /重试中/);
  assert.match(card.innerHTML, /disabled/);

  resolveRetry();
  await pending;
  assert.equal(reloadCount, 1);
});

test("tasks panel shows a visible retry failure and does not offer retries for ineligible jobs", async () => {
  const job = { job_id: "job-failed", job_type: "one_click", status: "failed", actions: { retry: true } };
  const { panel, card, retryButton } = createPanel({
    job,
    onRetry: async () => { throw new Error("retry unavailable"); },
  });

  panel.renderList();
  await retryButton.trigger("click");
  assert.match(card.innerHTML, /重试任务失败：retry unavailable/);

  const ineligible = createPanel({
    job: { job_id: "job-success", job_type: "one_click", status: "success", actions: { retry: false } },
    onRetry: async () => {},
  });
  ineligible.panel.renderList();
  assert.doesNotMatch(ineligible.card.innerHTML, /btn-job-retry/);
});
