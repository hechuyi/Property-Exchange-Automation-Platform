const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

async function loadApiModule() {
  const moduleUrl = pathToFileURL(path.join(__dirname, "api.mjs")).href;
  return import(moduleUrl);
}

test("createApiClient injects desktop token headers by default", async () => {
  const { createApiClient } = await loadApiModule();
  const fetchCalls = [];
  const api = createApiClient({
    baseUrl: "http://127.0.0.1:42679",
    apiToken: "desktop-secret",
    fetchFn: async (url, options) => {
      fetchCalls.push([url, options]);
      return {
        ok: true,
        json: async () => ({ ok: true, mode: "overview" }),
      };
    },
  });

  const payload = await api("/api/overview");

  assert.deepEqual(payload, { ok: true, mode: "overview" });
  assert.deepEqual(fetchCalls, [
    [
      "http://127.0.0.1:42679/api/overview",
      {
        headers: {
          "Content-Type": "application/json",
          "X-PEAP-Desktop-Token": "desktop-secret",
        },
      },
    ],
  ]);
});
