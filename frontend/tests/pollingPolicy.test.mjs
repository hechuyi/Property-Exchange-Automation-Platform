import test from "node:test";
import assert from "node:assert/strict";

import { shouldAutoPollPanel, pollDelayForPanel } from "../src/state/pollingPolicy.js";

test("shouldAutoPollPanel only keeps polling while there is an active job", () => {
  // overview: only when active job
  assert.equal(shouldAutoPollPanel("overview", {}), false);
  assert.equal(shouldAutoPollPanel("overview", { latest_job: { status: "running" } }), true);
  // tasks: use the loaded task collection so a direct /tasks visit works
  assert.equal(shouldAutoPollPanel("tasks", {}, []), false);
  assert.equal(shouldAutoPollPanel("tasks", {}, [{ status: "running" }]), true);
  assert.equal(shouldAutoPollPanel("tasks", { latest_job: { status: "running" } }, [{ status: "success" }]), false);
  // other panels: never
  assert.equal(shouldAutoPollPanel("records", { latest_job: { status: "running" } }), false);
});

test("pollDelayForPanel uses task delay for tasks and active delay for overview polling", () => {
  assert.equal(pollDelayForPanel("tasks", { latest_job: { status: "running" } }), 10000);
  assert.equal(pollDelayForPanel("overview", { latest_job: { status: "running" } }), 8000);
});
