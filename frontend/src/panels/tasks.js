import { buildJobMetaText } from "../presenters/jobPresentation.mjs";

export function isJobRetryable(job = {}) {
  return job?.actions?.retry === true;
}

export function createTasksPanel({
  $,
  API,
  escapeHtml,
  num,
  formatJobTime,
  jobTypeLabel,
  jobStatusLabel,
  stateDotClass,
  getJobs,
  setJobs,
  onRetry,
}) {
  let retryingJobId = "";
  let taskActionErrorMessage = "";

  function renderList() {
    const el = $("#panel-tasks .card");
    const jobs = Array.isArray(getJobs()) ? getJobs() : [];
    if (!jobs.length) {
      el.innerHTML = `<div style="padding:24px 0;text-align:center;color:var(--text-faint)">暂无任务</div>`;
      return;
    }
    const actionError = taskActionErrorMessage
      ? `<div class="alert alert-danger" role="alert">${escapeHtml(taskActionErrorMessage)}</div>`
      : "";
    el.innerHTML = `
      ${actionError}
      <ul class="job-list">
        ${jobs.map((job) => {
          const status = String(job.status || "");
          const meta = buildJobMetaText(job, { num });
          const retryable = isJobRetryable(job);
          const retrying = retryingJobId === String(job.job_id || "");
          return `<li class="job-item">
            <span class="job-status-dot ${stateDotClass(status)}"></span>
            <div class="job-info">
              <div class="job-type">${jobTypeLabel(job.job_type)} · ${jobStatusLabel(job.status)}</div>
              <div class="job-time">${formatJobTime(job.created_at)}</div>
              <div class="job-time">${meta}</div>
            </div>
            ${retryable
              ? `<button class="btn btn-sm btn-job-retry" type="button" data-job-id="${escapeHtml(job.job_id)}" ${retryingJobId ? "disabled" : ""}>${retrying ? "重试中..." : "重试"}</button>`
              : ""}
          </li>`;
        }).join("")}
      </ul>
    `;

    el.querySelectorAll(".btn-job-retry").forEach((button) => {
      button.addEventListener("click", async () => {
        const jobId = String(button.dataset.jobId || "").trim();
        const job = jobs.find((item) => String(item.job_id || "").trim() === jobId);
        if (!job || retryingJobId || typeof onRetry !== "function") return;
        taskActionErrorMessage = "";
        retryingJobId = jobId;
        renderList();
        try {
          await onRetry(job);
          await load();
        } catch (error) {
          console.error("Job retry failed:", error);
          taskActionErrorMessage = `重试任务失败：${error.message || "请稍后重试。"}`;
        } finally {
          retryingJobId = "";
          renderList();
        }
      });
    });
  }

  async function load() {
    try {
      const data = await API.listJobs(50);
      setJobs(Array.isArray(data.jobs) ? data.jobs : []);
      renderList();
    } catch (error) {
      $("#panel-tasks .card").innerHTML = `<div class="alert alert-danger">加载失败: ${error.message}</div>`;
    }
  }

  function render() {
    const el = $("#panel-tasks");
    el.innerHTML = `
      <div class="animate-in"><h1 class="page-title">任务</h1></div>
      <div class="card animate-in delay-1">
        <div style="color:var(--text-muted);font-size:13px;padding:24px 0;text-align:center">加载中...</div>
      </div>
    `;
  }

  return {
    render,
    load,
    renderList,
  };
}
