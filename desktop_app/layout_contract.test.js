const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");

const app = fs.readFileSync(path.join(__dirname, "src", "App.tsx"), "utf8");
const appShell = fs.readFileSync(path.join(__dirname, "src", "app-shell.tsx"), "utf8");
const navigationPath = path.join(__dirname, "src", "features", "shell", "navigation.ts");
const navigation = fs.existsSync(navigationPath) ? fs.readFileSync(navigationPath, "utf8") : "";
const overviewPage = fs.readFileSync(path.join(__dirname, "src", "pages", "OverviewPage.tsx"), "utf8");
const recordsPage = fs.readFileSync(path.join(__dirname, "src", "pages", "RecordsPage.tsx"), "utf8");
const mappingsPage = fs.readFileSync(path.join(__dirname, "src", "pages", "MappingsPage.tsx"), "utf8");
const useOverview = fs.readFileSync(path.join(__dirname, "src", "features", "overview", "useOverview.ts"), "utf8");
const renderer = fs.readFileSync(path.join(__dirname, "renderer.js"), "utf8");
const tasksModule = fs.readFileSync(path.join(__dirname, "renderer", "tasks.mjs"), "utf8");
const recordsModule = fs.readFileSync(path.join(__dirname, "renderer", "records.mjs"), "utf8");
const mappingsModule = fs.readFileSync(path.join(__dirname, "renderer", "mappings.mjs"), "utf8");
const rendererApi = fs.readFileSync(path.join(__dirname, "renderer", "api.mjs"), "utf8");
const main = fs.readFileSync(path.join(__dirname, "main.js"), "utf8");
const preload = fs.readFileSync(path.join(__dirname, "preload.js"), "utf8");
const mainEntry = fs.readFileSync(path.join(__dirname, "src", "main.tsx"), "utf8");
const appAst = ts.createSourceFile("App.tsx", app, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const navigationAst = ts.createSourceFile("navigation.ts", navigation, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);

function collectNodes(root, predicate) {
  const matches = [];
  const visit = (node) => {
    if (predicate(node)) {
      matches.push(node);
    }
    ts.forEachChild(node, visit);
  };
  visit(root);
  return matches;
}

test("desktop rail exposes a dedicated tasks panel instead of a global sidebar", () => {
  assert.match(navigation, /navWorkbench/);
  assert.match(navigation, /label: "工作台"/);
  assert.match(app, /from "\.\/features\/shell\/navigation"/);
  assert.match(app, /workbench:\s*lazy\(\(\)\s*=>\s*import\("\.\/pages\/OverviewPage"\)\)/);
  assert.match(app, /DESKTOP_PANEL_KEYS\.map\(\(key\) => \(\{ name: key, list: `\/\$\{key\}` \}\)\)/);
  assert.match(appShell, /DESKTOP_PRIMARY_NAVIGATION_ITEMS/);
  assert.match(appShell, /DESKTOP_SECONDARY_NAVIGATION_ITEMS/);
  assert.doesNotMatch(appShell, /navTasks/);
  assert.doesNotMatch(appShell, /label: "任务"/);
  assert.doesNotMatch(app, /tasks:\s*lazy\(\(\)\s*=>\s*import\("\.\/pages\/TasksPage"\)\)/);
  assert.doesNotMatch(appShell, /workspace-sidebar/);
});

test("app panel contract removes source-level test hack and uses finite key set", () => {
  assert.doesNotMatch(app, /TASKS_PANEL_LAYOUT_CONTRACT/);

  const panelKeysNode = collectNodes(
    navigationAst,
    (node) => ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.name.text === "DESKTOP_PANEL_KEYS",
  )[0];
  assert.ok(panelKeysNode, "expected DESKTOP_PANEL_KEYS declaration");
  assert.ok(panelKeysNode.initializer && ts.isAsExpression(panelKeysNode.initializer), "DESKTOP_PANEL_KEYS should use `as const`");

  const panelKeysExpr = panelKeysNode.initializer.expression;
  assert.ok(ts.isArrayLiteralExpression(panelKeysExpr), "DESKTOP_PANEL_KEYS should be an array literal");
  const panelKeys = panelKeysExpr.elements.map((element) => (ts.isStringLiteral(element) ? element.text : ""));
  assert.deepEqual(panelKeys, ["workbench", "records", "mappings", "settings"]);

  const primaryKeysNode = collectNodes(
    navigationAst,
    (node) => ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.name.text === "DESKTOP_PRIMARY_PANEL_KEYS",
  )[0];
  assert.ok(primaryKeysNode, "expected DESKTOP_PRIMARY_PANEL_KEYS declaration");
  assert.match(app, /useState<DesktopPanelKey>\("workbench"\)/);
  assert.doesNotMatch(app, /PAGE_COMPONENTS\[.*\]\s*\|\|\s*PAGE_COMPONENTS\.workbench/);
});

test("lazy page rendering is wrapped by explicit error boundary", () => {
  const errorBoundaryClassNode = collectNodes(
    appAst,
    (node) => ts.isClassDeclaration(node) && Boolean(node.name) && node.name.text === "LazyPageErrorBoundary",
  )[0];
  assert.ok(errorBoundaryClassNode, "expected LazyPageErrorBoundary class");
  assert.match(app, /<LazyPageErrorBoundary[\s\S]*<ActivePage \/>[\s\S]*<\/LazyPageErrorBoundary>/);
});

test("renderer bootstrap publishes explicit ready and error contract", () => {
  assert.match(mainEntry, /__PEAP_DESKTOP_BOOTSTRAP_STATE/);
  assert.match(mainEntry, /ready:\s*false/);
  assert.match(app, /publishDesktopBootstrapState/);
  assert.match(app, /ready:\s*Boolean\(backendConfig\)/);
  assert.match(app, /error:\s*bootstrapError/);
});

test("task failure hint points to the tasks panel", () => {
  assert.match(renderer, /任务页/);
  assert.doesNotMatch(renderer, /数据记录页的任务明细/);
});

test("capacity-limited desktop lists surface explicit truncation copy", () => {
  assert.match(recordsModule, /只显示前/);
  assert.match(mappingsModule, /只显示前/);
  assert.match(renderer, /payload\.truncated/);
});

test("desktop task and mapping failures are business-facing rather than raw internals", () => {
  assert.match(tasksModule, /映射回刷失败/);
  assert.match(mappingsModule, /规则保存失败，请到任务页查看明细/);
  assert.doesNotMatch(tasksModule, /preview endpoint failed/);
  assert.doesNotMatch(mappingsModule, /preview endpoint failed/);
});

test("export action participates in task tracking and uses export-specific task copy", () => {
  assert.match(renderer, /job\.job_type === "export_excel"/);
  assert.match(renderer, /selectedJobId = payload\.job_id \|\| selectedJobId/);
  assert.match(renderer, /生成文件/);
});

test("records panel exposes date and keyword filters for direct database inspection", () => {
  assert.match(recordsPage, /id="recordsDateFromInput"/);
  assert.match(recordsPage, /id="recordsDateToInput"/);
  assert.match(recordsPage, /id="recordsKeywordInput"/);
  assert.match(renderer, /date_from/);
  assert.match(renderer, /keyword/);
});

test("saved mapping rules are visible without forcing a scroll-to-bottom toggle flow", () => {
  assert.match(mappingsPage, /id="mappingEntriesTableWrap"/);
  assert.match(mappingsPage, /className="records-table-wrap compact-list"/);
  assert.doesNotMatch(mappingsPage, /收起规则表/);
});

test("homepage first screen keeps one-click export and manual import in the primary action grid", () => {
  assert.match(overviewPage, /id="homePrimaryActions"[\s\S]*id="runOneClickBtn"[\s\S]*id="runManualImportBtn"[\s\S]*id="runExportBtn"/);
  assert.match(overviewPage, /id="statPendingMappingCard"/);
});

test("mappings panel exposes a batch pending refresh action instead of moving it to homepage", () => {
  const batchIndex = mappingsPage.indexOf('id="runPendingMappingRefreshBtn"');
  const overviewIndex = overviewPage.indexOf('id="runPendingMappingRefreshBtn"');
  assert.notEqual(batchIndex, -1);
  assert.equal(overviewIndex, -1);
});

test("overview exposes a force-stop control wired to backend restart", () => {
  assert.match(overviewPage, /id="forceStopBtn"/);
  assert.match(useOverview, /window\.peapDesktop\?\.restartBackend/);
  assert.match(preload, /restartBackend:\s*\(\)\s*=> ipcRenderer\.invoke\("peap:restart-backend"\)/);
  assert.match(main, /ipcMain\.handle\("peap:restart-backend"/);
});

test("desktop window no longer blocks first paint on backend readiness", () => {
  assert.doesNotMatch(main, /async function createMainWindow\(\)\s*\{[\s\S]*waitForBackend\(/);
  assert.match(rendererApi, /\/api\/ready/);
});
