import { describeError, fetchJson } from "./http";
import { priceWalk, type ModelAsset } from "./market-model";

export interface SpotTick {
  asset: ModelAsset;
  price: number;
  source: "binance" | "coinbase" | "model";
  at: number;
  prev: number | null;
}

const cache = new Map<ModelAsset, SpotTick>();
const failedSources = new Set<string>();
const TTL_MS = 2500;

interface SpotSource {
  source: SpotTick["source"];
  url: (asset: ModelAsset) => string;
  pick: (data: unknown) => number;
}

const SOURCES: SpotSource[] = [
  {
    source: "binance",
    url: (asset) => `https://api.binance.com/api/v3/ticker/price?symbol=${asset}USDT`,
    pick: (data) => Number((data as { price?: string } | null)?.price),
  },
  {
    source: "coinbase",
    url: (asset) => `https://api.exchange.coinbase.com/products/${asset}-USD/ticker`,
    pick: (data) => Number((data as { price?: string } | null)?.price),
  },
];

/**
 * Живой тик спота — эталон для лаборатории лага (раздел 4.4):
 * сравниваем, когда площадка обновила коэффициент, против движения реальной цены.
 * Если внешние фиды недоступны (нет сети / блокировка) — тихо падаем на модель.
 */
export async function getSpot(asset: ModelAsset): Promise<SpotTick> {
  const hit = cache.get(asset) ?? null;
  if (hit && Date.now() - hit.at < TTL_MS) return hit;

  for (const s of SOURCES) {
    try {
      const { data } = await fetchJson<unknown>({ url: s.url(asset), timeoutMs: 3500, minIntervalMs: 1200 });
      const price = s.pick(data);
      if (Number.isFinite(price) && price > 0) {
        const tick: SpotTick = { asset, price, source: s.source, at: Date.now(), prev: hit?.price ?? null };
        cache.set(asset, tick);
        return tick;
      }
    } catch (error) {
      // не спамим лог: каждый источник помечаем как упавший и предупредаем один раз
      if (!failedSources.has(s.source)) {
        failedSources.add(s.source);
        console.warn(`[spot] ${s.source} недоступен: ${describeError(error)} — переключаюсь на следующий`);
      }
    }
  }

  const tick: SpotTick = {
    asset,
    price: priceWalk(asset, Date.now()),
    source: "model",
    at: Date.now(),
    prev: hit?.price ?? null,
  };
  cache.set(asset, tick);
  return tick;
}

export function spotChangePct(tick: SpotTick): number | null {
  if (tick.prev === null || !Number.isFinite(tick.prev) || tick.prev === 0) return null;
  return ((tick.price - tick.prev) / tick.prev) * 100;
}
