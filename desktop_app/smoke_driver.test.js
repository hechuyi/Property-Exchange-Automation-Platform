const test = require("node:test");
const assert = require("node:assert/strict");

const { runDesktopSmoke } = require("./smoke_driver");

test("runDesktopSmoke orchestrates manual import mapping export and interrupt recovery", async () => {
  const steps = [];
  const pendingCounts = [1, 1, 0];
  let manualImportCalls = 0;
  let mappingSaveCalls = 0;

  const report = await runDesktopSmoke({
    actions: {
      waitForRendererReady: async () => {
        steps.push("ready");
      },
      triggerManualImport: async () => {
        manualImportCalls += 1;
        return { job_id: `manual-${manualImportCalls}` };
      },
      waitForJobTerminal: async (jobId) => {
        steps.push(`terminal:${jobId}`);
        if (jobId === "manual-1") {
          return { job_id: jobId, status: "success_with_warnings", summary: { pending_mapping_count: 1 } };
        }
        if (jobId === "map-1" || jobId === "map-2") {
          return { job_id: jobId, status: "success" };
        }
        if (jobId === "export-1") {
          return { job_id: jobId, status: "success", summary: { artifacts: ["out.xlsx"] } };
        }
        if (jobId === "manual-2") {
          return { job_id: jobId, status: "interrupted" };
        }
        throw new Error(`unexpected terminal wait: ${jobId}`);
      },
      getPendingMappingsCount: async () => pendingCounts.shift() ?? 0,
      openMappingsPanel: async () => {
        steps.push("open-mappings");
      },
      importPendingMappings: async () => {
        steps.push("import-pending");
      },
      fillPendingMappingDrafts: async ({ groupName, sourceType }) => {
        steps.push(`fill-drafts:${groupName}:${sourceType}`);
      },
      saveDraftMappings: async () => {
        mappingSaveCalls += 1;
        return { job_id: `map-${mappingSaveCalls}` };
      },
      openOverviewPanel: async () => {
        steps.push("open-overview");
      },
      prepareExportScope: async () => {
        steps.push("prepare-export");
      },
      triggerExport: async () => ({ job_id: "export-1" }),
      waitForJobRunning: async (jobId) => {
        steps.push(`running:${jobId}`);
        return { job_id: jobId, status: "running" };
      },
      forceStopCurrentJob: async () => {
        steps.push("force-stop");
      },
      readInteractionTrace: async () => ({
        forceStop: {
          mutationEvents: [{ ts: 1, phase: "request_started" }],
        },
      }),
      waitForBackendReadyAfterRestart: async () => {
        steps.push("backend-ready");
      },
    },
    mappingGroupName: "测试集团",
    mappingSourceType: "国资",
  });

  assert.equal(report.ok, true);
  assert.deepEqual(
    report.steps.map((item) => item.name),
    [
      "renderer_ready",
      "manual_import",
      "mapping_refresh_1",
      "mapping_refresh_2",
      "export",
      "interrupt_restart",
    ],
  );
  assert.match(JSON.stringify(steps), /fill-drafts:测试集团:国资/);
});

test("runDesktopSmoke interrupt restart fails explicitly when stop evidence is missing", async () => {
  let manualImportCalls = 0;

  const report = await runDesktopSmoke({
    actions: {
      waitForRendererReady: async () => {},
      triggerManualImport: async () => {
        manualImportCalls += 1;
        return { job_id: manualImportCalls === 1 ? "manual-initial" : "manual-restart" };
      },
      waitForJobTerminal: async (jobId) => {
        if (jobId === "manual-initial") {
          return { job_id: jobId, status: "success_with_warnings", summary: { pending_mapping_count: 0 } };
        }
        if (jobId === "export-1") {
          return { job_id: jobId, status: "success", summary: { artifacts: ["out.xlsx"] } };
        }
        if (jobId === "manual-restart") {
          return { job_id: jobId, status: "success" };
        }
        throw new Error(`unexpected terminal wait: ${jobId}`);
      },
      getPendingMappingsCount: async () => 0,
      openMappingsPanel: async () => {},
      importPendingMappings: async () => {},
      fillPendingMappingDrafts: async () => {},
      saveDraftMappings: async () => ({ job_id: "map-1" }),
      openOverviewPanel: async () => {},
      triggerExport: async () => ({ job_id: "export-1" }),
      waitForJobRunning: async () => ({ status: "running" }),
      forceStopCurrentJob: async () => {},
      readFetchTrace: async () => [{ url: "/api/jobs/manual-import", method: "POST", status: 202, ok: true }],
      readInteractionTrace: async () => ({ manualImport: { clickEvents: [{ ts: 1, trusted: true }] } }),
    },
  });

  assert.equal(report.ok, false);
  const interruptStep = report.steps.find((item) => item.name === "interrupt_restart");
  assert.ok(interruptStep);
  assert.match(String(interruptStep.error || ""), /force stop mutation evidence missing/);
  assert.match(String(interruptStep.error || ""), /fetch_trace=/);
  assert.match(String(interruptStep.error || ""), /interaction_trace=/);
});

test("runDesktopSmoke interrupt restart fails explicitly when job reaches terminal before running", async () => {
  let manualImportCalls = 0;

  const report = await runDesktopSmoke({
    actions: {
      waitForRendererReady: async () => {},
      triggerManualImport: async () => {
        manualImportCalls += 1;
        return { job_id: manualImportCalls === 1 ? "manual-initial" : "manual-fast" };
      },
      waitForJobTerminal: async (jobId) => {
        if (jobId === "manual-initial") {
          return { job_id: jobId, status: "success_with_warnings", summary: { pending_mapping_count: 0 } };
        }
        if (jobId === "export-1") {
          return { job_id: jobId, status: "success", summary: { artifacts: ["out.xlsx"] } };
        }
        if (jobId === "manual-fast") {
          return { job_id: jobId, status: "success" };
        }
        throw new Error(`unexpected terminal wait: ${jobId}`);
      },
      getPendingMappingsCount: async () => 0,
      openMappingsPanel: async () => {},
      importPendingMappings: async () => {},
      fillPendingMappingDrafts: async () => {},
      saveDraftMappings: async () => ({ job_id: "map-1" }),
      openOverviewPanel: async () => {},
      triggerExport: async () => ({ job_id: "export-1" }),
      waitForJobRunning: async (jobId) => {
        throw new Error(`job ${jobId} reached terminal status success before running`);
      },
      forceStopCurrentJob: async () => {
        throw new Error("force stop should not run");
      },
      readFetchTrace: async () => [{ url: "/api/jobs/manual-import", method: "POST", status: 202, ok: true }],
      readInteractionTrace: async () => ({ manualImport: { clickEvents: [{ ts: 1, trusted: true }] } }),
    },
  });

  assert.equal(report.ok, false);
  const interruptStep = report.steps.find((item) => item.name === "interrupt_restart");
  assert.ok(interruptStep);
  assert.match(String(interruptStep.error || ""), /reached terminal status success before running/);
  assert.match(String(interruptStep.error || ""), /fetch_trace=/);
  assert.match(String(interruptStep.error || ""), /interaction_trace=/);
});
