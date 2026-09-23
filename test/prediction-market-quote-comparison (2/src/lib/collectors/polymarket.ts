import { fetchJson, describeError, nowIso } from "../http";
import type { ContractType, Quote } from "../types";

/**
 * Polymarket collector — единственная площадка старта с ЧЕСТНЫМ публичным API
 * (Gamma Markets API, документирован Polymarket). Поэтому его пишем первым: он даёт
 * эталонный поток цен 0..1 и служит проверкой математики engine'а.
 */

const GAMMA_URL =
  process.env.POLYMARKET_GAMMA_URL ??
  "https://gamma-api.polymarket.com/markets?closed=false&active=true&archived=false&limit=250&order=volume24hr&ascending=false";

interface GammaMarket {
  id?: string | number;
  question?: string;
  slug?: string;
  conditionId?: string;
  outcomes?: string | string[];
  outcomePrices?: string | string[];
  bestBid?: number;
  bestAsk?: number;
  lastTradePrice?: number;
  endDate?: string;
  endDateIso?: string;
  volume24hr?: number;
  liquidityNum?: number;
  closed?: boolean;
  active?: boolean;
  events?: Array<{ title?: string; slug?: string; ticker?: string }>;
}

function asArray(value: string | string[] | undefined): string[] {
  if (!value) return [];
  if (Array.isArray(value)) return value;
  try {
    const parsed = JSON.parse(value);
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch {
    return [];
  }
}

function toNumber(value: unknown): number | null {
  const n = typeof value === "string" ? Number(value) : typeof value === "number" ? value : NaN;
  return Number.isFinite(n) ? n : null;
}

const ASSETS: Array<[RegExp, string]> = [
  [/\b(btc|bitcoin)\b/i, "BTC"],
  [/\b(eth|ethereum)\b/i, "ETH"],
  [/\b(sol|solana)\b/i, "SOL"],
  [/\bxrp\b/i, "XRP"],
  [/\bdoge(?:coin)?\b/i, "DOGE"],
  [/\bbnb\b/i, "BNB"],
];

/** Достаём актив/страйк/направление из формулировки — эндпоинт этого не отдаёт. */
export function parseCryptoTitle(title: string): {
  asset: string | null;
  strike: number | null;
  direction: "above" | "below" | null;
  contractType: ContractType;
} {
  const asset = ASSETS.find(([re]) => re.test(title))?.[1] ?? null;
  const priceMatch = title.match(/\$\s?([\d][\d,]*(?:\.\d+)?)\s?([kKmM])?/);
  let strike: number | null = null;
  if (priceMatch) {
    const num = Number(priceMatch[1].replace(/,/g, ""));
    if (Number.isFinite(num)) {
      const mult = priceMatch[2]?.toLowerCase() === "k" ? 1e3 : priceMatch[2]?.toLowerCase() === "m" ? 1e6 : 1;
      strike = num * mult;
    }
  }
  const below = /\b(below|under|lower|drop to|close below|fall to|dip to)\b/i.test(title);
  const above = /\b(above|over|higher|reach|hit|exceed|close above|rise to|top)\b/i.test(title);
  const direction = below ? "below" : above ? "above" : null;
  const contractType: ContractType = asset ? (strike ? "target" : "updown") : "other";
  return { asset, strike, direction, contractType };
}

function parseExpiry(m: GammaMarket): string | null {
  const raw = m.endDateIso ?? m.endDate;
  if (!raw) return null;
  const t = Date.parse(raw);
  return Number.isFinite(t) ? new Date(t).toISOString() : null;
}

export function mapGammaMarket(m: GammaMarket, endpoint: string): Quote[] {
  const outcomes = asArray(m.outcomes);
  const prices = asArray(m.outcomePrices).map((p) => Number(p));
  const title = m.question ?? m.slug ?? "unknown";
  const expiry = parseExpiry(m);
  const fetchedAt = nowIso();
  const parsed = parseCryptoTitle(title);
  const volume = toNumber(m.volume24hr);
  const liquidity = toNumber(m.liquidityNum);

  if (outcomes.length < 2 || prices.length < 2) return [];

  const quotes: Quote[] = [];
  outcomes.slice(0, 2).forEach((outcome, index) => {
    const price = prices[index];
    if (!Number.isFinite(price) || price <= 0 || price >= 1) return;
    quotes.push({
      platform: "polymarket",
      market_id: String(m.id ?? m.conditionId ?? m.slug ?? title),
      raw_title: title,
      outcome: outcome.toLowerCase(),
      market_type: "continuous",
      odds: null,
      implied_probability: Math.round(price * 10000) / 10000,
      complementary_probability: Math.round((1 - price) * 10000) / 10000,
      expiry,
      window_start: null,
      asset: parsed.asset,
      contract_type: parsed.contractType,
      direction: outcome.toLowerCase().startsWith("no")
        ? parsed.direction === "above"
          ? "below"
          : "above"
        : parsed.direction,
      strike: parsed.strike,
      spot_price: null,
      volume_24h: volume,
      liquidity,
      resolution_source: "UMA CTF adapter (Polymarket rules)",
      source: "live",
      endpoint,
      fetched_at: fetchedAt,
      raw: {
        slug: m.slug,
        conditionId: m.conditionId,
        bestBid: m.bestBid ?? null,
        bestAsk: m.bestAsk ?? null,
        lastTradePrice: m.lastTradePrice ?? null,
        event: m.events?.[0]?.title ?? null,
      },
    });
  });
  return quotes;
}

export async function collectPolymarket(): Promise<{ quotes: Quote[]; status: number }> {
  const { data, status } = await fetchJson<GammaMarket[] | { data?: GammaMarket[] }>({
    url: GAMMA_URL,
    timeoutMs: 9000,
    minIntervalMs: 8000,
  });
  const list = Array.isArray(data) ? data : (data?.data ?? []);
  const quotes = list.flatMap((m) => mapGammaMarket(m, GAMMA_URL));
  return { quotes, status };
}

export function describeGammaError(error: unknown): string {
  return describeError(error);
}
