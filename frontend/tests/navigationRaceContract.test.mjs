import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

function extractSwitchPanelBody(source) {
  const anchor = "window.switchPanel = function (panel) {";
  const start = source.indexOf(anchor);
  assert.notEqual(start, -1, "switchPanel definition should exist");
  const asyncStart = source.indexOf("(async () => {", start);
  assert.notEqual(asyncStart, -1, "switchPanel should wrap navigation in async IIFE");
  const end = source.indexOf("    })();", asyncStart);
  assert.notEqual(end, -1, "switchPanel async IIFE should close cleanly");
  return source.slice(asyncStart, end);
}

function indexOfOrThrow(haystack, needle) {
  const index = haystack.indexOf(needle);
  assert.notEqual(index, -1, `expected snippet not found: ${needle}`);
  return index;
}

function assertGuardBeforeNextBoundary(body, awaitSnippet, nextBoundarySnippets) {
  const awaitIndex = indexOfOrThrow(body, awaitSnippet);
  const nextBoundaryIndex = Math.min(
    ...nextBoundarySnippets
      .map((snippet) => body.indexOf(snippet, awaitIndex + awaitSnippet.length))
      .filter((index) => index !== -1),
  );
  assert.notEqual(nextBoundaryIndex, Infinity, `expected boundary after ${awaitSnippet}`);
  const guardSnippet = "if (navigationSeq !== panelNavigationSeq) return;";
  const guardIndex = body.indexOf(guardSnippet, awaitIndex + awaitSnippet.length);
  assert.notEqual(guardIndex, -1, `expected navigation guard after ${awaitSnippet}`);
  assert.ok(guardIndex < nextBoundaryIndex, `guard must appear after ${awaitSnippet} and before the next awaited load/render/startPoll`);
}

test("switchPanel guards each awaited navigation step before later loads, render, or polling", async () => {
  const source = await readFile(new URL("../app.js", import.meta.url), "utf8");
  const body = extractSwitchPanelBody(source);

  assert.match(source, /let\s+panelNavigationSeq\s*=\s*0\s*;/);
  assert.match(source, /const\s+navigationSeq\s*=\s*\+\+panelNavigationSeq\s*;/);

  assertGuardBeforeNextBoundary(body, "await loadCatalog();", [
    "await loadBasicSettings();",
    "await loadOverviewData();",
    "await loadFamilyStats();",
    "render();",
    "startPoll();",
  ]);
  assertGuardBeforeNextBoundary(body, "await loadBasicSettings();", [
    "await loadOverviewData();",
    "await loadFamilyStats();",
    "render();",
    "startPoll();",
  ]);
  assertGuardBeforeNextBoundary(body, "await loadOverviewData();", [
    "await loadFamilyStats();",
    "render();",
    "startPoll();",
  ]);
  assertGuardBeforeNextBoundary(body, "await loadFamilyStats();", [
    "openOverviewStream();",
    "render();",
    "startPoll();",
  ]);
  assertGuardBeforeNextBoundary(body, "await loadJobs();", [
    "render();",
    "startPoll();",
  ]);
});

test("review problem loads ignore stale filter responses", async () => {
  const source = await readFile(new URL("../app.js", import.meta.url), "utf8");

  assert.match(source, /let\s+reviewProblemRequestSeq\s*=\s*0\s*;/);
  assert.match(source, /const\s+requestSeq\s*=\s*\+\+reviewProblemRequestSeq\s*;/);
  assert.match(source, /if\s*\(requestSeq\s*!==\s*reviewProblemRequestSeq\)\s*return;\s*\n\s*reviewProblems\s*=\s*nextReviewProblems;/);
  assert.match(source, /page:\s*key\s*===\s*"page"\s*\?\s*value\s*:\s*1/);
});

test("deep link routing accepts export history panel", async () => {
  const source = await readFile(new URL("../app.js", import.meta.url), "utf8");

  assert.match(source, /const\s+VALID_PANELS\s*=\s*\[[^\]]*"export-history"/s);
  assert.match(source, /case\s+"export-history":\s*renderExportHistory\(\);\s*break;/);
});

test("overview polling cannot overwrite a newer SSE snapshot", async () => {
  const source = await readFile(new URL("../app.js", import.meta.url), "utf8");
  const loadBody = source.slice(
    indexOfOrThrow(source, "async function loadOverviewData() {"),
    indexOfOrThrow(source, "async function loadFamilyStats() {"),
  );
  const streamBody = source.slice(
    indexOfOrThrow(source, "function openOverviewStream() {"),
    indexOfOrThrow(source, "/* ── Navigation ── */"),
  );

  assert.match(source, /let\s+overviewRequestSeq\s*=\s*0\s*;/);
  assert.match(source, /let\s+overviewStreamRevision\s*=\s*0\s*;/);
  assert.match(loadBody, /const\s+requestSeq\s*=\s*\+\+overviewRequestSeq\s*;/);
  assert.match(loadBody, /requestSeq\s*!==\s*overviewRequestSeq/);
  assert.match(loadBody, /streamRevisionAtStart\s*!==\s*overviewStreamRevision/);
  assert.match(streamBody, /overviewRequestSeq\s*\+=\s*1\s*;/);
  assert.match(streamBody, /currentEvents\s*=\s*normalizeJobEventList\(frame\.events\)\s*;/);
});
