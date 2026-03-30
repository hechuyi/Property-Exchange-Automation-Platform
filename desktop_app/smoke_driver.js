const fs = require("node:fs");
const path = require("node:path");

const DESKTOP_API_TOKEN_HEADER = "X-PEAP-Desktop-Token";
const TERMINAL_JOB_STATUSES = new Set(["success", "success_with_warnings", "failed", "interrupted"]);
const SMOKE_FETCH_TRACE_KEY = "__PEAP_DESKTOP_SMOKE_FETCH_TRACE";
const SMOKE_INTERACTION_TRACE_KEY = "__PEAP_DESKTOP_SMOKE_INTERACTION_TRACE";
const EMBEDDED_SMOKE_SELECTOR_BRIDGE = {
  nav: {
    workbench: ['[data-testid="desktop-nav-workbench"]'],
    records: ['[data-testid="desktop-nav-records"]'],
    mappings: ['[data-testid="desktop-nav-mappings"]'],
  },
  pages: {
    workbench: ['[data-testid="overview-page"]'],
    records: ['[data-testid="records-page"]'],
    mappings: ['[data-testid="mappings-page"]'],
  },
  actions: {
    triggerManualImport: ["#runManualImportBtn"],
    triggerExport: ["#runExportBtn"],
    importPendingMappings: ["#importPendingMappingBtn"],
    saveDraftMappings: ["#saveDraftMappingsBtn"],
    forceStopCurrentJob: ["#forceStopBtn"],
  },
  mappings: {
    draftItems: [".mapping-draft-item"],
    draftRuleKindField: ['[data-draft-field="ruleKind"]'],
    draftTargetValueField: ['[data-draft-field="targetValue"]'],
  },
  records: {
    stateFilter: ["#recordsStateFilter"],
    projectTypeFilter: ["#recordsProjectTypeFilter"],
    dateFromInput: ["#recordsDateFromInput"],
    dateToInput: ["#recordsDateToInput"],
    keywordInput: ["#recordsKeywordInput"],
  },
};

function sleep(delayMs) {
  return new Promise((resolve) => {
    setTimeout(resolve, delayMs);
  });
}

function errorMessage(error) {
  return String((error && error.message) || error || "unknown smoke failure");
}

function getRequiredSelector(selectors, label) {
  if (!Array.isArray(selectors) || selectors.length === 0) {
    throw new Error(`${label} selector contract missing`);
  }
  return String(selectors[0] || "").trim();
}

async function runStep(report, name, task) {
  try {
    const detail = await task();
    report.steps.push({ name, passed: true, detail });
    return detail;
  } catch (error) {
    report.steps.push({ name, passed: false, error: errorMessage(error) });
    throw error;
  }
}

async function buildSmokeDiagnosticError(actions, error) {
  const fetchTraceResult = await readDiagnosticValue("fetch_trace", actions.readFetchTrace, []);
  const interactionTraceResult = await readDiagnosticValue("interaction_trace", actions.readInteractionTrace, null);
  const fetchTrace = fetchTraceResult.value;
  const interactionTrace = interactionTraceResult.value;
  const traceSuffix = Array.isArray(fetchTrace) && fetchTrace.length
    ? ` fetch_trace=${JSON.stringify(fetchTrace)}`
    : "";
  const interactionSuffix = interactionTrace
    ? ` interaction_trace=${JSON.stringify(interactionTrace)}`
    : "";
  return new Error(
    `${errorMessage(error)}${traceSuffix}${interactionSuffix}${fetchTraceResult.error}${interactionTraceResult.error}`,
  );
}

function hasForceStopMutationEvidence(interactionTrace) {
  if (!interactionTrace || typeof interactionTrace !== "object") {
    return false;
  }
  const forceStop = interactionTrace.forceStop;
  if (!forceStop || typeof forceStop !== "object") {
    return false;
  }
  const mutationEvents = Array.isArray(forceStop.mutationEvents) ? forceStop.mutationEvents : [];
  return mutationEvents.some((event) => {
    const phase = String(event && event.phase || "");
    return phase === "request_started" || phase === "request_succeeded" || phase === "request_failed";
  });
}

