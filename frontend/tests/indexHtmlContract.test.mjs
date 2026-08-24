import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

test("frontend shell does not depend on external network resources", async () => {
  const html = await readFile(new URL("../index.html", import.meta.url), "utf8");
  const externalResources = Array.from(
    html.matchAll(/\b(?:src|href)=["']https?:\/\/[^"']+["']/gi),
    (match) => match[0],
  );

  assert.deepEqual(
    externalResources,
    [],
    `frontend/index.html must remain hermetic for desktop smoke tests; found: ${externalResources.join(", ")}`,
  );
});

test("frontend shell exposes export history as a normal user navigation panel", async () => {
  const html = await readFile(new URL("../index.html", import.meta.url), "utf8");

  assert.match(html, /data-panel="export-history"/);
  assert.match(html, /switchPanel\('export-history'\)/);
  assert.match(html, />导出历史</);
  assert.match(html, /id="panel-export-history"/);
});
