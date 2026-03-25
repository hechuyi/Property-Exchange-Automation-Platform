#!/usr/bin/env node

const path = require("path");
const { spawnSync } = require("child_process");

function normalizeRequestedPlatform(requestedPlatform, hostPlatform = process.platform) {
  const value = String(requestedPlatform || "current").trim().toLowerCase();
  if (!value || value === "current" || value === "host" || value === "auto") {
    return hostPlatform;
  }
  if (["mac", "macos", "darwin"].includes(value)) {
    return "darwin";
  }
  if (["win", "windows", "win32"].includes(value)) {
    return "win32";
  }
  throw new Error(`Unsupported desktop packaging platform: ${requestedPlatform}`);
}

function normalizeLayout(layout) {
  const value = String(layout || "release").trim().toLowerCase();
  if (["release", "dist"].includes(value)) {
    return "release";
  }
  if (["dir", "pack", "unpacked"].includes(value)) {
    return "dir";
  }
  throw new Error(`Unsupported desktop packaging layout: ${layout}`);
}

function buildPackagingPlan({
  hostPlatform = process.platform,
  requestedPlatform = "current",
  layout = "release",
  appDir = __dirname,
} = {}) {
  const targetPlatform = normalizeRequestedPlatform(requestedPlatform, hostPlatform);
  const targetLayout = normalizeLayout(layout);
  if (targetPlatform !== hostPlatform) {
    throw new Error(
      `Desktop packaging for ${targetPlatform} must run on its native host. ` +
      `Current host is ${hostPlatform}. Use a native host/runner instead.`,
    );
  }

  const builderArgs = [];
  if (targetLayout === "dir") {
    builderArgs.push("--dir");
  } else if (targetPlatform === "darwin") {
    builderArgs.push("--mac", "pkg");
  } else if (targetPlatform === "win32") {
    builderArgs.push("--win", "nsis");
  } else {
    throw new Error(`Unsupported native desktop packaging host: ${targetPlatform}`);
  }
  builderArgs.push("-c", "electron-builder.yml");
  builderArgs.push("--config.electronDist=node_modules/electron/dist");

  return {
    appDir: path.resolve(appDir),
    hostPlatform,
    targetPlatform,
    layout: targetLayout,
    builderArgs,
  };
}

function run(command, args, { cwd } = {}) {
  const result = spawnSync(command, args, {
    cwd,
    stdio: "inherit",
    shell: process.platform === "win32",
  });
  if (result.error) {
    throw result.error;
  }
  if (typeof result.status === "number" && result.status !== 0) {
    process.exit(result.status);
  }
}

function parseArgs(argv) {
  const parsed = {
    platform: "current",
    layout: "release",
  };
  for (let index = 0; index < argv.length; index += 1) {
    const token = String(argv[index] || "").trim();
    if (token === "--platform") {
      parsed.platform = String(argv[index + 1] || "").trim() || parsed.platform;
      index += 1;
      continue;
    }
    if (token === "--layout") {
      parsed.layout = String(argv[index + 1] || "").trim() || parsed.layout;
      index += 1;
      continue;
    }
    throw new Error(`Unknown argument: ${token}`);
  }
  return parsed;
}

function main(argv = process.argv.slice(2)) {
  const args = parseArgs(argv);
  const plan = buildPackagingPlan({
    requestedPlatform: args.platform,
    layout: args.layout,
    appDir: __dirname,
  });
  process.stdout.write(
    `Packaging desktop app for ${plan.targetPlatform} (${plan.layout}) in ${plan.appDir}\n`,
  );
  run("npm", ["run", "build:backend"], { cwd: plan.appDir });
  run("npx", ["electron-builder", ...plan.builderArgs], { cwd: plan.appDir });
}

if (require.main === module) {
  try {
    main(process.argv.slice(2));
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exit(1);
  }
}

module.exports = {
  buildPackagingPlan,
  main,
  normalizeRequestedPlatform,
};
