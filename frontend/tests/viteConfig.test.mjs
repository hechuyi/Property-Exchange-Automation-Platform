import test from "node:test";
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

async function loadViteConfig(t, env = {}) {
  const previousEnv = {
    PEAP_APP_API_TOKEN: process.env.PEAP_APP_API_TOKEN,
    PEAP_FRONTEND_API_TOKEN: process.env.PEAP_FRONTEND_API_TOKEN,
    PEAP_FRONTEND_BACKEND_TARGET: process.env.PEAP_FRONTEND_BACKEND_TARGET,
    PEAP_FRONTEND_PORT: process.env.PEAP_FRONTEND_PORT,
  };
  t.after(() => {
    for (const [key, value] of Object.entries(previousEnv)) {
      if (value === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = value;
      }
    }
  });

  for (const key of Object.keys(previousEnv)) {
    delete process.env[key];
  }
  for (const [key, value] of Object.entries(env)) {
    process.env[key] = value;
  }

  const configUrl = new URL(`../vite.config.js?case=${Date.now()}-${Math.random()}`, import.meta.url);
  const imported = await import(pathToFileURL(configUrl.pathname).href + configUrl.search);
  return imported.default;
}

test("vite dev proxy forwards desktop token header when backend auth is enabled", async (t) => {
  const config = await loadViteConfig(t, {
    PEAP_APP_API_TOKEN: "desktop-secret",
  });

  assert.equal(
    config.server.proxy["/api/"].headers["X-PEAP-Desktop-Token"],
    "desktop-secret",
  );
});

test("vite dev server uses fixed configured port and backend target", async (t) => {
  const config = await loadViteConfig(t, {
    PEAP_FRONTEND_BACKEND_TARGET: "http://127.0.0.1:49999",
    PEAP_FRONTEND_PORT: "5199",
  });

  assert.equal(config.server.port, 5199);
  assert.equal(config.server.strictPort, true);
  assert.equal(config.server.proxy["/api/"].target, "http://127.0.0.1:49999");
});

test("vite dev server falls back to default port for invalid frontend port", async (t) => {
  const config = await loadViteConfig(t, {
    PEAP_FRONTEND_PORT: "not-a-number",
  });

  assert.equal(config.server.port, 5173);
  assert.equal(config.server.strictPort, true);
});
