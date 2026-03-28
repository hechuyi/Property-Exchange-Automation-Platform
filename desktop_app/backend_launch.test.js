const test = require("node:test");
const assert = require("node:assert/strict");

const {
  commandLooksLikePath,
  parseBackendArgs,
  resolveBackendLaunch,
  validateBackendLaunch,
} = require("./backend_launch");

test("development launch uses repo venv python and workspace playwright cache", () => {
  const repoRoot = "/tmp/peap";
  const launch = resolveBackendLaunch({
    env: {},
    isPackaged: false,
    platform: "darwin",
    repoRoot,
    resourcesPath: "/Applications/PEAP.app/Contents/Resources",
    backendHost: "127.0.0.1",
    backendPort: 42679,
    homeDir: "/Users/tester",
    documentsDir: "/Users/tester/Documents",
  });

  assert.equal(launch.mode, "development");
  assert.equal(launch.command, "/tmp/peap/.venv/bin/python");
  assert.deepEqual(launch.args, [
    "-m",
    "desktop_backend.app_backend",
    "--host",
    "127.0.0.1",
    "--port",
    "42679",
  ]);
  assert.equal(launch.cwd, "/tmp/peap");
  assert.equal(launch.env.PLAYWRIGHT_BROWSERS_PATH, "/Users/tester/Documents/PEAP/cache/ms-playwright");
  assert.equal(launch.env.PEAP_PLAYWRIGHT_BROWSERS_PATH, "/Users/tester/Documents/PEAP/cache/ms-playwright");
  assert.equal(launch.backendUrl, "http://127.0.0.1:42679");
});

test("packaged launch is explicitly retired and refuses bundled sidecar mode", () => {
  const launch = resolveBackendLaunch({
    env: {},
    isPackaged: true,
    platform: "darwin",
    repoRoot: "/tmp/peap",
    resourcesPath: "/Applications/PEAP.app/Contents/Resources",
    backendHost: "0.0.0.0",
    backendPort: 43111,
    homeDir: "/Users/tester",
    documentsDir: "/Users/tester/Documents",
  });

  assert.equal(launch.mode, "unsupported_packaged");
  assert.equal(launch.command, "");
  assert.equal(launch.cwd, "/tmp/peap");
  assert.deepEqual(launch.args, []);
  assert.match(String(validateBackendLaunch(launch) || ""), /packaged desktop runtime has been retired/i);
});

test("explicit launch keeps caller command and mirrors explicit playwright cache", () => {
  const launch = resolveBackendLaunch({
    env: {
      PEAP_BACKEND_CMD: "python3",
      PEAP_BACKEND_ARGS: "[\"-m\", \"custom_backend\"]",
      PLAYWRIGHT_BROWSERS_PATH: "/tmp/custom-browser-cache",
    },
    isPackaged: false,
    platform: "linux",
    repoRoot: "/srv/peap",
    homeDir: "/home/tester",
  });

  assert.equal(launch.mode, "explicit");
  assert.equal(launch.command, "python3");
  assert.deepEqual(launch.args, ["-m", "custom_backend"]);
  assert.equal(launch.env.PEAP_PLAYWRIGHT_BROWSERS_PATH, "/tmp/custom-browser-cache");
  assert.equal(launch.env.PLAYWRIGHT_BROWSERS_PATH, "/tmp/custom-browser-cache");
});

test("parseBackendArgs falls back to whitespace splitting", () => {
  assert.deepEqual(parseBackendArgs("--host 127.0.0.1 --port 42679"), [
    "--host",
    "127.0.0.1",
    "--port",
    "42679",
  ]);
});

test("validateBackendLaunch explains missing dev venv python clearly", () => {
  const message = validateBackendLaunch({
    mode: "development",
    command: "/tmp/peap/.venv/bin/python",
  });

  assert.match(message, /Desktop backend Python runtime was not found/);
  assert.match(message, /uv sync/);
});

test("commandLooksLikePath distinguishes bare commands from file paths", () => {
  assert.equal(commandLooksLikePath("python3"), false);
  assert.equal(commandLooksLikePath("/tmp/peap/.venv/bin/python"), true);
  assert.equal(commandLooksLikePath(".venv/bin/python"), true);
});
