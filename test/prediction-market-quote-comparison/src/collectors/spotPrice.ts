// Small shared helper used only by the *simulated* collectors (Bybit/MEXC
// placeholders) so their synthetic markets are struck around a realistic
// spot price instead of a random number. Real collectors for these platforms
// should instead read whatever price index the platform itself displays
// (see spec section 8.1 — matching the correct price index matters a lot
// more than matching a "close enough" number).
const FALLBACK_PRICES: Record<string, number> = { BTC: 65_000, ETH: 3_200, SOL: 150 };

let cache: { prices: Record<string, number>; fetchedAt: number } | null = null;
const CACHE_TTL_MS = 15_000;

export async function getSpotPrices(): Promise<Record<string, number>> {
  const now = Date.now();
  if (cache && now - cache.fetchedAt < CACHE_TTL_MS) {
    return cache.prices;
  }

  try {
    const res = await fetch(
      "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana&vs_currencies=usd",
      { cache: "no-store", headers: { accept: "application/json" } },
    );
    if (!res.ok) throw new Error(`coingecko ${res.status}`);
    const data = (await res.json()) as Record<string, { usd: number }>;
    const prices: Record<string, number> = {
      BTC: data.bitcoin?.usd ?? FALLBACK_PRICES.BTC,
      ETH: data.ethereum?.usd ?? FALLBACK_PRICES.ETH,
      SOL: data.solana?.usd ?? FALLBACK_PRICES.SOL,
    };
    cache = { prices, fetchedAt: now };
    return prices;
  } catch {
    // No network / rate-limited — fall back to a slow synthetic random walk
    // around the last known fallback so simulated markets still move a bit.
    const drifted = { ...FALLBACK_PRICES };
    for (const key of Object.keys(drifted)) {
      drifted[key] *= 1 + (Math.random() - 0.5) * 0.01;
    }
    cache = { prices: drifted, fetchedAt: now };
    return drifted;
  }
}
