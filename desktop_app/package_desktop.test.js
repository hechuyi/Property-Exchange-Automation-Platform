const test = require("node:test");
const assert = require("node:assert/strict");
const Module = require("node:module");
const path = require("node:path");

const {
  buildPackagingPlan,
  normalizeRequestedPlatform,
} = require("./package_desktop.js");

const PACKAGE_DESKTOP_MODULE_PATH = path.join(__dirname, "package_desktop.js");

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

test("main builds renderer assets before invoking electron-builder", () => {
  const originalLoad = Module._load;
  const calls = [];
  Module._load = function mockLoad(request, parent, isMain) {
    if (request === "child_process") {
      return {
        spawnSync: (command, args, options = {}) => {
          calls.push({
            command,
            args: [...args],
            cwd: options.cwd,
          });
          return { status: 0 };
        },
      };
    }
    return originalLoad(request, parent, isMain);
  };

  delete require.cache[PACKAGE_DESKTOP_MODULE_PATH];
  const { main } = require("./package_desktop.js");

  try {
    main(["--platform", "mac", "--layout", "release"]);
    assert.deepEqual(
      calls.map((call) => [call.command, call.args]),
      [
        ["npm", ["run", "build:backend"]],
        ["npm", ["run", "build:renderer"]],
        ["npx", ["electron-builder", "--mac", "pkg", "-c", "electron-builder.yml", "--config.electronDist=node_modules/electron/dist"]],
      ],
    );
  } finally {
    Module._load = originalLoad;
    delete require.cache[PACKAGE_DESKTOP_MODULE_PATH];
  }
});
