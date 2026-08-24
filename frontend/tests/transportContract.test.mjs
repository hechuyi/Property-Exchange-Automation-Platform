import test from "node:test";
import assert from "node:assert/strict";

import { API } from "../api.js";

function createJsonResponse({ ok, status, payload, contentType = "application/json; charset=utf-8" }) {
  return {
    ok,
    status,
    headers: {
      get(name) {
        return String(name || "").toLowerCase() === "content-type" ? contentType : null;
      },
    },
    async json() {
      return payload;
    },
  };
}

function setFetchMock(t, factory) {
  const previousFetch = globalThis.fetch;
  t.after(() => {
    if (previousFetch) {
      globalThis.fetch = previousFetch;
      return;
    }
    delete globalThis.fetch;
  });
  globalThis.fetch = async () => factory();
}

test("API.request unwraps canonical success transport envelope", { concurrency: false }, async (t) => {
  setFetchMock(t, () => createJsonResponse({
    ok: true,
    status: 200,
    payload: {
      ok: true,
      data: {
        jobs: [{ job_id: "job-1" }],
      },
      meta: {
        request_id: "req-1",
      },
    },
  }));

  const payload = await API.request("/api/jobs");

  assert.deepEqual(payload, {
    jobs: [{ job_id: "job-1" }],
  });
});

test("API.request rejects 2xx payloads without transport envelope", { concurrency: false }, async (t) => {
  setFetchMock(t, () => createJsonResponse({
    ok: true,
    status: 200,
    payload: {
      jobs: [{ job_id: "job-1" }],
    },
  }));

  await assert.rejects(
    API.request("/api/jobs"),
    (error) => {
      assert.equal(error.name, "TransportContractError");
      assert.equal(error.code, "transport_contract_violation");
      assert.equal(error.status, 200);
      assert.match(error.message, /invalid success transport envelope/);
      assert.deepEqual(error.payload, {
        jobs: [{ job_id: "job-1" }],
      });
      return true;
    },
  );
});

test("API.request rejects flat 4xx error payloads", { concurrency: false }, async (t) => {
  setFetchMock(t, () => createJsonResponse({
    ok: false,
    status: 400,
    payload: {
      error_code: "invalid_request",
      message: "legacy flat error",
    },
  }));

  await assert.rejects(
    API.request("/api/jobs/manual-import"),
    (error) => {
      assert.equal(error.name, "TransportContractError");
      assert.equal(error.code, "transport_contract_violation");
      assert.equal(error.status, 400);
      assert.match(error.message, /invalid error transport envelope/);
      assert.deepEqual(error.payload, {
        error_code: "invalid_request",
        message: "legacy flat error",
      });
      return true;
    },
  );
});

test("API.request surfaces canonical error envelopes without fallback parsing", { concurrency: false }, async (t) => {
  setFetchMock(t, () => createJsonResponse({
    ok: false,
    status: 409,
    payload: {
      ok: false,
      error: {
        code: "mutating_job_in_progress",
        message: "已有执行中的任务：一键执行",
        details: {
          active_job_type: "one_click",
        },
      },
    },
  }));

  await assert.rejects(
    API.request("/api/jobs/manual-import"),
    (error) => {
      assert.equal(error.name, "ApiError");
      assert.equal(error.code, "mutating_job_in_progress");
      assert.equal(error.status, 409);
      assert.equal(error.message, "已有执行中的任务：一键执行");
      assert.deepEqual(error.details, {
        active_job_type: "one_click",
      });
      assert.deepEqual(error.payload, {
        ok: false,
        error: {
          code: "mutating_job_in_progress",
          message: "已有执行中的任务：一键执行",
          details: {
            active_job_type: "one_click",
          },
        },
      });
      return true;
    },
  );
});