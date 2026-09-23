import type { Quote } from "@/lib/types";
import { config } from "@/lib/config";
import { getSpotPrices } from "./spotPrice";
import { hashToUnit, simulatedProbability, targetProbabilityAbove, windowBounds } from "./simUtils";

// -----------------------------------------------------------------------
// PLACEHOLDER COLLECTOR — Bybit Odds / Bybit Prediction.
//
// Bybit Odds (launched ~Sep 2026) and Bybit Prediction (~Jun 2026) do not
// have a confirmed public API at the time this was written. Per the spec
// (section 2), the real implementation should:
//   1. Open https://www.bybit.com/en/trade/odds/ (or /prediction/) in a
//      browser with DevTools -> Network -> Fetch/XHR (or WS) open.
//   2. Find the internal endpoint the page itself polls/subscribes to for
//      live odds (likely something under bybit.com/x-api/ or a WS topic).
//   3. Replace `fetchRaw()` below with a real `fetch()`/WS client against
//      that endpoint, keeping the same output shape.
//   4. Flip `SIMULATE_BYBIT_ODDS=false` in the environment once it's wired up.
//
// Until then this generates structurally realistic fixed-odds quotes (own
// house margin baked in, same field shape) so normalizer/engine/dashboard
// can be exercised end-to-end.
// -----------------------------------------------------------------------

export interface ReferenceTarget {
  asset: string;
  threshold: number;
  expiry: string; // ISO
}

const UPDOWN_ASSETS = ["BTC", "ETH"];
const UPDOWN_WINDOW_SECONDS = 300; // Bybit Odds 5-minute Up/Down

function buildUpDownQuotes(spot: Record<string, number>, fetchedAt: string): Quote[] {
  const quotes: Quote[] = [];
  for (const asset of UPDOWN_ASSETS) {
    const { start, end, index } = windowBounds(Date.now(), UPDOWN_WINDOW_SECONDS);
    const pUp = simulatedProbability(`bybit_odds:updown:${asset}`, index, 0.25);
    const margin = 0.03 + hashToUnit(`bybit_margin:${asset}:${index}`) * 0.02;
    const half = margin / 2;

    quotes.push({
      platform: "bybit_odds",
      marketId: `BYBIT-${asset}USDT-UPDOWN-${index}`,
      rawTitle: `${asset} price up in next 5 min (${start.toISOString()} - ${end.toISOString()})`,
      outcome: "up",
      impliedProbability: Math.min(0.97, pUp + half),
      marketType: "fixed_odds",
      expiry: end.toISOString(),
      fetchedAt,
      isSimulated: true,
      meta: {
        asset,
        contractType: "updown",
        windowSeconds: UPDOWN_WINDOW_SECONDS,
        windowStart: start.toISOString(),
        windowEnd: end.toISOString(),
        priceIndexSource: "bybit-mark-price (assumed, unverified)",
        fee: config.fees.bybit_odds,
        dataSource: "SIMULATED placeholder — replace with real Bybit endpoint",
      },
    });
    quotes.push({
      platform: "bybit_odds",
      marketId: `BYBIT-${asset}USDT-UPDOWN-${index}`,
      rawTitle: `${asset} price down in next 5 min (${start.toISOString()} - ${end.toISOString()})`,
      outcome: "down",
      impliedProbability: Math.min(0.97, 1 - pUp + half),
      marketType: "fixed_odds",
      expiry: end.toISOString(),
      fetchedAt,
      isSimulated: true,
      meta: {
        asset,
        contractType: "updown",
        windowSeconds: UPDOWN_WINDOW_SECONDS,
        windowStart: start.toISOString(),
        windowEnd: end.toISOString(),
        priceIndexSource: "bybit-mark-price (assumed, unverified)",
        fee: config.fees.bybit_odds,
        dataSource: "SIMULATED placeholder — replace with real Bybit endpoint",
      },
    });
  }
  return quotes;
}

function buildTargetQuotes(
  spot: Record<string, number>,
  fetchedAt: string,
  referenceTargets: ReferenceTarget[],
): Quote[] {
  const quotes: Quote[] = [];
  const targets =
    referenceTargets.length > 0
      ? referenceTargets
      : ["BTC", "ETH"].flatMap((asset) => {
          const s = spot[asset] ?? 1;
          const now = Date.now();
          return [0.1, -0.1].map((pct, i) => ({
            asset,
            threshold: Math.round((s * (1 + pct)) / 100) * 100,
            expiry: new Date(now + (30 + i * 30) * 86_400_000).toISOString(),
          }));
        });

  for (const target of targets.slice(0, 12)) {
    const s = spot[target.asset];
    if (!s) continue;
    const daysToExpiry = Math.max(1, (new Date(target.expiry).getTime() - Date.now()) / 86_400_000);
    const baseProb = targetProbabilityAbove(s, target.threshold, daysToExpiry);
    const noise = (hashToUnit(`bybit_target_noise:${target.asset}:${target.threshold}`) - 0.5) * 0.1;
    const margin = 0.03;
    const probAbove = Math.min(0.97, Math.max(0.03, baseProb + noise + margin / 2));

    const marketId = `BYBIT-${target.asset}-TARGET-${target.threshold}-${target.expiry.slice(0, 10)}`;
    quotes.push({
      platform: "bybit_odds",
      marketId,
      rawTitle: `${target.asset} above $${target.threshold.toLocaleString()} by ${target.expiry.slice(0, 10)}`,
      outcome: "above",
      impliedProbability: probAbove,
      marketType: "fixed_odds",
      expiry: target.expiry,
      fetchedAt,
      isSimulated: true,
      meta: {
        asset: target.asset,
        contractType: "target",
        threshold: target.threshold,
        direction: "above",
        priceIndexSource: "bybit-index-price (assumed, unverified)",
        fee: config.fees.bybit_odds,
        dataSource: "SIMULATED placeholder — replace with real Bybit endpoint",
      },
    });
  }
  return quotes;
}

export async function fetchBybitOddsQuotes(referenceTargets: ReferenceTarget[] = []): Promise<Quote[]> {
  const spot = await getSpotPrices();
  const fetchedAt = new Date().toISOString();
  return [...buildUpDownQuotes(spot, fetchedAt), ...buildTargetQuotes(spot, fetchedAt, referenceTargets)];
}
