const DEFAULT_READY_PATH = "/api/ready";

async function waitForBackend(
  baseUrl,
  {
    readyPath = DEFAULT_READY_PATH,
    timeoutMs = 60000,
    intervalMs = 500,
    headers = undefined,
    fetchFn = globalThis.fetch,
    sleepFn = (delayMs) => new Promise((resolve) => setTimeout(resolve, delayMs)),
    nowFn = () => Date.now(),
    getFailure = () => null,
  } = {},
) {
  const started = nowFn();
  while (nowFn() - started < timeoutMs) {
    const failure = getFailure();
    if (failure) {
      throw failure;
    }
    try {
      const requestOptions = headers ? { headers } : undefined;
      const response = await fetchFn(`${baseUrl}${readyPath}`, requestOptions);
      if (response && response.ok) {
        return;
      }
    } catch (error) {
      // Ignore connection failures until timeout or explicit backend failure.
    }
    await sleepFn(intervalMs);
  }
  throw new Error(`Backend did not become ready within ${timeoutMs}ms`);
}

module.exports = {
  DEFAULT_READY_PATH,
  waitForBackend,
};
