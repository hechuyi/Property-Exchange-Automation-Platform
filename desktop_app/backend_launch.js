const fs = require("fs");
const os = require("os");
const path = require("path");

function resolveBackendUrl({ backendHost, backendPort }) {
  return `http://${backendHost}:${backendPort}`;
}

function packagedBinaryName(platform = process.platform) {
  return platform === "win32" ? "peap-desktop-backend.exe" : "peap-desktop-backend";
}

function resolvePathMaybeRelative(value, baseDir) {
  const text = String(value || "").trim();
  if (!text) {
    return "";
  }
  if (path.isAbsolute(text)) {
    return path.normalize(text);
  }
  if (text.includes("/") || text.includes("\\")) {
    return path.resolve(baseDir, text);
  }
  return text;
}

function commandLooksLikePath(command) {
  const text = String(command || "").trim();
  if (!text) {
    return false;
  }
  return path.isAbsolute(text) || text.includes("/") || text.includes("\\");
}

function resolveDirectory(value, fallbackDir) {
  const text = String(value || "").trim();
  if (!text) {
    return path.resolve(fallbackDir);
  }
  return path.resolve(fallbackDir, text);
}

function parseBackendArgs(rawValue) {
  const text = String(rawValue || "").trim();
  if (!text) {
    return [];
  }
  try {
    const parsed = JSON.parse(text);
    if (Array.isArray(parsed)) {
      return parsed.map((item) => String(item));
    }
  } catch (error) {
    // Fall back to whitespace splitting for simple local overrides.
  }
  return text.split(/\s+/).filter(Boolean);
}

function defaultAppHome({
  env = process.env,
  platform = process.platform,
  homeDir = os.homedir(),
  documentsDir = "",
} = {}) {
  const explicit = env.PEAP_APP_HOME || env.PEAP_WORKSPACE_ROOT || env.PEAP_DOCUMENTS_HOME;
  if (explicit) {
    return path.resolve(explicit);
  }
  const baseDocumentsDir = documentsDir || path.join(homeDir, "Documents");
  return path.resolve(baseDocumentsDir, "PEAP");
}

function resolveBrowserCache({
  env = process.env,
  isPackaged = false,
  platform = process.platform,
  repoRoot = path.resolve(__dirname, ".."),
  homeDir = os.homedir(),
  documentsDir = "",
} = {}) {
  const explicit = env.PEAP_PLAYWRIGHT_BROWSERS_PATH || env.PLAYWRIGHT_BROWSERS_PATH;
  if (explicit) {
    return path.resolve(explicit);
  }
  return path.resolve(defaultAppHome({ env, platform, homeDir, documentsDir }), "cache", "ms-playwright");
}

function resolveBackendLaunch({
  env = process.env,
  isPackaged = false,
  platform = process.platform,
  repoRoot = path.resolve(__dirname, ".."),
  resourcesPath = "",
  backendHost = "127.0.0.1",
  backendPort = 42679,
  homeDir = os.homedir(),
  documentsDir = "",
} = {}) {
  const resolvedRepoRoot = path.resolve(repoRoot);
  const workspaceRoot = defaultAppHome({ env, platform, homeDir, documentsDir });
  const browserCacheDir = resolveBrowserCache({
    env,
    isPackaged,
    platform,
    repoRoot: resolvedRepoRoot,
    homeDir,
    documentsDir,
  });
  const launchEnv = {
    ...env,
    PEAP_APP_HOME: env.PEAP_APP_HOME || env.PEAP_WORKSPACE_ROOT || env.PEAP_DOCUMENTS_HOME || workspaceRoot,
    PYTHONUNBUFFERED: "1",
    PLAYWRIGHT_BROWSERS_PATH: browserCacheDir,
    PEAP_PLAYWRIGHT_BROWSERS_PATH: browserCacheDir,
  };
  const backendUrl = resolveBackendUrl({ backendHost, backendPort });

  if (env.PEAP_BACKEND_CMD) {
    return {
      mode: "explicit",
      backendUrl,
      command: resolvePathMaybeRelative(env.PEAP_BACKEND_CMD, resolvedRepoRoot),
      args: parseBackendArgs(env.PEAP_BACKEND_ARGS),
      cwd: resolveDirectory(env.PEAP_BACKEND_CWD, resolvedRepoRoot),
      env: launchEnv,
    };
  }

  if (isPackaged) {
    const resourceRoot = path.resolve(
      env.PEAP_BACKEND_RESOURCE_ROOT || path.join(resourcesPath, "desktop_backend"),
    );
    return {
      mode: "packaged",
      backendUrl,
      command: resolvePathMaybeRelative(
        env.PEAP_BACKEND_BIN || path.join(resourceRoot, packagedBinaryName(platform)),
        resolvedRepoRoot,
      ),
      args: ["--host", backendHost, "--port", String(backendPort)],
      cwd: resolveDirectory(env.PEAP_BACKEND_CWD, resourceRoot),
      env: launchEnv,
    };
  }

  const devPython =
    platform === "win32"
      ? path.join(resolvedRepoRoot, ".venv-desktop", "Scripts", "python.exe")
      : path.join(resolvedRepoRoot, ".venv-desktop", "bin", "python");
  return {
    mode: "development",
    backendUrl,
    command: resolvePathMaybeRelative(
      env.PEAP_DESKTOP_PYTHON || env.PEAP_PYTHON || devPython,
      resolvedRepoRoot,
    ),
    args: ["-m", "desktop_backend.app_backend", "--host", backendHost, "--port", String(backendPort)],
    cwd: resolveDirectory(env.PEAP_BACKEND_CWD, resolvedRepoRoot),
    env: launchEnv,
  };
}

function validateBackendLaunch(launch) {
  const command = String((launch && launch.command) || "").trim();
  if (!command) {
    return "Backend launch command is empty.";
  }
  if (commandLooksLikePath(command) && !fs.existsSync(command)) {
    if (launch && launch.mode === "development") {
      return (
        "Desktop backend Python runtime was not found.\n\n" +
        `Expected: ${command}\n\n` +
        "Build the desktop dev environment first:\n" +
        "bash scripts/bootstrap_desktop_env.sh"
      );
    }
    if (launch && launch.mode === "packaged") {
      return (
        "Packaged desktop backend sidecar was not found.\n\n" +
        `Expected: ${command}\n\n` +
        "Rebuild the app package so the backend sidecar is bundled into desktop_backend/."
      );
    }
    return `Backend launch target was not found: ${command}`;
  }
  return "";
}

module.exports = {
  commandLooksLikePath,
  defaultAppHome,
  packagedBinaryName,
  parseBackendArgs,
  resolveBackendLaunch,
  resolveBackendUrl,
  resolveBrowserCache,
  validateBackendLaunch,
};
