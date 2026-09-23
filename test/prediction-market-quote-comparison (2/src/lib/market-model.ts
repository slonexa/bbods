/**
 * Математическая модель для симулятора и для "как это должно работать".
 * Она же — эталон, по которому можно проверять, что collector отдаёт вменяемые числа.
 *
 * priceWalk — детерминированная функция времени: одинаковая на сервере и при повторных
 * вызовах, без случайности. Нужна, чтобы котировки "жили" между опросами и чтобы
 * лаг обновления коэффициентов был воспроизводимым.
 */

export type ModelAsset = "BTC" | "ETH" | "SOL";

const BASE_PRICE: Record<ModelAsset, number> = { BTC: 92500, ETH: 3150, SOL: 175 };

/** Волатильность окна в долях: 5-мин BTC ~ 0.18%, 15-мин ~ 0.32%, день ~ 2.5%. */
export function windowVolPct(windowMs: number): number {
  const minutes = windowMs / 60000;
  if (minutes <= 5) return 0.0018;
  if (minutes <= 15) return 0.0032;
  if (minutes <= 60) return 0.0065;
  if (minutes <= 60 * 24) return 0.025;
  return 0.045;
}

/**
 * Гладкий детерминированный "тик" цены: сумма синусов разных периодов + медленный тренд.
 * basePrice позволяет привязать модель к реальному споту — тогда симулятор даёт
 * страйки на тех же уровнях, что и живые рынки, и матчинг получается осмысленным.
 */
export function priceWalk(asset: ModelAsset, tMs: number, basePrice?: number): number {
  const base = basePrice ?? BASE_PRICE[asset];
  const t = tMs / 1000;
  const seed = asset === "BTC" ? 1 : asset === "ETH" ? 2.3 : 4.1;
  const w =
    0.0022 * Math.sin(t / 41 + seed) +
    0.0041 * Math.sin(t / 197 + seed * 2) +
    0.0079 * Math.sin(t / 1031 + seed * 3) +
    0.0143 * Math.sin(t / 4373 + seed * 5) +
    0.0210 * Math.sin(t / 18961 + seed * 7);
  return base * (1 + w);
}

function erf(x: number): number {
  const sign = x < 0 ? -1 : 1;
  const ax = Math.abs(x);
  const t = 1 / (1 + 0.3275911 * ax);
  const y =
    1 -
    ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) *
      t *
      Math.exp(-ax * ax);
  return sign * y;
}

export function normCdf(x: number): number {
  return 0.5 * (1 + erf(x / Math.SQRT2));
}

/**
 * P(S_T > S_start) для логнормального S_T с волатильностью окна.
 * Именно так ("дельта бинарного опциона") CEX и пересчитывает коэффициент Up/Down.
 */
export function probUp(startPrice: number, currentPrice: number, windowMs: number, remainingMs: number): number {
  const remainingFrac = Math.min(1, Math.max(remainingMs / windowMs, 0.02));
  const sigma = windowVolPct(windowMs) * Math.sqrt(remainingFrac);
  if (sigma <= 0) return currentPrice >= startPrice ? 1 : 0;
  const z = Math.log(currentPrice / startPrice) / sigma;
  return Math.min(0.995, Math.max(0.005, normCdf(z)));
}

/** Коэффициент fixed-odds с площадочной маржой: O = (1/p) * (1 - margin). */
export function oddsFromProbability(p: number, marginBps: number): number {
  const safe = Math.min(0.99, Math.max(0.01, p));
  return Math.round((1 / safe) * (1 - marginBps / 10000) * 10000) / 10000;
}
