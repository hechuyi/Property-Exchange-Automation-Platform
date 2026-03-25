const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

function listedPackagedFiles(configText) {
  const files = new Set();
  const lines = String(configText || "").split(/\r?\n/);
  let inFilesSection = false;
  for (const line of lines) {
    if (!inFilesSection) {
      if (/^files:\s*$/.test(line)) {
        inFilesSection = true;
      }
      continue;
    }
    if (/^[A-Za-z0-9_]/.test(line)) {
      break;
    }
    const match = line.match(/^\s*-\s+(.+?)\s*$/);
    if (match) {
      files.add(match[1].replace(/^["']|["']$/g, ""));
    }
  }
  return files;
}

function localRuntimeRequires(sourceText) {
  return Array.from(String(sourceText || "").matchAll(/require\(["']\.\/([^"']+)["']\)/g))
    .map((match) => {
      const target = String(match[1] || "").trim();
      return target.endsWith(".js") ? target : `${target}.js`;
    });
}

test("electron-builder files include every local runtime dependency used by main.js", () => {
  const configText = fs.readFileSync(path.join(__dirname, "electron-builder.yml"), "utf8");
  const mainSource = fs.readFileSync(path.join(__dirname, "main.js"), "utf8");
  const packagedFiles = listedPackagedFiles(configText);
  const runtimeDependencies = localRuntimeRequires(mainSource);

  for (const dependency of runtimeDependencies) {
    assert.ok(
      packagedFiles.has(dependency),
      `electron-builder.yml is missing runtime dependency ${dependency}`,
    );
  }
});
