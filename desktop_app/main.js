const crypto = require("crypto");
const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { resolveBackendLaunch, resolveBackendUrl, validateBackendLaunch } = require("./backend_launch");
const { waitForBackend } = require("./backend_ready");
const { runDesktopSmoke } = require("./smoke_driver");

const BACKEND_PORT = Number(process.env.PEAP_APP_BACKEND_PORT || 42679);
const BACKEND_HOST = process.env.PEAP_APP_BACKEND_HOST || "127.0.0.1";
const BACKEND_URL = resolveBackendUrl({ backendHost: BACKEND_HOST, backendPort: BACKEND_PORT });
const BACKEND_API_TOKEN = String(process.env.PEAP_APP_API_TOKEN || crypto.randomBytes(32).toString("hex"));
const BACKEND_REQUEST_HEADERS = { "X-PEAP-Desktop-Token": BACKEND_API_TOKEN };
const REPO_ROOT = path.resolve(__dirname, "..");

let backendProcess = null;
let backendStartFailure = null;
let backendRestartPromise = null;
let backendReadyPromise = null;
let backendReady = false;
let backendStopRequested = false;
let mainWindow = null;
let smokePickDirectoryQueue = [];
let smokeLastPickDirectory = "";

const BACKEND_READY_TIMEOUT_MS = 60000;
const BACKEND_FORCE_KILL_TIMEOUT_MS = 3000;
const RENDERER_ENTRY_PATH = path.join(__dirname, "build", "renderer", "index.html");

function startupLogPath() {
  try {
    return path.join(app.getPath("documents"), "PEAP", "logs", "desktop-app-main.log");
  } catch (error) {
    return path.join(os.tmpdir(), "peap-desktop-main.log");
  }
}

function appendStartupLog(message, extra = null) {
  const logFile = startupLogPath();
  const line = extra
    ? `${new Date().toISOString()} ${message} ${JSON.stringify(extra)}\n`
    : `${new Date().toISOString()} ${message}\n`;
  try {
    fs.mkdirSync(path.dirname(logFile), { recursive: true });
    fs.appendFileSync(logFile, line, "utf8");
  } catch (error) {
    // Logging must never block startup.
  }
}

function backendLogPath() {
  return startupLogPath().replace(/desktop-app-main\.log$/, "desktop-backend.log");
}

function appendBackendLog(source, chunk) {
  const logFile = backendLogPath();
  const line = `${new Date().toISOString()} [${source}] ${String(chunk || "")}`;
  try {
    fs.mkdirSync(path.dirname(logFile), { recursive: true });
    fs.appendFileSync(logFile, line, "utf8");
  } catch (error) {
    // Logging must never block startup.
  }
}

function handleStartupFatalError(error) {
  const message = String((error && error.message) || error || "Unknown startup failure");
  appendStartupLog("startup_fatal", { message });
  console.error(message);
  dialog.showErrorBox("产权交易所自动录入启动失败", message);
  app.quit();
}