async function orchestrateSmoke({
  actions,
  mappingGroupName = "测试集团",
  mappingSourceType = "国资",
  maxMappingPasses = 3,
  nowFn = () => new Date().toISOString(),
  writeReport,
} = {}) {
  const report = {
    ok: false,
    started_at: nowFn(),
    finished_at: "",
    steps: [],
  };
  try {
    await runStep(report, "renderer_ready", async () => actions.waitForRendererReady());
    if (typeof actions.initializeSmokeTracing === "function") {
      await actions.initializeSmokeTracing();
    }

    const manualImportJob = await runStep(report, "manual_import", async () => {
      try {
        const job = await actions.triggerManualImport();
        return actions.waitForJobTerminal(job.job_id);
      } catch (error) {
        throw await buildSmokeDiagnosticError(actions, error);
      }
    });

    let pendingCount = await actions.getPendingMappingsCount();
    if (
      manualImportJob
      && manualImportJob.summary
      && Number.isFinite(Number(manualImportJob.summary.pending_mapping_count))
    ) {
      pendingCount = Math.max(pendingCount, Number(manualImportJob.summary.pending_mapping_count));
    }

    let mappingPass = 0;
    while (pendingCount > 0 && mappingPass < maxMappingPasses) {
      mappingPass += 1;
      const mappingRefreshJob = await runStep(report, `mapping_refresh_${mappingPass}`, async () => {
        await actions.openMappingsPanel();
        await actions.importPendingMappings();
        await actions.fillPendingMappingDrafts({
          groupName: mappingGroupName,
          sourceType: mappingSourceType,
        });
        const job = await actions.saveDraftMappings();
        return actions.waitForJobTerminal(job.job_id);
      });
      pendingCount = await actions.getPendingMappingsCount();
      if (
        mappingRefreshJob
        && mappingRefreshJob.summary
        && Number.isFinite(Number(mappingRefreshJob.summary.pending_mapping_count))
      ) {
        pendingCount = Math.max(pendingCount, Number(mappingRefreshJob.summary.pending_mapping_count));
      }
    }

    if (pendingCount > 0) {
      throw new Error(`pending mappings remain after ${mappingPass} passes: ${pendingCount}`);
    }

    await runStep(report, "export", async () => {
      try {
        if (actions.openRecordsPanel) {
          await actions.openRecordsPanel();
        }
        if (actions.prepareExportScope) {
          await actions.prepareExportScope();
        }
        await actions.openWorkbenchPanel();
        const job = await actions.triggerExport();
        const result = await actions.waitForJobTerminal(job.job_id);
        const artifacts = Array.isArray(result?.summary?.artifacts)
          ? result.summary.artifacts
          : Array.isArray(result?.artifacts)
            ? result.artifacts
            : [];
        if (!artifacts.length) {
          throw new Error("export completed without artifacts");
        }
        return result;
      } catch (error) {
        throw await buildSmokeDiagnosticError(actions, error);
      }
    });

    await runStep(report, "interrupt_restart", async () => {
      try {
        await actions.openWorkbenchPanel();
        const job = await actions.triggerManualImport();
        await actions.waitForJobRunning(job.job_id);
        if (actions.waitForForceStopReady) {
          await actions.waitForForceStopReady();
        }
        await actions.forceStopCurrentJob();
        if (actions.waitForForceStopMutationCompletion) {
          await actions.waitForForceStopMutationCompletion();
        }
        if (actions.waitForBackendReadyAfterRestart) {
          await actions.waitForBackendReadyAfterRestart();
        }
        const result = await actions.waitForJobTerminal(job.job_id);
        if (String(result?.status || "") !== "interrupted") {
          const interactionTrace = typeof actions.readInteractionTrace === "function"
            ? await actions.readInteractionTrace()
            : null;
          if (!hasForceStopMutationEvidence(interactionTrace)) {
            throw new Error(`expected interrupted status, got ${String(result?.status || "")}; force stop mutation evidence missing`);
          }
          throw new Error(`expected interrupted status, got ${String(result?.status || "")}`);
        }
        return result;
      } catch (error) {
        throw await buildSmokeDiagnosticError(actions, error);
      }
    });

    report.ok = true;
    return report;
  } catch (error) {
    report.ok = false;
    report.error = errorMessage(error);
    return report;
  } finally {
    report.finished_at = nowFn();
    if (typeof writeReport === "function") {
      await writeReport(report);
    }
  }
}

