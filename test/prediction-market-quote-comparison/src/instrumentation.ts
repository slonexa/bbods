// Next.js calls `register()` once when the server process starts (not during
// `next build`). We use it to kick off the collector polling loop described
// in the spec — no separate process/cron needed for this MVP, everything
// lives inside the one Next.js server.
export async function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;

  const { markPollerStarted } = await import("@/engine/status");
  if (!markPollerStarted()) return; // avoid double-start on hot reload

  const { runCycle } = await import("@/engine/run");
  const { config } = await import("@/lib/config");

  // Run once immediately so the dashboard has data right away, then keep
  // polling at the configured interval (default 10s — within the spec's
  // recommended 5-15s range for polite polling).
  runCycle().catch((err) => console.error("[poller] initial cycle failed", err));
  setInterval(() => {
    runCycle().catch((err) => console.error("[poller] cycle failed", err));
  }, config.pollIntervalMs);
}
