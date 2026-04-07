/* ── API Client ── */
const API = {
  base: "",

  /* GET /api/overview */
  async getOverview() {
    const res = await fetch(`${this.base}/api/overview`);
    if (!res.ok) throw new Error(`Overview failed: ${res.status}`);
    return res.json();
  },

  /* GET /api/jobs?limit=N */
  async listJobs(limit = 50) {
    const res = await fetch(`${this.base}/api/jobs?limit=${limit}`);
    if (!res.ok) throw new Error(`Jobs failed: ${res.status}`);
    return res.json();
  },

  /* GET /api/jobs/{job_id} */
  async getJob(jobId) {
    const res = await fetch(`${this.base}/api/jobs/${encodeURIComponent(jobId)}`);
    if (!res.ok) throw new Error(`Get job failed: ${res.status}`);
    return res.json();
  },

  /* GET /api/jobs/{job_id}/events?limit=N */
  async getJobEvents(jobId, limit = 200) {
    const res = await fetch(`${this.base}/api/jobs/${encodeURIComponent(jobId)}/events?limit=${limit}`);
    if (!res.ok) throw new Error(`Job events failed: ${res.status}`);
    return res.json();
  },

  /* GET /api/records — uses query params */
  async listRecords(params = {}) {
    const p = new URLSearchParams();
    if (params.record_family) p.set("record_family", params.record_family);
    if (params.state) p.set("state", params.state);
    if (params.project_type) p.set("project_type", params.project_type);
    if (params.keyword) p.set("keyword", params.keyword);
    if (params.date_from) p.set("date_from", params.date_from);
    if (params.date_to) p.set("date_to", params.date_to);
    if (params.page) p.set("page", String(params.page));
    if (params.page_size) p.set("page_size", String(params.page_size));
    const res = await fetch(`${this.base}/api/records?${p}`);
    if (!res.ok) throw new Error(`Records failed: ${res.status}`);
    return res.json();
  },

  /* POST /api/jobs/one-click */
  async runOneClick(payload = {}) {
    const res = await fetch(`${this.base}/api/jobs/one-click`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`One-click failed: ${res.status}`);
    return res.json();
  },

  /* POST /api/jobs/manual-import */
  async runManualImport(inputDir) {
    const res = await fetch(`${this.base}/api/jobs/manual-import`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ input_dir: inputDir }),
    });
    if (!res.ok) throw new Error(`Manual import failed: ${res.status}`);
    return res.json();
  },

  /* POST /api/exports */
  async runExport(scope = "listing", mode = "rebuild") {
    const res = await fetch(`${this.base}/api/exports`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope: { record_family: scope }, mode }),
    });
    if (!res.ok) throw new Error(`Export failed: ${res.status}`);
    return res.json();
  },

  /* GET /api/mappings */
  async listMappings() {
    const res = await fetch(`${this.base}/api/mappings`);
    if (!res.ok) throw new Error(`Mappings failed: ${res.status}`);
    return res.json();
  },

  /* POST /api/mappings */
  async saveMapping(draft) {
    const res = await fetch(`${this.base}/api/mappings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(draft),
    });
    if (!res.ok) throw new Error(`Save mapping failed: ${res.status}`);
    return res.json();
  },

  /* POST /api/mappings/reprocess-pending */
  async reprocessPendingMappings() {
    const res = await fetch(`${this.base}/api/mappings/reprocess-pending`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    if (!res.ok) throw new Error(`Reprocess failed: ${res.status}`);
    return res.json();
  },

  /* GET /api/settings/basic */
  async getSettingsBasic() {
    const res = await fetch(`${this.base}/api/settings/basic`);
    if (!res.ok) throw new Error(`Settings failed: ${res.status}`);
    return res.json();
  },

  /* POST /api/runtime/install-browser */
  async installBrowser() {
    const res = await fetch(`${this.base}/api/runtime/install-browser`, { method: "POST" });
    if (!res.ok) throw new Error(`Install failed: ${res.status}`);
    return res.json();
  },
};
