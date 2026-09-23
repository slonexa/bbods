import { candidatesFor } from "../platforms";
import { fetchJson, nowIso } from "../http";
import type { ContractType, MarketType, Quote } from "../types";

/**
 * Универсальный collector для CEX-продуктов без публичного API (Bybit Odds/Prediction,
 * MEXC, OKX, Gate). Стратегия:
 *   1) перебираем эндпоинты-кандидаты (env → реестр), которые выглядят правдоподобно;
 *   2) у ответа ищем массив объектов, похожих на рынки;
 *   3) мапим поля по списку синонимов (у каждой площадки свои названия);
 *   4) если ничего не нашлось — отдаём "пусто", а pipeline падает в симулятор.
 * Ни один шаг не роняет цикл: площадка просто помечается как не отвечающая.
 */

const ID_FIELDS = ["market_id", "marketId", "id", "symbol", "contractId", "code", "instrumentId", "slug"];
const TITLE_FIELDS = ["raw_title", "title", "name", "question", "marketName", "displayName", "subject"];
const OUTCOME_FIELDS = ["outcome", "side", "direction", "result", "betSide", "option", "type", "upDown"];
const PROB_FIELDS = [
  "price",
  "probability",
  "prob",
  "lastPrice",
  "impliedProbability",
  "oddsRate",
  "quote",
  "lastTradePrice",
  "outcomePrices",
  "bestAsk",
  "bestBid",
  "yesPrice",
];
const ODDS_FIELDS = ["odds", "oddsRate", "payoutRate", "multiplier", "coefficient", "bonusRate"];
const EXPIRY_FIELDS = ["expiry", "expireTime", "expiryTime", "endTime", "settleTime", "closeTime", "deadline", "endAt"];
const START_FIELDS = ["window_start", "startTime", "openTime", "beginTime", "startAt", "periodStart"];
const ASSET_FIELDS = ["asset", "baseAsset", "coin", "symbol", "currency", "instrumentName"];
const STRIKE_FIELDS = ["strike", "strikePrice", "targetPrice", "triggerPrice", "basePrice", "openPrice", "referencePrice"];

function pick(obj: Record<string, unknown>, fields: string[]): unknown {
  for (const f of fields) {
    const value = obj[f];
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return undefined;
}

function toNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string") {
    const n = Number(value.replace(/[^0-9.\-eE]/g, ""));
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/**
 * Некоторые площадки (Polymarket Gamma) отдают цены JSON-строкой: '["0.55","0.45"]'.
 */
function parseMaybeArray(value: unknown): { yes: number | null; no: number | null } {
  if (typeof value !== "string" || !value.trim().startsWith("[")) return { yes: null, no: null };
  try {
    const parsed = JSON.parse(value);
    if (!Array.isArray(parsed)) return { yes: null, no: null };
    const nums = parsed.map((v) => Number(v)).filter((v) => Number.isFinite(v));
    return { yes: nums[0] ?? null, no: nums[1] ?? null };
  } catch {
    return { yes: null, no: null };
  }
}

function toIso(value: unknown): string | null {
  if (value === undefined || value === null) return null;
  if (typeof value === "number") {
    const ms = value > 1e12 ? value : value * 1000;
    return Number.isFinite(ms) ? new Date(ms).toISOString() : null;
  }
  if (typeof value === "string") {
    const numeric = Number(value);
    if (Number.isFinite(numeric) && value.trim().length >= 10) {
      const ms = numeric > 1e12 ? numeric : numeric * 1000;
      return new Date(ms).toISOString();
    }
    const t = Date.parse(value);
    return Number.isFinite(t) ? new Date(t).toISOString() : null;
  }
  return null;
}

function looksLikeMarket(obj: unknown): obj is Record<string, unknown> {
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) return false;
  const rec = obj as Record<string, unknown>;
  const hasId = ID_FIELDS.some((f) => rec[f] !== undefined);
  const hasTitle = TITLE_FIELDS.some((f) => typeof rec[f] === "string");
  const hasPrice = [...PROB_FIELDS, ...ODDS_FIELDS].some((f) => rec[f] !== undefined);
  return hasId && hasTitle && hasPrice;
}

/** Рекурсивный обход: находим самый правдоподобный массив рынков. */
export function findMarketArray(data: unknown, depth = 0): Record<string, unknown>[] {
  if (depth > 6 || data === null || typeof data !== "object") return [];
  if (Array.isArray(data)) {
    const hits = data.filter(looksLikeMarket) as Record<string, unknown>[];
    if (hits.length > 0) return hits;
    for (const item of data) {
      const nested = findMarketArray(item, depth + 1);
      if (nested.length > 0) return nested;
    }
    return [];
  }
  const rec = data as Record<string, unknown>;
  let best: Record<string, unknown>[] = [];
  for (const value of Object.values(rec)) {
    const found = findMarketArray(value, depth + 1);
    if (found.length > best.length) best = found;
  }
  return best;
}

