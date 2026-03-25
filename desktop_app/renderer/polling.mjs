export function createPollLoop({
  isPaused,
  loadOverview,
  loadJobs,
  loadJobEvents,
  getCurrentPanel,
  loadRecords,
  loadMappings,
  isMappingEditorActive,
  onError = () => {},
} = {}) {
  return async function pollLoop() {
    if (isPaused()) {
      return;
    }
    try {
      await loadOverview();
      await loadJobs();
      await loadJobEvents();
      if (getCurrentPanel() === "records") {
        await loadRecords();
      } else if (getCurrentPanel() === "mappings" && !isMappingEditorActive()) {
        await loadMappings();
      }
    } catch (error) {
      onError(error);
    }
  };
}

export function startPolling({ pollLoop, intervalMs = 1500 } = {}) {
  void pollLoop();
  return setInterval(() => {
    void pollLoop();
  }, intervalMs);
}
