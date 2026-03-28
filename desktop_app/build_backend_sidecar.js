#!/usr/bin/env node

const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const APP_ROOT = __dirname;
const REPO_ROOT = path.resolve(APP_ROOT, "..");
const DIST_DIR = path.join(APP_ROOT, "build", "desktop_backend");
const WORK_DIR = path.join(APP_ROOT, ".pyinstaller", "work");
const SPEC_DIR = path.join(APP_ROOT, ".pyinstaller", "spec");
const BACKEND_ENTRY = path.join(REPO_ROOT, "desktop_backend_entry.py");
const BINARY_NAME = process.platform === "win32" ? "peap-desktop-backend.exe" : "peap-desktop-backend";

function log(message) {
  process.stdout.write(`${message}\n`);
}

function fail(message) {
  process.stderr.write(`${message}\n`);
  process.exit(1);
}

function run(command, args, options = {}) {
  log(`> ${command} ${args.join(" ")}`);
  const result = spawnSync(command, args, {
    cwd: REPO_ROOT,
    stdio: "inherit",
    ...options,
  });
  if (result.error) {
    fail(`Command failed to start: ${result.error.message}`);
  }
  if (typeof result.status === "number" && result.status !== 0) {
    process.exit(result.status);
  }
}

function isRunnable(command) {
  const result = spawnSync(command, ["--version"], {
    stdio: "ignore",
    shell: process.platform === "win32",
  });
  if (result.error) {
    return false;
  }
  return result.status === 0;
}

function resolveUv() {
  const candidates = [];
  if (process.env.PEAP_UV_BIN) {
    candidates.push(process.env.PEAP_UV_BIN);
  }
  candidates.push("uv");

  for (const candidate of candidates) {
    if (candidate && isRunnable(candidate)) {
      return candidate;
    }
  }
  fail(
    "No usable uv runtime found. Install uv or set PEAP_UV_BIN first.",
  );
}

function buildUvEnvironment() {
  const env = { ...process.env };
  const explicitPython = process.env.PEAP_DESKTOP_PYTHON || process.env.PEAP_PYTHON;
  if (explicitPython) {
    env.UV_PYTHON = explicitPython;
  }
  return env;
}

function ensureDirectories() {
  fs.mkdirSync(DIST_DIR, { recursive: true });
  fs.mkdirSync(WORK_DIR, { recursive: true });
  fs.mkdirSync(SPEC_DIR, { recursive: true });
}

function main() {
  if (!fs.existsSync(BACKEND_ENTRY)) {
    fail(`Missing backend entrypoint: ${BACKEND_ENTRY}`);
  }

  const uv = resolveUv();
  const uvEnv = buildUvEnvironment();
  ensureDirectories();

  run(uv, ["sync", "--locked", "--group", "build"], { env: uvEnv });
  run(uv, [
    "run",
    "--group",
    "build",
    "pyinstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--name",
    "peap-desktop-backend",
    "--distpath",
    DIST_DIR,
    "--workpath",
    WORK_DIR,
    "--specpath",
    SPEC_DIR,
    "--paths",
    REPO_ROOT,
    "--collect-submodules",
    "desktop_backend",
    "--collect-submodules",
    "peap",
    "--collect-submodules",
    "peap_core",
    "--collect-submodules",
    "peap_parsers",
    "--collect-submodules",
    "peap_postprocess",
    "--collect-data",
    "peap_postprocess",
    "--collect-data",
    "playwright",
    "--collect-binaries",
    "playwright",
    BACKEND_ENTRY,
  ], { env: uvEnv });

  const outputBinary = path.join(DIST_DIR, BINARY_NAME);
  if (!fs.existsSync(outputBinary)) {
    fail(`PyInstaller finished without producing ${outputBinary}`);
  }
  log(`Built backend sidecar: ${outputBinary}`);
}

main();
