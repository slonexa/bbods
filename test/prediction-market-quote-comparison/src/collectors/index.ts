import type { CollectorResult, Quote } from "@/lib/types";
import { config } from "@/lib/config";
import { fetchPolymarketQuotes } from "./polymarket";
import { fetchBybitOddsQuotes, type ReferenceTarget } from "./bybit";
import { fetchMexcQuotes } from "./mexc";

export const PLATFORMS = ["polymarket", "bybit_odds", "mexc_prediction"] as const;
export type PlatformName = (typeof PLATFORMS)[number];

async function timed(platform: string, isSimulated: boolean, fn: () => Promise<Quote[]>): Promise<CollectorResult> {
  const startedAt = Date.now();
  try {
    const quotes = await fn();
    return { platform, ok: true, quotes, durationMs: Date.now() - startedAt, isSimulated };
  } catch (err) {
    return {
      platform,
      ok: false,
      quotes: [],
      error: err instanceof Error ? err.message : String(err),
      durationMs: Date.now() - startedAt,
      isSimulated,
    };
  }
}

// Runs every collector independently (one platform failing/rate-limiting
// never blocks the others) and returns a per-platform status report plus the
// combined list of quotes for this cycle.
export async function runAllCollectors(): Promise<CollectorResult[]> {
  const polymarketResult = await timed("polymarket", false, fetchPolymarketQuotes);

  // Demo convenience: feed the real Polymarket target thresholds into the
  // simulated Bybit collector so the dashboard has meaningful matches to show
  // right away. A real Bybit collector would NOT depend on Polymarket at all
  // — this wiring only exists because Bybit has no real data source yet.
  const referenceTargets: ReferenceTarget[] = polymarketResult.quotes
    .filter((q) => q.meta.contractType === "target" && q.meta.threshold && q.expiry)
    .map((q) => ({ asset: q.meta.asset as string, threshold: q.meta.threshold as number, expiry: q.expiry as string }))
    .filter((t, idx, arr) => arr.findIndex((o) => o.asset === t.asset && o.threshold === t.threshold) === idx);

  const [bybitResult, mexcResult] = await Promise.all([
    timed("bybit_odds", config.simulate.bybit_odds, () => fetchBybitOddsQuotes(referenceTargets)),
    timed("mexc_prediction", config.simulate.mexc_prediction, fetchMexcQuotes),
  ]);

  return [polymarketResult, bybitResult, mexcResult];
}