const ASSET_RE = /\b(BTC|ETH|SOL|XRP|DOGE|BNB)\b/i;

export function mapGenericMarket(obj: Record<string, unknown>, platform: string, endpoint: string): Quote[] {
  const marketId = pick(obj, ID_FIELDS);
  const title = pick(obj, TITLE_FIELDS);
  if (marketId === undefined || title === undefined) return [];

  const oddsRaw = toNumber(pick(obj, ODDS_FIELDS));
  const rawPriceField = pick(obj, PROB_FIELDS);
  const pair = parseMaybeArray(rawPriceField);
  const priceRaw = pair.yes !== null ? pair.yes : toNumber(rawPriceField);
  const priceComplement = pair.no;
  const fetchedAt = nowIso();
  const titleStr = String(title);
  const asset = String(pick(obj, ASSET_FIELDS) ?? "") || (ASSET_RE.exec(titleStr)?.[1] ?? null);
  const outcomeRaw = pick(obj, OUTCOME_FIELDS);
  const outcome = (outcomeRaw === undefined ? "" : String(outcomeRaw)).toLowerCase() || "yes";

  // Эвристика типа рынка: коэффициент > 1.05 = fixed_odds; значение 0..1 = continuous.
  let marketType: MarketType;
  let odds: number | null = null;
  let prob: number;
  if (oddsRaw !== null && oddsRaw > 1.05) {
    marketType = "fixed_odds";
    odds = oddsRaw;
    prob = 1 / oddsRaw;
  } else if (priceRaw !== null && priceRaw > 1.05) {
    marketType = "fixed_odds";
    odds = priceRaw;
    prob = 1 / priceRaw;
  } else if (priceRaw !== null) {
    marketType = "continuous";
    prob = priceRaw;
  } else {
    return [];
  }

  const contractType: ContractType = /updown|up[_-]?down|binary/i.test(titleStr + JSON.stringify(obj).slice(0, 400))
    ? "updown"
    : /range|inside|between/i.test(titleStr)
      ? "range"
      : "target";

  const round4 = (n: number) => Math.round(n * 10000) / 10000;

  return [
    {
      platform,
      market_id: String(marketId),
      raw_title: titleStr,
      outcome,
      market_type: marketType,
      odds: odds === null ? null : round4(odds),
      implied_probability: round4(prob),
      complementary_probability: priceComplement === null ? null : round4(priceComplement),
      expiry: toIso(pick(obj, EXPIRY_FIELDS)),
      window_start: toIso(pick(obj, START_FIELDS)),
      asset: asset ? asset.toUpperCase() : null,
      contract_type: contractType,
      direction: /(^|[^a-z])up([^a-z]|$)/i.test(outcome) || /up/i.test(outcome) ? "up" : /down/i.test(outcome) ? "down" : null,
      strike: toNumber(pick(obj, STRIKE_FIELDS)),
      spot_price: toNumber(obj.markPrice ?? obj.indexPrice ?? obj.lastPrice),
      volume_24h: toNumber(obj.volume24h ?? obj.turnover24h ?? obj.volume),
      liquidity: toNumber(obj.liquidity ?? obj.depth),
      resolution_source: null,
      source: "live",
      endpoint,
      fetched_at: fetchedAt,
      raw: { keys: Object.keys(obj).slice(0, 40), sample: obj },
    },
  ];
}

export interface CandidateProbe {
  url: string;
  ok: boolean;
  status: number | null;
  items: number;
  error?: string;
}

/** Пробуем все кандидаты по очереди; первый успешный с непустым массивом рынков побеждает. */
export async function probeCexCandidates(platform: string): Promise<{
  quotes: Quote[];
  endpoint: string | null;
  status: number | null;
  probes: CandidateProbe[];
  error?: string;
}> {
  const probes: CandidateProbe[] = [];
  for (const url of candidatesFor(platform)) {
    try {
      const { data, status } = await fetchJson<unknown>({ url, timeoutMs: 6000, minIntervalMs: 3000 });
      const markets = findMarketArray(data);
      probes.push({ url, ok: true, status, items: markets.length });
      if (markets.length === 0) continue;
      const quotes = markets.slice(0, 120).flatMap((m) => mapGenericMarket(m, platform, url));
      if (quotes.length > 0) return { quotes, endpoint: url, status, probes };
    } catch (error) {
      probes.push({
        url,
        ok: false,
        status: null,
        items: 0,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }
  return {
    quotes: [],
    endpoint: null,
    status: probes[probes.length - 1]?.status ?? null,
    probes,
    error: "ни один эндпоинт-кандидат не отдал распознаваемый список рынков",
  };
}
