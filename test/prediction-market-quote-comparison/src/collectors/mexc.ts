import type { Quote } from "@/lib/types";
import { config } from "@/lib/config";
import { getSpotPrices } from "./spotPrice";
import { hashToUnit, simulatedProbability, windowBounds } from "./simUtils";

// -----------------------------------------------------------------------
// PLACEHOLDER COLLECTOR — MEXC Prediction Market (prediction.mexc.com).
//
// Same situation as bybit.ts: no confirmed public API. Real steps:
//   1. Open https://prediction.mexc.com/prediction-markets/up-down with
//      DevTools -> Network -> Fetch/XHR (MEXC's main exchange API is
//      documented, but the *prediction* product is a separate, newer app —
//      check if it shares auth/infra with the main site).
//   2. Find the polling/WS endpoint for live probabilities.
//   3. Replace `fetchRaw()` below, flip SIMULATE_MEXC_PREDICTION=false.
//
// This generates 5-min AND 15-min Up/Down quotes (MEXC's two windows) with
// their own independent house margin, aligned to wall-clock window
// boundaries so they can be compared against Bybit's 5-min window under the
// same "same price index + same window, second for second" rule from spec
// section 8.1 (in real life you MUST verify both conditions before trusting
// a match — here we only align windows, we do not know the true index).
// -----------------------------------------------------------------------

const ASSETS = ["BTC", "ETH"];
const WINDOWS = [
  { seconds: 300, label: "5min" },
  { seconds: 900, label: "15min" },
];

export async function fetchMexcQuotes(): Promise<Quote[]> {
  const spot = await getSpotPrices();
  const fetchedAt = new Date().toISOString();
  const quotes: Quote[] = [];

  for (const asset of ASSETS) {
    for (const win of WINDOWS) {
      const { start, end, index } = windowBounds(Date.now(), win.seconds);
      const pUp = simulatedProbability(`mexc:updown:${asset}:${win.label}`, index, 0.25);
      const margin = 0.0 + hashToUnit(`mexc_margin:${asset}:${win.label}:${index}`) * 0.015; // MEXC advertises 0 fees
      const half = margin / 2;
      const marketId = `MEXC-${asset}USDT-UPDOWN-${win.label}-${index}`;

      quotes.push({
        platform: "mexc_prediction",
        marketId,
        rawTitle: `${asset} up in next ${win.label} (${start.toISOString()} - ${end.toISOString()})`,
        outcome: "up",
        impliedProbability: Math.min(0.97, pUp + half),
        marketType: "fixed_odds",
        expiry: end.toISOString(),
        fetchedAt,
        isSimulated: true,
        meta: {
          asset,
          contractType: "updown",
          windowSeconds: win.seconds,
          windowStart: start.toISOString(),
          windowEnd: end.toISOString(),
          priceIndexSource: "mexc-mark-price (assumed, unverified)",
          fee: config.fees.mexc_prediction,
          dataSource: "SIMULATED placeholder — replace with real MEXC endpoint",
        },
      });
      quotes.push({
        platform: "mexc_prediction",
        marketId,
        rawTitle: `${asset} down in next ${win.label} (${start.toISOString()} - ${end.toISOString()})`,
        outcome: "down",
        impliedProbability: Math.min(0.97, 1 - pUp + half),
        marketType: "fixed_odds",
        expiry: end.toISOString(),
        fetchedAt,
        isSimulated: true,
        meta: {
          asset,
          contractType: "updown",
          windowSeconds: win.seconds,
          windowStart: start.toISOString(),
          windowEnd: end.toISOString(),
          priceIndexSource: "mexc-mark-price (assumed, unverified)",
          fee: config.fees.mexc_prediction,
          dataSource: "SIMULATED placeholder — replace with real MEXC endpoint",
        },
      });
    }
  }

  void spot; // reserved for future target-style MEXC contracts
  return quotes;
}
