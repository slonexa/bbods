// Central, tweakable configuration. Nothing here is hardcoded into the logic
// itself — everything can be overridden with env vars, per the spec's
// "не хардкодь" requirement for thresholds/fees.

function num(name: string, fallback: number): number {
  const v = process.env[name];
  if (!v) return fallback;
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

export const config = {
  // How often the polling loop runs a full collect -> normalize -> engine cycle.
  pollIntervalMs: num("POLL_INTERVAL_MS", 10_000),

  // Estimated per-platform trading fee + slippage, expressed as a fraction of
  // stake. These are rough placeholders — replace with real fee schedules once
  // you have live order-book depth to estimate slippage from.
  fees: {
    polymarket: num("FEE_POLYMARKET", 0.02),
    bybit_odds: num("FEE_BYBIT_ODDS", 0.02),
    mexc_prediction: num("FEE_MEXC_PREDICTION", 0.0), // MEXC advertises zero fees
  } as Record<string, number>,

  // Additional flat slippage buffer applied on top of the two platform fees.
  extraSlippageBuffer: num("EXTRA_SLIPPAGE_BUFFER", 0.005),

  // Alert threshold — spreads below this are still logged (for later stats)
  // but are not surfaced as "actionable" on the dashboard by default.
  defaultAlertThreshold: num("MIN_SPREAD_THRESHOLD", 0.02),

  // Tolerance windows used by the normalizer when matching crypto target
  // contracts across platforms (see spec section 3).
  matching: {
    thresholdTolerancePct: num("MATCH_THRESHOLD_TOLERANCE_PCT", 0.01), // 1%
    expiryToleranceMinutes: num("MATCH_EXPIRY_TOLERANCE_MIN", 60 * 24), // 1 day
    updownWindowToleranceSeconds: num("MATCH_UPDOWN_WINDOW_TOLERANCE_SEC", 5),
  },

  // Feature flags: whether a collector is allowed to run in "simulated" mode
  // when no confirmed public/reverse-engineered endpoint is wired up yet.
  // Flip these to false once you plug in a real scraped endpoint.
  simulate: {
    bybit_odds: (process.env.SIMULATE_BYBIT_ODDS ?? "true") !== "false",
    mexc_prediction: (process.env.SIMULATE_MEXC_PREDICTION ?? "true") !== "false",
  },

  maxRawQuoteRowsKeep: num("MAX_RAW_QUOTE_ROWS", 20_000),
};
