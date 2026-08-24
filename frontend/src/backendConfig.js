export const DESKTOP_API_TOKEN_HEADER = "X-PEAP-Desktop-Token";

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function asText(value) {
  return String(value ?? "").trim();
}

function normalizeBackendConfig(value) {
  const source = asObject(value);
  return {
    baseUrl: asText(
      source.baseUrl
      || source.base_url
      || source.backendUrl
      || source.backend_url,
    ),
    apiToken: asText(
      source.apiToken
      || source.api_token
      || source.token,
    ),
  };
}

function readBridgeConfig(globalObject) {
  const root = asObject(globalObject);
  const windowObject = asObject(root.window);
  const peapDesktop = asObject(windowObject.peapDesktop || root.peapDesktop);
  if (typeof peapDesktop.getBackendConfig !== "function") {
    return { baseUrl: "", apiToken: "" };
  }
  try {
    return normalizeBackendConfig(peapDesktop.getBackendConfig());
  } catch {
    return { baseUrl: "", apiToken: "" };
  }
}

function readBootstrapConfig(globalObject) {
  const root = asObject(globalObject);
  const windowObject = asObject(root.window);
  const bootstrapState = asObject(
    windowObject.__PEAP_DESKTOP_BOOTSTRAP_STATE
    || root.__PEAP_DESKTOP_BOOTSTRAP_STATE,
  );
  for (const candidate of [
    bootstrapState.backendConfig,
    bootstrapState.backend_config,
    bootstrapState.backend,
    bootstrapState,
  ]) {
    const normalized = normalizeBackendConfig(candidate);
    if (normalized.baseUrl || normalized.apiToken) {
      return normalized;
    }
  }
  return { baseUrl: "", apiToken: "" };
}

export function resolveBrowserBackendConfig(globalObject = globalThis) {
  const bridgeConfig = readBridgeConfig(globalObject);
  if (bridgeConfig.baseUrl || bridgeConfig.apiToken) {
    return bridgeConfig;
  }
  return readBootstrapConfig(globalObject);
}