function sleep(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

function parseSmokePickDirectoryQueue(rawValue) {
  const text = String(rawValue || "").trim();
  if (!text) {
    return [];
  }
  try {
    const parsed = JSON.parse(text);
    if (Array.isArray(parsed)) {
      return parsed.map((item) => String(item || "").trim()).filter(Boolean);
    }
  } catch (error) {
    // Fall back to a single path environment variable.
  }
  return [];
}

function consumeSmokePickDirectory() {
  const queueBefore = smokePickDirectoryQueue.length;
  if (queueBefore > 0) {
    const path = smokePickDirectoryQueue.shift() || "";
    if (path) {
      smokeLastPickDirectory = path;
    }
    return {
      path,
      source: "queue",
      queueBefore,
      queueAfter: smokePickDirectoryQueue.length,
    };
  }
  const fallbackPath = String(process.env.PEAP_DESKTOP_SMOKE_PICK_DIRECTORY || "").trim();
  if (fallbackPath) {
    smokeLastPickDirectory = fallbackPath;
  }
  const smokeReportPath = String(process.env.PEAP_DESKTOP_SMOKE_REPORT_PATH || "").trim();
  if (!fallbackPath && smokeReportPath && smokeLastPickDirectory) {
    return {
      path: smokeLastPickDirectory,
      source: "queue_last",
      queueBefore,
      queueAfter: queueBefore,
    };
  }
  return {
    path: fallbackPath,
    source: fallbackPath ? "single_env" : "none",
    queueBefore,
    queueAfter: queueBefore,
  };
}

function normalizeBackendStartupError(error) {
  const message = String((error && error.message) || error || "Desktop backend failed during startup");
  if (/did not become ready within \d+ms/i.test(message)) {
    return new Error(`Desktop backend did not become ready: ${message}`);
  }
  if (/failed to start desktop backend/i.test(message) || /exited unexpectedly/i.test(message)) {
    return error instanceof Error ? error : new Error(message);
  }
  return new Error(`Desktop backend failed during startup: ${message}`);
}

function waitForBackendReady() {
  if (backendReady) {
    return Promise.resolve();
  }
  const processRef = backendProcess;
  if (!processRef) {
    return Promise.reject(new Error("Desktop backend is not running"));
  }
  if (!backendReadyPromise) {
    backendReadyPromise = waitForBackend(BACKEND_URL, {
      timeoutMs: BACKEND_READY_TIMEOUT_MS,
      headers: BACKEND_REQUEST_HEADERS,
      getFailure: () => backendStartFailure,
    }).then(() => {
      if (backendProcess === processRef) {
        backendReady = true;
        appendStartupLog("backend_ready", { backendUrl: BACKEND_URL });
      }
    }).catch((error) => {
      const startupError = normalizeBackendStartupError(error);
      if (!app.isQuitting && !backendStopRequested) {
        backendStartFailure = startupError;
        appendStartupLog("backend_ready_failed", { message: backendStartFailure.message });
      }
      throw startupError;
    }).finally(() => {
      backendReadyPromise = null;
    });
  }
  return backendReadyPromise;
}

async function ensureBackendRunningAndReady() {
  if (!backendProcess) {
    startBackend();
  }
  await waitForBackendReady();
}

function startBackend() {
  if (backendProcess) {
    return;
  }
  const backendLaunch = resolveBackendLaunch({
    env: {
      ...process.env,
      PEAP_APP_API_TOKEN: BACKEND_API_TOKEN,
    },
    isPackaged: app.isPackaged,
    platform: process.platform,
    repoRoot: REPO_ROOT,
    resourcesPath: process.resourcesPath || "",
    backendHost: BACKEND_HOST,
    backendPort: BACKEND_PORT,
    documentsDir: app.getPath("documents"),
  });
  appendStartupLog("backend_launch_resolved", {
    mode: backendLaunch.mode,
    command: backendLaunch.command,
    args: backendLaunch.args,
    cwd: backendLaunch.cwd,
    backendUrl: backendLaunch.backendUrl,
  });
  const launchIssue = validateBackendLaunch(backendLaunch);
  if (launchIssue) {
    appendStartupLog("backend_launch_invalid", { issue: launchIssue });
    throw new Error(launchIssue);
  }
  backendStopRequested = false;
  backendStartFailure = null;
  backendReady = false;
  const stdio = app.isPackaged ? ["ignore", "pipe", "pipe"] : "inherit";
  try {
    backendProcess = spawn(
      backendLaunch.command,
      backendLaunch.args,
      {
        cwd: backendLaunch.cwd,
        env: backendLaunch.env,
        stdio,
      },
    );
  } catch (error) {
    backendStartFailure = normalizeBackendStartupError(new Error(`Failed to start desktop backend: ${String((error && error.message) || error || "spawn failed")}`));
    appendStartupLog("backend_spawn_error", { message: backendStartFailure.message });
    throw backendStartFailure;
  }
  appendStartupLog("backend_spawned", { pid: backendProcess.pid || null });
  if (app.isPackaged && backendProcess.stdout) {
    backendProcess.stdout.on("data", (chunk) => {
      appendBackendLog("stdout", chunk);
    });
  }
  if (app.isPackaged && backendProcess.stderr) {
    backendProcess.stderr.on("data", (chunk) => {
      appendBackendLog("stderr", chunk);
    });
  }

  backendProcess.on("error", (error) => {
    backendStartFailure = normalizeBackendStartupError(new Error(`Failed to start desktop backend: ${error.message}`));
    backendReady = false;
    appendStartupLog("backend_spawn_error", { message: backendStartFailure.message });
  });

  backendProcess.on("exit", (code, signal) => {
    backendProcess = null;
    backendReady = false;
    if (!app.isQuitting && !backendStopRequested) {
      backendStartFailure = normalizeBackendStartupError(new Error(`Desktop backend exited unexpectedly code=${code} signal=${signal}`));
      appendStartupLog("backend_exit", { message: backendStartFailure.message, code, signal });
      console.error(backendStartFailure.message);
    }
  });
}

async function stopBackend() {
  const processRef = backendProcess;
  if (!processRef) {
    return;
  }
  backendStopRequested = true;
  backendStartFailure = null;
  backendReady = false;
  const exited = new Promise((resolve) => {
    processRef.once("exit", resolve);
  });
  try {
    processRef.kill();
  } catch (error) {
    appendStartupLog("backend_kill_failed", { message: String((error && error.message) || error || "") });
  }
  await Promise.race([exited, sleep(BACKEND_FORCE_KILL_TIMEOUT_MS)]);
  if (backendProcess === processRef) {
    try {
      processRef.kill("SIGKILL");
    } catch (error) {
      appendStartupLog("backend_force_kill_failed", { message: String((error && error.message) || error || "") });
    }
    await exited.catch(() => {});
  }
}

async function restartBackend() {
  if (backendRestartPromise) {
    return backendRestartPromise;
  }
  backendRestartPromise = (async () => {
    appendStartupLog("backend_restart_requested");
    await stopBackend();
    startBackend();
    await waitForBackendReady();
    appendStartupLog("backend_restarted", { backendUrl: BACKEND_URL });
    return { ok: true, backendUrl: BACKEND_URL };
  })().finally(() => {
    backendRestartPromise = null;
    backendStopRequested = false;
  });
  return backendRestartPromise;
}

async function createMainWindow() {
  if (!backendProcess || !backendReady) {
    throw new Error("Desktop backend is not ready");
  }
  if (mainWindow && !mainWindow.isDestroyed()) {
    return mainWindow;
  }
  const window = new BrowserWindow({
    width: 1480,
    height: 940,
    minWidth: 1240,
    minHeight: 760,
    backgroundColor: "#f2ebdf",
    title: "产权交易所自动录入",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow = window;
  window.on("closed", () => {
    if (mainWindow === window) {
      mainWindow = null;
    }
  });
  await window.loadFile(RENDERER_ENTRY_PATH);
  return window;
}

app.on("before-quit", () => {
  app.isQuitting = true;
  backendStopRequested = true;
  if (backendProcess) {
    backendProcess.kill();
  }
});

app.whenReady().then(async () => {
  smokePickDirectoryQueue = parseSmokePickDirectoryQueue(process.env.PEAP_DESKTOP_SMOKE_PICK_DIRECTORIES);
  smokeLastPickDirectory = "";
  ipcMain.handle("peap:get-backend-url", () => BACKEND_URL);
  ipcMain.handle("peap:get-backend-config", () => ({
    backendUrl: BACKEND_URL,
    apiToken: BACKEND_API_TOKEN,
  }));
  ipcMain.handle("peap:open-path", async (_event, targetPath) => {
    if (!targetPath) {
      return "empty path";
    }
    return shell.openPath(String(targetPath));
  });
  ipcMain.handle("peap:show-item-in-folder", async (_event, targetPath) => {
    if (!targetPath) {
      return "empty path";
    }
    shell.showItemInFolder(String(targetPath));
    return "";
  });
  ipcMain.handle("peap:pick-directory", async (_event, defaultPath) => {
    const smokeOverride = consumeSmokePickDirectory();
    appendStartupLog("pick_directory_probe", {
      source: smokeOverride.source,
      queueBefore: smokeOverride.queueBefore,
      queueAfter: smokeOverride.queueAfter,
      defaultPath: String(defaultPath || ""),
      pathPresent: Boolean(smokeOverride.path),
    });
    if (smokeOverride.path) {
      appendStartupLog("pick_directory_resolved", { source: `smoke_override_${smokeOverride.source}`, path: smokeOverride.path });
      return smokeOverride.path;
    }
    const result = await dialog.showOpenDialog({
      title: "选择目录",
      defaultPath: defaultPath || undefined,
      properties: ["openDirectory", "createDirectory"],
    });
    if (result.canceled || !result.filePaths.length) {
      appendStartupLog("pick_directory_resolved", { source: "dialog", path: "" });
      return "";
    }
    const selectedPath = String(result.filePaths[0] || "");
    appendStartupLog("pick_directory_resolved", { source: "dialog", path: selectedPath });
    return selectedPath;
  });
  ipcMain.handle("peap:pick-file", async (_event, defaultPath) => {
    const result = await dialog.showOpenDialog({
      title: "选择文件",
      defaultPath: defaultPath || undefined,
      properties: ["openFile"],
    });
    if (result.canceled || !result.filePaths.length) {
      appendStartupLog("pick_file_resolved", { source: "dialog", path: "" });
      return "";
    }
    const selectedPath = String(result.filePaths[0] || "");
    appendStartupLog("pick_file_resolved", { source: "dialog", path: selectedPath });
    return selectedPath;
  });
  ipcMain.handle("peap:restart-backend", async () => restartBackend());

  await ensureBackendRunningAndReady();
  await createMainWindow();
  if (String(process.env.PEAP_DESKTOP_SMOKE_REPORT_PATH || "").trim()) {
    await runDesktopSmoke({
      window: mainWindow,
      backendUrl: BACKEND_URL,
      apiToken: BACKEND_API_TOKEN,
      reportPath: String(process.env.PEAP_DESKTOP_SMOKE_REPORT_PATH || "").trim(),
    });
    app.quit();
    return;
  }

  app.on("activate", async () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      try {
        await ensureBackendRunningAndReady();
        await createMainWindow();
      } catch (error) {
        handleStartupFatalError(error);
      }
    }
  });
}).catch(handleStartupFatalError);

app.on("window-all-closed", () => {
  app.quit();
});
