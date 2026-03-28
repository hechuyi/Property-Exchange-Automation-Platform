export function createAsyncPoller(task: () => Promise<void>, intervalMs = 1500) {
  let timer: ReturnType<typeof setInterval> | null = null;

  return {
    async runOnce() {
      await task();
    },
    start() {
      if (timer) {
        return timer;
      }
      void task();
      timer = setInterval(() => {
        void task();
      }, intervalMs);
      return timer;
    },
    stop() {
      if (timer) {
        clearInterval(timer);
        timer = null;
      }
    },
  };
}
