import { buildActiveJobBlockMessage } from "./index.js";

export async function runActiveJobGuardedAction({
  fetchOverview,
  actionLabel,
  execute,
} = {}) {
  const latestOverview = await fetchOverview();
  const activeJobBlockMessage = buildActiveJobBlockMessage(latestOverview, actionLabel);
  if (activeJobBlockMessage) {
    throw Object.assign(new Error(activeJobBlockMessage), { localOnly: true });
  }
  return execute(latestOverview);
}
