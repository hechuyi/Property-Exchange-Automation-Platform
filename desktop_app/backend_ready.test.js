const test = require("node:test");
const assert = require("node:assert/strict");

const { DEFAULT_READY_PATH, waitForBackend } = require("./backend_ready");

test("waitForBackend probes the lightweight ready endpoint by default", async () => {
  const requestedUrls = [];
  let now = 0;
  let attempts = 0;

  await waitForBackend("http://127.0.0.1:42679", {
    intervalMs: 0,
    timeoutMs: 20,
    nowFn: () => now,
    sleepFn: async () => {
      now += 1;
    },
    fetchFn: async (url) => {
      requestedUrls.push(url);
      attempts += 1;
      if (attempts < 3) {
        throw new Error("not ready");
      }
      return { ok: true };
    },
  });

  assert.deepEqual(requestedUrls, [
    `http://127.0.0.1:42679${DEFAULT_READY_PATH}`,
    `http://127.0.0.1:42679${DEFAULT_READY_PATH}`,
    `http://127.0.0.1:42679${DEFAULT_READY_PATH}`,
  ]);
});

test("waitForBackend stops immediately when the backend process reports a startup failure", async () => {
  const startupFailure = new Error("backend exited");
  let now = 0;

  await assert.rejects(
    waitForBackend("http://127.0.0.1:42679", {
      intervalMs: 0,
      timeoutMs: 20,
      nowFn: () => now,
      sleepFn: async () => {
        now += 1;
      },
      fetchFn: async () => {
        throw new Error("connection refused");
      },
      getFailure: () => startupFailure,
    }),
    /backend exited/,
  );
});

test("waitForBackend times out cleanly when the backend never becomes reachable", async () => {
  let now = 0;

  await assert.rejects(
    waitForBackend("http://127.0.0.1:42679", {
      intervalMs: 0,
      timeoutMs: 3,
      nowFn: () => now,
      sleepFn: async () => {
        now += 1;
      },
      fetchFn: async () => {
        throw new Error("connection refused");
      },
    }),
    /did not become ready within 3ms/,
  );
});

test("waitForBackend forwards desktop token headers to readiness probes", async () => {
  const fetchCalls = [];

  await waitForBackend("http://127.0.0.1:42679", {
    timeoutMs: 5,
    intervalMs: 0,
    headers: { "X-PEAP-Desktop-Token": "desktop-secret" },
    fetchFn: async (url, options) => {
      fetchCalls.push([url, options]);
      return { ok: true };
    },
  });

  assert.deepEqual(fetchCalls, [
    [
      `http://127.0.0.1:42679${DEFAULT_READY_PATH}`,
      { headers: { "X-PEAP-Desktop-Token": "desktop-secret" } },
    ],
  ]);
});
