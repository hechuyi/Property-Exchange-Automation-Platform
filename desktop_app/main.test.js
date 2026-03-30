const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { EventEmitter } = require("node:events");
const Module = require("node:module");

const MAIN_MODULE_PATH = path.join(__dirname, "main.js");
const EXPECTED_RENDERER_ENTRY = path.join(__dirname, "build", "renderer", "index.html");

function createDeferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

async function flushMicrotasks() {
  await Promise.resolve();
  await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
}

function createChildProcessStub() {
  const child = new EventEmitter();
  child.pid = 1234;
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  child.kill = () => {};
  return child;
}

function loadMainWithMocks({
  waitForBackendImpl,
  spawnImpl = () => createChildProcessStub(),
  smokeDriverImpl,
  showOpenDialogImpl = async () => ({ canceled: true, filePaths: [] }),
  env = {},
} = {}) {
  const originalLoad = Module._load;
  const originalEnv = new Map();
  for (const [key, value] of Object.entries(env)) {
    originalEnv.set(key, Object.prototype.hasOwnProperty.call(process.env, key) ? process.env[key] : undefined);
    process.env[key] = value;
  }
  const whenReadyDeferred = createDeferred();
  const browserWindows = [];
  const showErrorCalls = [];
  const appQuitCalls = [];
  const ipcHandles = [];
  const spawnedProcesses = [];
  const appHandlers = new Map();
  let appIsQuitting = false;

  class BrowserWindowStub extends EventEmitter {
    constructor(options) {
      super();
      this.options = options;
      this.destroyed = false;
      this.loadedFiles = [];
      browserWindows.push(this);
    }

    isDestroyed() {
      return this.destroyed;
    }

    async loadFile(targetPath) {
      this.loadedFiles.push(targetPath);
    }
  }

  BrowserWindowStub.getAllWindows = () => browserWindows.filter((window) => !window.isDestroyed());

  const electronStub = {
    app: {
      isPackaged: false,
      getPath: () => "/tmp",
      quit: () => {
        appQuitCalls.push(true);
      },
      whenReady: () => whenReadyDeferred.promise,
      on: (event, handler) => {
        appHandlers.set(event, handler);
      },
      get isQuitting() {
        return appIsQuitting;
      },
      set isQuitting(value) {
        appIsQuitting = value;
      },
    },
    BrowserWindow: BrowserWindowStub,
    dialog: {
      showErrorBox: (title, message) => {
        showErrorCalls.push({ title, message });
      },
      showOpenDialog: showOpenDialogImpl,
    },
    ipcMain: {
      handle: (channel, handler) => {
        ipcHandles.push({ channel, handler });
      },
    },
    shell: {
      openPath: async () => "",
      showItemInFolder: () => {},
    },
  };

  Module._load = function mockLoad(request, parent, isMain) {
    if (request === "electron") {
      return electronStub;
    }
    if (request === "child_process") {
      return {
        spawn: (...args) => {
          const child = spawnImpl(...args);
          spawnedProcesses.push(child);
          return child;
        },
      };
    }
    if (request === "./backend_launch" && parent && parent.filename === MAIN_MODULE_PATH) {
      return {
        resolveBackendLaunch: () => ({
          mode: "development",
          command: "python3",
          args: ["-m", "desktop_backend.app_backend"],
          cwd: "/tmp/peap",
          env: {},
          backendUrl: "http://127.0.0.1:42679",
        }),
        resolveBackendUrl: () => "http://127.0.0.1:42679",
        validateBackendLaunch: () => null,
      };
    }
    if (request === "./backend_ready" && parent && parent.filename === MAIN_MODULE_PATH) {
      return {
        waitForBackend: waitForBackendImpl || (() => Promise.resolve()),
      };
    }
    if (request === "./smoke_driver" && parent && parent.filename === MAIN_MODULE_PATH) {
      return {
        runDesktopSmoke: smokeDriverImpl || (async () => undefined),
      };
    }
    return originalLoad(request, parent, isMain);
  };

  delete require.cache[MAIN_MODULE_PATH];
  require(MAIN_MODULE_PATH);

  return {
    browserWindows,
    showErrorCalls,
    appQuitCalls,
    ipcHandles,
    spawnedProcesses,
    appHandlers,
    whenReadyDeferred,
    restore() {
      for (const [key, value] of originalEnv.entries()) {
        if (value === undefined) {
          delete process.env[key];
        } else {
          process.env[key] = value;
        }
      }
      Module._load = originalLoad;
      delete require.cache[MAIN_MODULE_PATH];
    },
  };
}

