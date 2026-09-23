import type { MarketType } from "./types";

export interface PlatformSeed {
  slug: string;
  name: string;
  kind: "cex_odds" | "prediction_market";
  mode: "live" | "simulated" | "planned";
  defaultMarketType: MarketType;
  feeBps: number;
  slippageBps: number;
  minIntervalMs: number;
  endpointHint: string | null;
  resolutionSource: string | null;
  notes: string | null;
  /** Кандидаты внутренних эндпоинтов. Проверяются через /api/probe (DevTools workflow). */
  candidates: string[];
  envKeys: string[];
}

export const PLATFORM_SEED: PlatformSeed[] = [
  {
    slug: "polymarket",
    name: "Polymarket",
    kind: "prediction_market",
    mode: "live",
    defaultMarketType: "continuous",
    feeBps: 0,
    slippageBps: 30,
    minIntervalMs: 10000,
    endpointHint: "Gamma API (публичный, документированный) — самый честный старт",
    resolutionSource: "UMA / Polymarket rules",
    notes:
      "Официальный публичный API — используем его, а не обход. Цены 0..1 = implied probability напрямую.",
    candidates: [
      "https://gamma-api.polymarket.com/markets?closed=false&active=true&archived=false&limit=200&order=volume24hr&ascending=false",
    ],
    envKeys: ["POLYMARKET_GAMMA_URL"],
  },
  {
    slug: "bybit_odds",
    name: "Bybit Odds (Up/Down / Target / Range)",
    kind: "cex_odds",
    mode: "simulated",
    defaultMarketType: "fixed_odds",
    feeBps: 0,
    slippageBps: 0,
    minIntervalMs: 5000,
    endpointHint: "bybit.com/ru-RU/trade/odds/ → DevTools → Network → XHR/WS при листании окна",
    resolutionSource: "Bybit mark price (свой индекс!)",
    notes:
      "Продукт свежий, публичного API нет. Коэффициент пересчитывается от mark price и оставшегося времени окна.",
    candidates: [
      "https://api2.bybit.com/spot/api/odds/v1/market/list",
      "https://api.bybit.com/v5/prediction/market/list",
      "https://www.bybit.com/spot/api/odds/v1/market/list",
    ],
    envKeys: ["BYBIT_ODDS_ENDPOINT", "BYBIT_ODDS_ENDPOINTS"],
  },
  {
    slug: "mexc_prediction",
    name: "MEXC Prediction Market",
    kind: "cex_odds",
    mode: "simulated",
    defaultMarketType: "fixed_odds",
    feeBps: 0,
    slippageBps: 0,
    minIntervalMs: 5000,
    endpointHint: "prediction.mexc.com/prediction-markets/up-down → Network → XHR",
    resolutionSource: "MEXC mark price",
    notes: "Нулевые комиссии (с марта 2026) — издержки только как проскальзывание.",
    candidates: [
      "https://prediction.mexc.com/api/prediction/market/list",
      "https://www.mexc.com/api/prediction/market/list",
    ],
    envKeys: ["MEXC_PREDICTION_ENDPOINT", "MEXC_PREDICTION_ENDPOINTS"],
  },
  {
    slug: "okx_events",
    name: "OKX Trade Events (5m BTC/ETH up/down)",
    kind: "cex_odds",
    mode: "simulated",
    defaultMarketType: "fixed_odds",
    feeBps: 0,
    slippageBps: 0,
    minIntervalMs: 5000,
    endpointHint: "okx.com/trade-events/btc-updown-5min → Network → XHR/WS",
    resolutionSource: "OKX mark price",
    notes: "Цель №3 по приоритету. Проверить окно старта 5-минутки (секунда в секунду).",
    candidates: ["https://www.okx.com/api/v5/trade/events/markets", "https://www.okx.com/priapi/v5/trade/events/market/list"],
    envKeys: ["OKX_EVENTS_ENDPOINT"],
  },
  {
    slug: "gate_events",
    name: "Gate.com Trade Events (5m up/down)",
    kind: "cex_odds",
    mode: "planned",
    defaultMarketType: "fixed_odds",
    feeBps: 0,
    slippageBps: 0,
    minIntervalMs: 5000,
    endpointHint: "gate.com/trade-events/btc-updown-5m → Network → XHR",
    resolutionSource: "Gate mark price",
    notes: "Цель №4. Включать после Bybit/MEXC.",
    candidates: ["https://www.gate.com/api/trade-events/markets"],
    envKeys: ["GATE_EVENTS_ENDPOINT"],
  },
];

export function candidatesFor(slug: string): string[] {
  const seed = PLATFORM_SEED.find((p) => p.slug === slug);
  if (!seed) return [];
  const fromEnv: string[] = [];
  for (const key of seed.envKeys) {
    const value = process.env[key];
    if (!value) continue;
    for (const part of value.split(/[,\s]+/)) {
      if (part.startsWith("http")) fromEnv.push(part.trim());
    }
  }
  return [...fromEnv, ...seed.candidates];
}

export function primaryEndpointFor(slug: string): string | null {
  return candidatesFor(slug)[0] ?? null;
}
