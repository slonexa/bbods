// Shared helpers for the *simulated* collectors (Bybit Odds / MEXC Prediction
// Market). Neither platform has a confirmed public/reverse-engineered
// endpoint yet (spec section 2/8), so these collectors produce structurally
// realistic placeholder data — same shape a real collector would return —
// so the rest of the pipeline (normalizer/engine/dashboard) can be built and
// tested end-to-end today. Swap `fetchRaw()` in bybit.ts / mexc.ts for a real
// HTTP call once you've found the endpoint via DevTools, everything else
// keeps working unchanged.

export function hashToUnit(seed: string): number {
  let h = 0;
  for (let i = 0; i < seed.length; i++) {
    h = (h * 31 + seed.charCodeAt(i)) >>> 0;
  }
  return (h % 100000) / 100000;
}

export function windowBounds(nowMs: number, windowSeconds: number): { start: Date; end: Date; index: number } {
  const windowMs = windowSeconds * 1000;
  const index = Math.floor(nowMs / windowMs);
  const start = new Date(index * windowMs);
  const end = new Date((index + 1) * windowMs);
  return { start, end, index };
}

export function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

// Stable-ish "base" probability for a given key within a time window, plus a
// small live jitter so repeated polls aren't perfectly static.
export function simulatedProbability(seedKey: string, windowIndex: number, spread = 0.3): number {
  const base = 0.5 + (hashToUnit(`${seedKey}:${windowIndex}`) - 0.5) * spread;
  const jitter = (hashToUnit(`${seedKey}:${windowIndex}:${Math.floor(Date.now() / 4000)}`) - 0.5) * 0.02;
  return clamp(base + jitter, 0.03, 0.97);
}

// Logistic-ish probability that spot price ends up above `threshold` by
// `daysToExpiry`, used to strike semi-realistic target contracts.
export function targetProbabilityAbove(spot: number, threshold: number, daysToExpiry: number): number {
  const distancePct = (threshold - spot) / spot;
  const sensitivity = 6 / Math.max(1, Math.sqrt(daysToExpiry));
  const p = 1 / (1 + Math.exp(distancePct * sensitivity * 10));
  return clamp(p, 0.02, 0.98);
}