test("startup does not create the main window before backend readiness resolves", async () => {
  const backendReady = createDeferred();
  const harness = loadMainWithMocks({
    waitForBackendImpl: () => backendReady.promise,
  });

  try {
    harness.whenReadyDeferred.resolve();
    await flushMicrotasks();

    assert.equal(harness.browserWindows.length, 0);

    backendReady.resolve();
    await flushMicrotasks();

    assert.equal(harness.browserWindows.length, 1);
    assert.deepEqual(harness.browserWindows[0].loadedFiles, [EXPECTED_RENDERER_ENTRY]);
  } finally {
    harness.restore();
  }
});

test("startup converts backend early exit before readiness into a fatal startup with no window", async () => {
  const harness = loadMainWithMocks({
    waitForBackendImpl: () => Promise.reject(new Error("Desktop backend exited unexpectedly code=7 signal=null")),
  });

  try {
    harness.whenReadyDeferred.resolve();
    await flushMicrotasks();

    assert.equal(harness.browserWindows.length, 0);
    assert.equal(harness.showErrorCalls.length, 1);
    assert.match(harness.showErrorCalls[0].message, /backend exited unexpectedly/i);
    assert.equal(harness.appQuitCalls.length, 1);
  } finally {
    harness.restore();
  }
});

test("startup converts backend readiness timeout into a fatal startup with no window", async () => {
  const harness = loadMainWithMocks({
    waitForBackendImpl: () => Promise.reject(new Error("Backend did not become ready within 60000ms")),
  });

  try {
    harness.whenReadyDeferred.resolve();
    await flushMicrotasks();

    assert.equal(harness.browserWindows.length, 0);
    assert.equal(harness.showErrorCalls.length, 1);
    assert.match(harness.showErrorCalls[0].message, /did not become ready within 60000ms/i);
    assert.equal(harness.appQuitCalls.length, 1);
  } finally {
    harness.restore();
  }
});

test("startup converts backend spawn failures into a fatal startup with no window", async () => {
  const harness = loadMainWithMocks({
    spawnImpl: () => {
      throw new Error("spawn EACCES");
    },
  });

  try {
    harness.whenReadyDeferred.resolve();
    await flushMicrotasks();

    assert.equal(harness.browserWindows.length, 0);
    assert.equal(harness.showErrorCalls.length, 1);
    assert.match(harness.showErrorCalls[0].message, /spawn EACCES/);
    assert.equal(harness.appQuitCalls.length, 1);
  } finally {
    harness.restore();
  }
});

test("activate routes backend startup failures through fatal startup handling", async () => {
  let readinessAttempts = 0;
  let spawnCalls = 0;
  const harness = loadMainWithMocks({
    waitForBackendImpl: () => {
      readinessAttempts += 1;
      if (readinessAttempts === 1) {
        return Promise.resolve();
      }
      return Promise.reject(new Error("Desktop backend exited unexpectedly code=9 signal=null"));
    },
    spawnImpl: () => {
      spawnCalls += 1;
      return createChildProcessStub();
    },
  });

  try {
    harness.whenReadyDeferred.resolve();
    await flushMicrotasks();

    assert.equal(harness.browserWindows.length, 1);
    assert.equal(spawnCalls, 1);

    const firstWindow = harness.browserWindows[0];
    harness.spawnedProcesses[0].emit("exit", 9, null);
    await flushMicrotasks();
    firstWindow.destroyed = true;
    firstWindow.emit("closed");

    await harness.appHandlers.get("activate")();
    await flushMicrotasks();

    assert.equal(spawnCalls, 2);
    assert.equal(harness.showErrorCalls.length, 1);
    assert.match(harness.showErrorCalls[0].message, /backend exited unexpectedly/i);
    assert.equal(harness.appQuitCalls.length, 1);
    assert.equal(harness.browserWindows.length, 1);
  } finally {
    harness.restore();
  }
});