async function waitForCondition(
  label,
  evaluate,
  {
    timeoutMs = 30000,
    intervalMs = 250,
    sleepFn = sleep,
  } = {},
) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const result = await evaluate();
    if (result && result.error) {
      throw new Error(String(result.error));
    }
    if (result && result.done) {
      return result.value;
    }
    await sleepFn(intervalMs);
  }
  throw new Error(`${label} timed out after ${timeoutMs}ms`);
}

async function runJavaScript(window, source) {
  const wrappedSource = `(() => {
    try {
      return (${source});
    } catch (error) {
      return {
        __peapExecuteError: {
          message: String((error && error.message) || error || "renderer script failed"),
          stack: String((error && error.stack) || ""),
        },
      };
    }
  })()`;
  const result = await window.webContents.executeJavaScript(wrappedSource, true);
  const executeError = result && typeof result === "object"
    ? result.__peapExecuteError
    : null;
  if (executeError && typeof executeError === "object") {
    const message = String(executeError.message || "renderer script failed");
    const stack = String(executeError.stack || "").trim();
    throw new Error(stack ? `${message} stack=${stack}` : message);
  }
  return result;
}

async function readDiagnosticValue(label, reader, fallbackValue) {
  if (typeof reader !== "function") {
    return { value: fallbackValue, error: "" };
  }
  try {
    return { value: await reader(), error: "" };
  } catch (error) {
    return {
      value: fallbackValue,
      error: ` ${label}_error=${errorMessage(error)}`,
    };
  }
}

function buildElementCenterScript(selector, missingMessage) {
  return `(() => {
    const node = document.querySelector(${JSON.stringify(selector)});
    if (!node) {
      throw new Error(${JSON.stringify(missingMessage)});
    }
    const isDisabled = Boolean(node.disabled)
      || String(node.getAttribute && node.getAttribute("aria-disabled") || "").toLowerCase() === "true";
    if (isDisabled) {
      throw new Error(${JSON.stringify(`${missingMessage} (disabled)`)});
    }
    if (typeof node.scrollIntoView === "function") {
      node.scrollIntoView({ block: "center", inline: "center" });
    }
    if (typeof node.focus === "function") {
      node.focus({ preventScroll: true });
    }
    const rect = typeof node.getBoundingClientRect === "function" ? node.getBoundingClientRect() : null;
    if (!rect || !Number.isFinite(rect.left) || !Number.isFinite(rect.top) || rect.width <= 0 || rect.height <= 0) {
      throw new Error(${JSON.stringify(`${missingMessage} (not visible)`)});
    }
    return {
      x: Math.round(rect.left + rect.width / 2),
      y: Math.round(rect.top + rect.height / 2),
      id: String(node.id || ""),
    };
  })()`;
}

function buildClickSelectorScript(selector, missingMessage) {
  return `(() => {
    const node = document.querySelector(${JSON.stringify(selector)});
    if (!node) {
      throw new Error(${JSON.stringify(missingMessage)});
    }
    node.click();
    return true;
  })()`;
}

function buildButtonSnapshotScript(selector, traceBucket) {
  return `(() => {
    const trace = window[${JSON.stringify(SMOKE_INTERACTION_TRACE_KEY)}];
    const node = document.querySelector(${JSON.stringify(selector)});
    const snapshot = {
      ts: Date.now(),
      found: Boolean(node),
      id: String(node && node.id || ""),
      disabled: Boolean(node && node.disabled)
        || String(node && node.getAttribute && node.getAttribute("aria-disabled") || "").toLowerCase() === "true",
      ariaDisabled: String(node && node.getAttribute && node.getAttribute("aria-disabled") || ""),
      className: String(node && node.className || ""),
    };
    if (trace && typeof trace === "object") {
      const bucket = trace[${JSON.stringify(traceBucket)}];
      if (bucket && typeof bucket === "object") {
        const buttonSnapshots = Array.isArray(bucket.buttonSnapshots)
          ? bucket.buttonSnapshots
          : [];
        if (!Array.isArray(bucket.buttonSnapshots)) {
          bucket.buttonSnapshots = buttonSnapshots;
        }
        buttonSnapshots.push(snapshot);
      }
    }
    return snapshot;
  })()`;
}

