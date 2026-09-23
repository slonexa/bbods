import { getSpot, type SpotTick } from "./spot";
import type { ModelAsset } from "./market-model";

/**
 * Кольцевой буфер тиков спота. Нужен лаборатории лага: без внешнего эталонного ряда
 * цен невозможно понять, лагает ли площадка при обновлении коэффициента (раздел 4.4).
 */

const LIMIT = 300;
const series = new Map<ModelAsset, SpotTick[]>();

export function recordSpot(tick: SpotTick): void {
  const list = series.get(tick.asset) ?? [];
  const last = list[list.length - 1];
  if (last && Date.now() - last.at < 200) return;
  list.push(tick);
  if (list.length > LIMIT) list.splice(0, list.length - LIMIT);
  series.set(tick.asset, list);
}

export function spotSeries(asset: ModelAsset): SpotTick[] {
  return series.get(asset) ?? [];
}

export async function recordSpotNow(asset: ModelAsset): Promise<SpotTick> {
  const tick = await getSpot(asset);
  recordSpot(tick);
  return tick;
}
