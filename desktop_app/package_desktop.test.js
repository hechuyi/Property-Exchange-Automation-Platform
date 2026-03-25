const test = require("node:test");
const assert = require("node:assert/strict");

const {
  buildPackagingPlan,
  normalizeRequestedPlatform,
} = require("./package_desktop.js");

test("normalizeRequestedPlatform resolves current host aliases", () => {
  assert.equal(normalizeRequestedPlatform("current", "darwin"), "darwin");
  assert.equal(normalizeRequestedPlatform("mac", "darwin"), "darwin");
  assert.equal(normalizeRequestedPlatform("windows", "win32"), "win32");
});

test("buildPackagingPlan creates native mac release plan with local electron dist", () => {
  const plan = buildPackagingPlan({
    hostPlatform: "darwin",
    requestedPlatform: "mac",
    layout: "release",
    appDir: "/repo/desktop_app",
  });

  assert.equal(plan.targetPlatform, "darwin");
  assert.deepEqual(plan.builderArgs, [
    "--mac",
    "pkg",
    "-c",
    "electron-builder.yml",
    "--config.electronDist=node_modules/electron/dist",
  ]);
});

test("buildPackagingPlan rejects cross-platform packaging on the wrong host", () => {
  assert.throws(
    () =>
      buildPackagingPlan({
        hostPlatform: "darwin",
        requestedPlatform: "win",
        layout: "release",
        appDir: "/repo/desktop_app",
      }),
    /native host/i,
  );
});