async function clickByInputEvent({ window, selector, missingMessage }) {
  const webContents = window && window.webContents;
  if (!webContents || typeof webContents.sendInputEvent !== "function") {
    throw new Error("force stop trigger requires webContents.sendInputEvent for trusted input injection");
  }
  const target = await runJavaScript(window, buildElementCenterScript(selector, missingMessage));
  const x = Number(target && target.x);
  const y = Number(target && target.y);
  if (!Number.isFinite(x) || !Number.isFinite(y)) {
    throw new Error(`${missingMessage} (invalid click coordinates)`);
  }
  webContents.sendInputEvent({ type: "mouseMove", x, y, movementX: 0, movementY: 0 });
  webContents.sendInputEvent({ type: "mouseDown", x, y, button: "left", clickCount: 1 });
  webContents.sendInputEvent({ type: "mouseUp", x, y, button: "left", clickCount: 1 });
}

async function callBackendApi({
  backendUrl,
  apiToken,
  apiPath,
  method = "GET",
  body = undefined,
  fetchFn = globalThis.fetch,
}) {
  const response = await fetchFn(`${backendUrl}${apiPath}`, {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(apiToken ? { [DESKTOP_API_TOKEN_HEADER]: apiToken } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(String(payload.error || `HTTP ${response.status}`));
  }
  return payload;
}

function buildSmokeActions({
  window,
  backendUrl,
  apiToken,
  fetchFn = globalThis.fetch,
  sleepFn = sleep,
}) {
  async function listJobs(limit = 20) {
    const payload = await callBackendApi({
      backendUrl,
      apiToken,
      apiPath: `/api/jobs?limit=${limit}`,
      fetchFn,
    });
    return Array.isArray(payload.jobs) ? payload.jobs : [];
  }

  async function getPendingMappingsCount() {
    const payload = await callBackendApi({
      backendUrl,
      apiToken,
      apiPath: "/api/mappings",
      fetchFn,
    });
    const pendingEnvelope = payload.pending;
    if (pendingEnvelope && typeof pendingEnvelope === "object") {
      const totalCount = Number(pendingEnvelope.total_count);
      if (Number.isFinite(totalCount)) {
        return totalCount;
      }
      const items = Array.isArray(pendingEnvelope.items) ? pendingEnvelope.items : [];
      return items.length;
    }
    if (Array.isArray(payload.pending)) {
      return payload.pending.length;
    }
    return 0;
  }

  async function waitForNewJob(jobType, previousIds) {
    return waitForCondition(`new ${jobType} job`, async () => {
      const jobs = await listJobs();
      const job = jobs.find(
        (item) => String(item.job_type || "") === jobType && !previousIds.has(String(item.job_id || "")),
      );
      return job
        ? { done: true, value: { job_id: String(job.job_id || "") } }
        : { done: false };
    }, { timeoutMs: 45000, intervalMs: 300, sleepFn });
  }

  async function waitForJobStatus(jobId, { expectedRunning = false } = {}) {
    return waitForCondition(`job ${jobId} ${expectedRunning ? "running" : "terminal"}`, async () => {
      const payload = await callBackendApi({
        backendUrl,
        apiToken,
        apiPath: `/api/jobs/${jobId}`,
        fetchFn,
      });
      const status = String(payload.status || "");
      if (expectedRunning) {
        if (status === "running") {
          return { done: true, value: payload };
        }
        if (TERMINAL_JOB_STATUSES.has(status)) {
          return { done: false, error: `job ${jobId} reached terminal status ${status} before running` };
        }
        return { done: false };
      }
      return TERMINAL_JOB_STATUSES.has(status)
        ? { done: true, value: payload }
        : { done: false };
    }, { timeoutMs: 60000, intervalMs: 300, sleepFn });
  }

  async function clickAndCaptureNewJob({ jobType, script }) {
    const previousIds = new Set(
      (await listJobs()).map((item) => String(item.job_id || "")).filter(Boolean),
    );
    await runJavaScript(window, script);
    return waitForNewJob(jobType, previousIds);
  }

  async function initializeSmokeTracing() {
    return runJavaScript(window, `(() => {
      if (!Array.isArray(window[${JSON.stringify(SMOKE_FETCH_TRACE_KEY)}])) {
        window[${JSON.stringify(SMOKE_FETCH_TRACE_KEY)}] = [];
      }
      const existingTrace = window[${JSON.stringify(SMOKE_INTERACTION_TRACE_KEY)}];
      const trace = existingTrace && typeof existingTrace === "object"
        ? existingTrace
        : {};
      if (!trace.manualImport || typeof trace.manualImport !== "object") {
        trace.manualImport = {};
      }
      if (!trace.forceStop || typeof trace.forceStop !== "object") {
        trace.forceStop = {};
      }
      if (!Array.isArray(trace.windowErrors)) {
        trace.windowErrors = [];
      }
      const ensureBucket = (bucket) => {
        if (!Array.isArray(bucket.buttonSnapshots)) {
          bucket.buttonSnapshots = [];
        }
        if (!Array.isArray(bucket.clickEvents)) {
          bucket.clickEvents = [];
        }
        if (!Array.isArray(bucket.mutationEvents)) {
          bucket.mutationEvents = [];
        }
      };
      ensureBucket(trace.manualImport);
      ensureBucket(trace.forceStop);
      window[${JSON.stringify(SMOKE_INTERACTION_TRACE_KEY)}] = trace;
      if (!window.__PEAP_DESKTOP_SMOKE_ERROR_LISTENER_ATTACHED) {
        window.addEventListener("error", (event) => {
          const targetTrace = window[${JSON.stringify(SMOKE_INTERACTION_TRACE_KEY)}];
          if (!targetTrace || typeof targetTrace !== "object") {
            return;
          }
          const windowErrors = Array.isArray(targetTrace.windowErrors)
            ? targetTrace.windowErrors
            : [];
          if (!Array.isArray(targetTrace.windowErrors)) {
            targetTrace.windowErrors = windowErrors;
          }
          windowErrors.push({
            ts: Date.now(),
            message: String(event.message || ""),
            filename: String(event.filename || ""),
          });
        });
        window.__PEAP_DESKTOP_SMOKE_ERROR_LISTENER_ATTACHED = true;
      }
      return true;
    })()`);
  }

  async function waitForPageMount(pageSelectors, label) {
    const selector = getRequiredSelector(pageSelectors, `${label} page`);
    return waitForCondition(`${label} page mount`, async () => {
      const ready = await runJavaScript(window, `(() => {
        return Boolean(document.querySelector(${JSON.stringify(selector)}));
      })()`);
      return ready ? { done: true, value: true } : { done: false };
    }, { timeoutMs: 10000, intervalMs: 100, sleepFn });
  }

  async function openPanel({ navSelectors, navMissingMessage, pageSelectors, pageLabel }) {
    await runJavaScript(
      window,
      buildClickSelectorScript(getRequiredSelector(navSelectors, `${pageLabel} nav`), navMissingMessage),
    );
    await waitForPageMount(pageSelectors, pageLabel);
  }

  return {
    initializeSmokeTracing,
    waitForRendererReady: async () => waitForCondition(
      "renderer bootstrap",
      async () => {
        const state = await runJavaScript(window, `(() => {
          const value = window.__PEAP_DESKTOP_BOOTSTRAP_STATE || {};
          return { ready: Boolean(value.ready), error: String(value.error || "") };
        })()`);
        if (state && state.error) {
          return { done: false, error: state.error };
        }
        return state && state.ready
          ? { done: true, value: "renderer-ready" }
          : { done: false };
      },
      { timeoutMs: 60000, intervalMs: 300, sleepFn },
    ),
    triggerManualImport: async () => clickAndCaptureNewJob({
      jobType: "manual_import",
      script: buildClickSelectorScript(
        getRequiredSelector(EMBEDDED_SMOKE_SELECTOR_BRIDGE.actions.triggerManualImport, "manual import action"),
        "runManualImportBtn missing",
      ),
    }),
    readFetchTrace: async () => runJavaScript(window, `(() => {
      const trace = window[${JSON.stringify(SMOKE_FETCH_TRACE_KEY)}];
      return Array.isArray(trace) ? trace : [];
    })()`),
    readInteractionTrace: async () => runJavaScript(window, `(() => {
      const trace = window[${JSON.stringify(SMOKE_INTERACTION_TRACE_KEY)}];
      return trace && typeof trace === "object" ? trace : null;
    })()`),
    waitForJobTerminal: async (jobId) => waitForJobStatus(jobId, { expectedRunning: false }),
    waitForJobRunning: async (jobId) => waitForJobStatus(jobId, { expectedRunning: true }),
    waitForForceStopReady: async () => {
      const selector = getRequiredSelector(
        EMBEDDED_SMOKE_SELECTOR_BRIDGE.actions.forceStopCurrentJob,
        "force stop action",
      );
      return waitForCondition("force stop button enabled", async () => {
        const snapshot = await runJavaScript(
          window,
          buildButtonSnapshotScript(selector, "forceStop"),
        );
        if (!snapshot || !snapshot.found) {
          return { done: false };
        }
        return snapshot.disabled
          ? { done: false }
          : { done: true, value: snapshot };
      }, { timeoutMs: 10000, intervalMs: 250, sleepFn });
    },
    waitForForceStopMutationCompletion: async () => waitForCondition(
      "force stop mutation completion",
      async () => {
        const interactionTrace = await runJavaScript(window, `(() => {
          const trace = window[${JSON.stringify(SMOKE_INTERACTION_TRACE_KEY)}];
          return trace && typeof trace === "object" ? trace : null;
        })()`);
        const forceStop = interactionTrace && typeof interactionTrace === "object"
          ? interactionTrace.forceStop
          : null;
        const mutationEvents = forceStop && typeof forceStop === "object" && Array.isArray(forceStop.mutationEvents)
          ? forceStop.mutationEvents
          : [];
        const failureEvent = mutationEvents.find((event) => String(event && event.phase || "") === "request_failed");
        if (failureEvent) {
          return {
            done: false,
            error: String(failureEvent.message || "force stop mutation failed"),
          };
        }
        const successEvent = mutationEvents.find((event) => String(event && event.phase || "") === "request_succeeded");
        return successEvent
          ? { done: true, value: successEvent }
          : { done: false };
      },
      { timeoutMs: 60000, intervalMs: 250, sleepFn },
    ),
    getPendingMappingsCount,
    openMappingsPanel: async () => openPanel({
      navSelectors: EMBEDDED_SMOKE_SELECTOR_BRIDGE.nav.mappings,
      navMissingMessage: "mappings panel button missing",
      pageSelectors: EMBEDDED_SMOKE_SELECTOR_BRIDGE.pages.mappings,
      pageLabel: "mappings",
    }),
    importPendingMappings: async () => {
      await runJavaScript(window, `(() => {
        const node = document.getElementById("importPendingMappingBtn");
        if (!node) {
          throw new Error("importPendingMappingBtn missing");
        }
        node.click();
        return true;
      })()`);
      return waitForCondition("pending mapping drafts", async () => {
        const count = Number(await runJavaScript(window, `(() => {
          return document.querySelectorAll(".mapping-draft-item").length;
        })()`));
        return count > 0
          ? { done: true, value: count }
          : { done: false };
      }, { timeoutMs: 10000, intervalMs: 100, sleepFn });
    },
    fillPendingMappingDrafts: async ({ groupName, sourceType }) => runJavaScript(window, `(() => {
      const descriptor = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value");
      const setInputValue = descriptor && descriptor.set;
      if (typeof setInputValue !== "function") {
        throw new Error("input value setter unavailable");
      }
      const drafts = [...document.querySelectorAll(".mapping-draft-item")];
      drafts.forEach((draft) => {
        const ruleKindNode = draft.querySelector('[data-draft-field="ruleKind"]');
        const targetNode = draft.querySelector('[data-draft-field="targetValue"]');
        if (!ruleKindNode || !targetNode) {
          return;
        }
        const ruleKind = String(ruleKindNode.value || "");
        const nextValue = ruleKind.endsWith("_group") ? ${JSON.stringify(groupName)} : ${JSON.stringify(sourceType)};
        setInputValue.call(targetNode, nextValue);
        targetNode.dispatchEvent(new Event("input", { bubbles: true }));
        targetNode.dispatchEvent(new Event("change", { bubbles: true }));
      });
      return drafts.length;
    })()`),
    saveDraftMappings: async () => clickAndCaptureNewJob({
      jobType: "mapping_refresh",
      script: `(() => {
        const node = document.getElementById("saveDraftMappingsBtn");
        if (!node) {
          throw new Error("saveDraftMappingsBtn missing");
        }
        node.click();
      })()`,
    }),
    openWorkbenchPanel: async () => openPanel({
      navSelectors: EMBEDDED_SMOKE_SELECTOR_BRIDGE.nav.workbench,
      navMissingMessage: "workbench panel button missing",
      pageSelectors: EMBEDDED_SMOKE_SELECTOR_BRIDGE.pages.workbench,
      pageLabel: "workbench",
    }),
    openRecordsPanel: async () => openPanel({
      navSelectors: EMBEDDED_SMOKE_SELECTOR_BRIDGE.nav.records,
      navMissingMessage: "records panel button missing",
      pageSelectors: EMBEDDED_SMOKE_SELECTOR_BRIDGE.pages.records,
      pageLabel: "records",
    }),
    prepareExportScope: async () => runJavaScript(window, `(() => {
      const setValue = (id, value) => {
        const node = document.getElementById(id);
        if (!node) {
          throw new Error(id + " missing");
        }
        node.value = value;
        node.dispatchEvent(new Event("change", { bubbles: true }));
      };
      setValue("recordsStateFilter", "all");
      setValue("recordsProjectTypeFilter", "all");
      setValue("recordsDateFromInput", "");
      setValue("recordsDateToInput", "");
      const keywordNode = document.getElementById("recordsKeywordInput");
      if (!keywordNode) {
        throw new Error("recordsKeywordInput missing");
      }
      keywordNode.value = "";
      keywordNode.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    })()`),
    triggerExport: async () => clickAndCaptureNewJob({
      jobType: "export_excel",
      script: buildClickSelectorScript(
        getRequiredSelector(EMBEDDED_SMOKE_SELECTOR_BRIDGE.actions.triggerExport, "export action"),
        "runExportBtn missing",
      ),
    }),
    forceStopCurrentJob: async () => {
      await runJavaScript(window, `(() => {
        window.confirm = () => true;
        return true;
      })()`);
      await clickByInputEvent({
        window,
        selector: getRequiredSelector(EMBEDDED_SMOKE_SELECTOR_BRIDGE.actions.forceStopCurrentJob, "force stop action"),
        missingMessage: "forceStopBtn missing",
      });
      return true;
    },
    waitForBackendReadyAfterRestart: async () => waitForCondition(
      "backend ready after restart",
      async () => {
        try {
          await callBackendApi({
            backendUrl,
            apiToken,
            apiPath: "/api/ready",
            fetchFn,
          });
          return { done: true, value: "backend-ready" };
        } catch (error) {
          return { done: false };
        }
      },
      { timeoutMs: 60000, intervalMs: 300, sleepFn },
    ),
  };
}

function formatSmokeReportMarkdown(report) {
  const lines = [
    "# Electron Smoke Report",
    "",
    `- started_at: ${report.started_at || ""}`,
    `- finished_at: ${report.finished_at || ""}`,
    `- ok: ${report.ok ? "true" : "false"}`,
    "",
    "## Steps",
    "",
  ];
  for (const step of report.steps || []) {
    if (step.passed) {
      lines.push(`- [x] ${step.name}`);
      lines.push(`  - detail: \`${JSON.stringify(step.detail || {})}\``);
    } else {
      lines.push(`- [ ] ${step.name}`);
      lines.push(`  - error: ${step.error || ""}`);
    }
  }
  if (report.error) {
    lines.push("");
    lines.push("## Error");
    lines.push("");
    lines.push(report.error);
  }
  lines.push("");
  return lines.join("\n");
}

async function writeReportFile(reportPath, report) {
  if (!reportPath) {
    return;
  }
  fs.mkdirSync(path.dirname(reportPath), { recursive: true });
  if (String(reportPath).toLowerCase().endsWith(".md")) {
    fs.writeFileSync(reportPath, formatSmokeReportMarkdown(report), "utf8");
    return;
  }
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2), "utf8");
}

async function runDesktopSmoke(options = {}) {
  if (options && options.actions) {
    return orchestrateSmoke(options);
  }
  const {
    window,
    backendUrl,
    apiToken,
    reportPath = "",
    fetchFn = globalThis.fetch,
    sleepFn = sleep,
    nowFn = () => new Date().toISOString(),
    mappingGroupName = "测试集团",
    mappingSourceType = "国资",
  } = options || {};
  const actions = buildSmokeActions({
    window,
    backendUrl,
    apiToken,
    fetchFn,
    sleepFn,
  });
  return orchestrateSmoke({
    actions,
    mappingGroupName,
    mappingSourceType,
    nowFn,
    writeReport: (report) => writeReportFile(reportPath, report),
  });
}

module.exports = {
  runDesktopSmoke,
  runJavaScript,
  __internal: {
    buildSmokeActions,
    EMBEDDED_SMOKE_SELECTOR_BRIDGE,
  },
};
