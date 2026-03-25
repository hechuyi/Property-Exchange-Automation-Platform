export const DESKTOP_API_TOKEN_HEADER = "X-PEAP-Desktop-Token";

export function buildDesktopHeaders(apiToken, extraHeaders = {}) {
  return {
    "Content-Type": "application/json",
    ...(apiToken ? { [DESKTOP_API_TOKEN_HEADER]: apiToken } : {}),
    ...(extraHeaders || {}),
  };
}

export function createApiClient({
  baseUrl,
  apiToken,
  fetchFn = globalThis.fetch,
} = {}) {
  return async function api(path, options = {}) {
    const response = await fetchFn(`${baseUrl}${path}`, {
      ...options,
      headers: buildDesktopHeaders(apiToken, options.headers),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    return payload;
  };
}

export async function waitForDesktopBackendAvailability({
  baseUrl,
  apiToken,
  timeoutMs = 60000,
  fetchFn = globalThis.fetch,
  sleepFn = (delayMs) => new Promise((resolve) => setTimeout(resolve, delayMs)),
} = {}) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const response = await fetchFn(`${baseUrl}/api/ready`, {
        headers: apiToken ? { [DESKTOP_API_TOKEN_HEADER]: apiToken } : undefined,
      });
      if (response.ok) {
        return;
      }
      lastError = new Error(`HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await sleepFn(300);
  }
  throw lastError || new Error("后台服务启动超时");
}
