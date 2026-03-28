import { createDesktopProductError } from "./errors";

export const DESKTOP_API_TOKEN_HEADER = "X-PEAP-Desktop-Token";
const SMOKE_FETCH_TRACE_KEY = "__PEAP_DESKTOP_SMOKE_FETCH_TRACE";

export function buildDesktopHeaders(
  apiToken: string,
  extraHeaders: Record<string, string> = {},
): Record<string, string> {
  return {
    "Content-Type": "application/json",
    ...(apiToken ? { [DESKTOP_API_TOKEN_HEADER]: apiToken } : {}),
    ...(extraHeaders || {}),
  };
}

type DesktopHttpClientOptions = {
  baseUrl: string;
  apiToken: string;
  fetchFn?: typeof fetch;
};

function appendSmokeFetchTrace(entry: { url: string; method: string; status: number; ok: boolean; error?: string }) {
  const traceTarget = globalThis as typeof globalThis & Record<string, unknown>;
  const trace = traceTarget[SMOKE_FETCH_TRACE_KEY];
  if (!Array.isArray(trace)) {
    return;
  }
  trace.push(entry);
}

export function createDesktopHttpClient({
  baseUrl,
  apiToken,
  fetchFn = globalThis.fetch,
}: DesktopHttpClientOptions) {
  return async function request(path: string, options: RequestInit = {}) {
    const url = `${baseUrl}${path}`;
    const method = String(options.method || "GET").toUpperCase();
    let response;
    try {
      response = await fetchFn(url, {
        method,
        ...options,
        headers: buildDesktopHeaders(
          apiToken,
          (options.headers || {}) as Record<string, string>,
        ),
      });
    } catch (error) {
      appendSmokeFetchTrace({
        url,
        method,
        status: 0,
        ok: false,
        error: String((error as Error)?.message || error || "fetch failed"),
      });
      throw error;
    }
    const payload = await response.json().catch(() => ({}));
    appendSmokeFetchTrace({
      url,
      method,
      status: Number(response.status || 0),
      ok: Boolean(response.ok),
      ...(response.ok ? {} : { detail: String(payload?.error || `HTTP ${response.status}`) }),
    });
    if (!response.ok) {
      throw createDesktopProductError({
        status: response.status,
        detail: String(payload?.error || `HTTP ${response.status}`),
      });
    }
    return payload;
  };
}

export type DesktopHttpClient = ReturnType<typeof createDesktopHttpClient>;
