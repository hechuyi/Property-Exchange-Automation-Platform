function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function asIssues(value) {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item) => item && typeof item === "object")
    .map((item) => ({
      code: String(item.code || "").trim(),
      severity: String(item.severity || "").trim(),
      message: String(item.message || "").trim(),
    }));
}

export function normalizeRuntimeResource(resource = {}) {
  const source = asObject(resource);
  const browser = asObject(source.browser);
  const install = asObject(source.install);
  const readiness = asObject(source.readiness);
  return {
    browser: {
      installed: Boolean(browser.installed),
      browser_name: String(browser.browser_name || "").trim(),
      installation_source: String(browser.installation_source || "").trim(),
      error: String(browser.error || "").trim(),
    },
    install: {
      status: String(install.status || "").trim(),
      browser_name: String(install.browser_name || "").trim(),
      trigger: String(install.trigger || "").trim(),
      attempt_count: Number.parseInt(install.attempt_count, 10) || 0,
      started_at: String(install.started_at || "").trim(),
      updated_at: String(install.updated_at || "").trim(),
      completed_at: String(install.completed_at || "").trim(),
      message: String(install.message || "").trim(),
      running: Boolean(install.running),
    },
    readiness: {
      ready: Boolean(readiness.ready),
      download_ready: Boolean(readiness.download_ready),
      browser_runtime_ready: Boolean(readiness.browser_runtime_ready),
      issues: asIssues(readiness.issues),
    },
  };
}
