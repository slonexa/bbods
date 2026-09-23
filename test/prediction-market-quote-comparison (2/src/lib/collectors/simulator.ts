import { getSpot } from "../spot";
import { priceWalk, probUp, type ModelAsset } from "../market-model";
import type { Quote } from "../types";

/**
 * Симулятор котировок. Зачем он в скелете:
 *  1) pipeline (collector -> normalizer -> engine -> dashboard) можно отлаживать до того,
 *     как найдены реальные внутренние эндпоинты Bybit/MEXC/OKX/Gate;
 *  2) он показывает, какие поля обязана отдавать площадка (окно старта, страйк, окно экспирации,
 *     market_type) — это и есть чеклист для реверса эндпоинта через DevTools;
 *  3) у каждой площадки свой лаг обновления коэффициента и своё смещение mark price —
 *     так на дашборде видно и вилки, и фантомные "вилки" из-за разных ценовых индексов.
 */
interface SimConfig {
  lagMs: number;
  biasBps: number;
  marginBps: number;
  windowsMs: number[];
  assets: ModelAsset[];
  targets: boolean;
  ranges: boolean;
  continuous: boolean;
}

const SIM_CONFIG: Record<string, SimConfig> = {
  bybit_odds: {
    lagMs: 2600,
    biasBps: 1.5,
    marginBps: 220,
    windowsMs: [5 * 60000, 15 * 60000],
    assets: ["BTC", "ETH"],
    targets: true,
    ranges: true,
    continuous: false,
  },
  mexc_prediction: {
    lagMs: 1400,
    biasBps: -1.2,
    marginBps: 0,
    windowsMs: [5 * 60000, 15 * 60000],
    assets: ["BTC", "SOL"],
    targets: true,
    ranges: false,
    continuous: false,
  },
  okx_events: {
    lagMs: 900,
    biasBps: 0.6,
    marginBps: 180,
    windowsMs: [5 * 60000],
    assets: ["BTC", "ETH"],
    targets: false,
    ranges: false,
    continuous: false,
  },
  gate_events: {
    lagMs: 1800,
    biasBps: -0.8,
    marginBps: 200,
    windowsMs: [5 * 60000],
    assets: ["BTC"],
    targets: false,
    ranges: false,
    continuous: false,
  },
  polymarket: {
    lagMs: 600,
    biasBps: 0,
    marginBps: 0,
    windowsMs: [],
    assets: ["BTC", "ETH"],
    targets: true,
    ranges: false,
    continuous: true,
  },
};

const niceStrike = (asset: ModelAsset, price: number): number => {
  // Крупные шаги => страйки на тех же уровнях, что у реальных рынков (50000, 87500, 90000...)
  const step = asset === "BTC" ? 500 : asset === "ETH" ? 25 : 5;
  return Math.round(price / step) * step;
};

function updownQuotes(cfg: SimConfig, slug: string, now: number, bases: Record<string, number>): Quote[] {
  const out: Quote[] = [];
  for (const asset of cfg.assets) {
    const basePrice = bases[asset];
    const base = priceWalk(asset, now, basePrice);
    for (const windowMs of cfg.windowsMs) {
      const periodMs = windowMs;
      const windowStart = Math.floor(now / periodMs) * periodMs;
      const windowEnd = windowStart + periodMs;
      const startPrice = priceWalk(asset, windowStart, basePrice);
      // лаг + собственный индекс цены площадки: главный источник фантомных расхождений
      const platformPrice = priceWalk(asset, now - cfg.lagMs, basePrice) * (1 + cfg.biasBps / 10000);
      const label = windowMs === 5 * 60000 ? "5M" : windowMs === 15 * 60000 ? "15M" : `${windowMs / 60000}M`;
      const p = probUp(startPrice, platformPrice, periodMs, windowEnd - now);
      const id = `${asset}USDT-UPDOWN-${label}-${windowStart}`;
      const title = `${asset} price up by ${new Date(windowEnd).toISOString().slice(11, 16)} UTC (${label})`;

      for (const dir of ["up", "down"] as const) {
        const raw = dir === "up" ? p : 1 - p;
        const fair = Math.min(0.995, Math.max(0.005, raw));
        const marketType = cfg.continuous ? "continuous" : "fixed_odds";
        const price = cfg.continuous
          ? Math.round(fair * 10000) / 10000
          : Math.round((1 / fair) * (1 - cfg.marginBps / 10000) * 10000) / 10000;
        out.push({
          platform: slug,
          market_id: `${id}-${dir.toUpperCase()}`,
          raw_title: title,
          outcome: dir,
          market_type: marketType,
          odds: marketType === "fixed_odds" ? price : null,
          implied_probability: marketType === "fixed_odds" ? Math.round((1 / price) * 10000) / 10000 : price,
          complementary_probability: marketType === "fixed_odds" ? null : Math.round((1 - fair) * 10000) / 10000,
          expiry: new Date(windowEnd).toISOString(),
          window_start: new Date(windowStart).toISOString(),
          asset,
          contract_type: "updown",
          direction: dir,
          strike: startPrice,
          spot_price: base,
          volume_24h: Math.round(50000 + 400000 * Math.abs(Math.sin(now / 90000 + asset.length))),
          liquidity: Math.round(20000 + 90000 * Math.abs(Math.cos(now / 70000))),
          resolution_source: `${asset} ${label} mark price`,
          source: "simulated",
          fetched_at: new Date(now).toISOString(),
          raw: { simulated: true, windowMs, lagMs: cfg.lagMs, indexBiasBps: cfg.biasBps },
        });
      }
    }
  }
  return out;
}

function targetQuotes(cfg: SimConfig, slug: string, now: number, bases: Record<string, number>): Quote[] {
  const out: Quote[] = [];
  const horizons = [6 * 3600000, 24 * 3600000, 3 * 24 * 3600000];
  for (const asset of cfg.assets) {
    const spot = priceWalk(asset, now, bases[asset]);
    for (const horizon of horizons) {
      const expiry = Math.ceil((now + horizon) / 3600000) * 3600000;
      const remaining = expiry - now;
      const bump = horizon === 6 * 3600000 ? 0.004 : horizon === 24 * 3600000 ? 0.012 : 0.03;
      for (const sign of [1, -1]) {
        const strike = niceStrike(asset, spot * (1 + sign * bump));
        const marketType = cfg.continuous ? "continuous" : "fixed_odds";
        const dir = sign > 0 ? "above" : "below";
        const pAbove = probUp(spot, spot, remaining, remaining);
        for (const [direction, fair] of [
          ["above", pAbove],
          ["below", 1 - pAbove],
        ] as const) {
          const price = cfg.continuous
            ? Math.round(Math.min(0.98, Math.max(0.02, fair)) * 10000) / 10000
            : Math.round(
                (1 / Math.min(0.98, Math.max(0.02, fair))) * (1 - cfg.marginBps / 10000) * 10000,
              ) / 10000;
          out.push({
            platform: slug,
            market_id: `${asset}-TARGET-${expiry}-${strike}-${direction.toUpperCase()}`,
            raw_title: `Will ${asset} be ${direction} $${strike.toLocaleString("en-US")} at ${new Date(expiry)
              .toISOString()
              .slice(0, 16)}Z?`,
            outcome: direction,
            market_type: marketType,
            odds: marketType === "fixed_odds" ? price : null,
            implied_probability: marketType === "fixed_odds" ? Math.round((1 / price) * 10000) / 10000 : price,
            complementary_probability: marketType === "fixed_odds" ? null : Math.round((1 - fair) * 10000) / 10000,
            expiry: new Date(expiry).toISOString(),
            window_start: new Date(now).toISOString(),
            asset,
            contract_type: "target",
            direction,
            strike,
            spot_price: spot,
            volume_24h: Math.round(30000 + 200000 * Math.abs(Math.sin(now / 120000 + strike / 1e5))),
            liquidity: Math.round(10000 + 50000 * Math.abs(Math.cos(now / 90000 + strike / 1e5))),
            resolution_source: cfg.continuous ? "Chainlink / exchange index" : `${asset} mark price`,
            source: "simulated",
            fetched_at: new Date(now).toISOString(),
            raw: { simulated: true, horizon, lagMs: cfg.lagMs, indexBiasBps: cfg.biasBps },
          });
        }
      }
    }
  }
  return out;
}

function rangeQuotes(cfg: SimConfig, slug: string, now: number, bases: Record<string, number>): Quote[] {
  const out: Quote[] = [];
  for (const asset of cfg.assets) {
    const spot = priceWalk(asset, now, bases[asset]);
    const windowMs = 15 * 60000;
    const windowStart = Math.floor(now / windowMs) * windowMs;
    const windowEnd = windowStart + windowMs;
    const low = niceStrike(asset, spot * 0.9975);
    const high = niceStrike(asset, spot * 1.0025);
    const pInside = 0.46 + 0.06 * Math.sin(now / 60000);
    for (const [dir, fair] of [
      ["inside", pInside],
      ["outside", 1 - pInside],
    ] as const) {
      const price = Math.round((1 / fair) * (1 - cfg.marginBps / 10000) * 10000) / 10000;
      out.push({
        platform: slug,
        market_id: `${asset}-RANGE-${windowStart}-${dir.toUpperCase()}`,
        raw_title: `${asset} inside ${low}-${high} by ${new Date(windowEnd).toISOString().slice(11, 16)} UTC`,
        outcome: dir,
        market_type: "fixed_odds",
        odds: price,
        implied_probability: Math.round((1 / price) * 10000) / 10000,
        complementary_probability: null,
        expiry: new Date(windowEnd).toISOString(),
        window_start: new Date(windowStart).toISOString(),
        asset,
        contract_type: "range",
        direction: dir,
        strike: low,
        spot_price: spot,
        volume_24h: 40000,
        liquidity: 15000,
        resolution_source: `${asset} mark price`,
        source: "simulated",
        fetched_at: new Date(now).toISOString(),
        raw: { simulated: true, low, high, note: "range = комбинация двух порогов, хедж двумя позициями" },
      });
    }
  }
  return out;
}

/**
 * Асинхронный, потому что привязывается к живому споту (если фид доступен):
 * уровни цен получаются те же, на которых лежат реальные рынки.
 */
export async function simulateQuotes(slug: string, now = Date.now()): Promise<Quote[]> {
  const cfg = SIM_CONFIG[slug];
  if (!cfg) return [];
  const bases: Record<string, number> = {};
  await Promise.all(
    cfg.assets.map(async (asset) => {
      bases[asset] = (await getSpot(asset)).price;
    }),
  );
  const quotes: Quote[] = [...updownQuotes(cfg, slug, now, bases)];
  if (cfg.targets) quotes.push(...targetQuotes(cfg, slug, now, bases));
  if (cfg.ranges) quotes.push(...rangeQuotes(cfg, slug, now, bases));
  return quotes;
}

export function simConfigFor(slug: string): SimConfig | null {
  return SIM_CONFIG[slug] ?? null;
}