test("pick-directory IPC consumes configured smoke directory queue before opening dialogs", async () => {
  const harness = loadMainWithMocks({
    env: {
      PEAP_DESKTOP_SMOKE_PICK_DIRECTORIES: JSON.stringify(["/tmp/one", "/tmp/two"]),
    },
    showOpenDialogImpl: async () => ({ canceled: false, filePaths: ["/tmp/dialog"] }),
  });

  try {
    harness.whenReadyDeferred.resolve();
    await flushMicrotasks();

    const pickDirectory = harness.ipcHandles.find((item) => item.channel === "peap:pick-directory");
    assert.ok(pickDirectory);

    assert.equal(await pickDirectory.handler({}, "/tmp/default"), "/tmp/one");
    assert.equal(await pickDirectory.handler({}, "/tmp/default"), "/tmp/two");
    assert.equal(await pickDirectory.handler({}, "/tmp/default"), "/tmp/dialog");
  } finally {
    harness.restore();
  }
});

test("pick-file IPC returns the selected file path", async () => {
  const harness = loadMainWithMocks({
    showOpenDialogImpl: async () => ({ canceled: false, filePaths: ["/tmp/postprocess.json"] }),
  });

  try {
    harness.whenReadyDeferred.resolve();
    await flushMicrotasks();

    const pickFile = harness.ipcHandles.find((item) => item.channel === "peap:pick-file");
    assert.ok(pickFile);
    assert.equal(await pickFile.handler({}, "/tmp/default.json"), "/tmp/postprocess.json");
  } finally {
    harness.restore();
  }
});

test("pick-directory IPC returns empty after single-item smoke queue is exhausted and dialog is canceled", async () => {
  const harness = loadMainWithMocks({
    env: {
      PEAP_DESKTOP_SMOKE_PICK_DIRECTORIES: JSON.stringify(["/tmp/one"]),
    },
    showOpenDialogImpl: async () => ({ canceled: true, filePaths: [] }),
  });

  try {
    harness.whenReadyDeferred.resolve();
    await flushMicrotasks();

    const pickDirectory = harness.ipcHandles.find((item) => item.channel === "peap:pick-directory");
    assert.ok(pickDirectory);

    assert.equal(await pickDirectory.handler({}, "/tmp/default"), "/tmp/one");
    assert.equal(await pickDirectory.handler({}, "/tmp/default"), "");
  } finally {
    harness.restore();
  }
});

test("pick-directory IPC reuses the last smoke override after the queue is exhausted during desktop smoke", async () => {
  const harness = loadMainWithMocks({
    env: {
      PEAP_DESKTOP_SMOKE_REPORT_PATH: "/tmp/desktop-smoke.json",
      PEAP_DESKTOP_SMOKE_PICK_DIRECTORIES: JSON.stringify(["/tmp/fixture"]),
    },
    showOpenDialogImpl: async () => ({ canceled: false, filePaths: ["/tmp/dialog"] }),
  });

  try {
    harness.whenReadyDeferred.resolve();
    await flushMicrotasks();

    const pickDirectory = harness.ipcHandles.find((item) => item.channel === "peap:pick-directory");
    assert.ok(pickDirectory);

    assert.equal(await pickDirectory.handler({}, "/tmp/default"), "/tmp/fixture");
    assert.equal(await pickDirectory.handler({}, "/tmp/default"), "/tmp/fixture");
  } finally {
    harness.restore();
  }
});

test("startup launches smoke driver when a smoke report path is configured", async () => {
  const smokeCalls = [];
  const harness = loadMainWithMocks({
    env: {
      PEAP_DESKTOP_SMOKE_REPORT_PATH: "/tmp/desktop-smoke.json",
    },
    smokeDriverImpl: async (options) => {
      smokeCalls.push(options);
    },
  });

  try {
    harness.whenReadyDeferred.resolve();
    await flushMicrotasks();

    assert.equal(harness.browserWindows.length, 1);
    assert.deepEqual(harness.browserWindows[0].loadedFiles, [EXPECTED_RENDERER_ENTRY]);
    assert.equal(smokeCalls.length, 1);
    assert.equal(smokeCalls[0].reportPath, "/tmp/desktop-smoke.json");
    assert.equal(smokeCalls[0].backendUrl, "http://127.0.0.1:42679");
  } finally {
    harness.restore();
  }
});
