import type { Quote } from "@/lib/types";
import { config } from "@/lib/config";

// Polymarket is the one platform in this MVP with an official, documented
// public API (Gamma for discovery, CLOB for order books) — see spec section
// 2/7. No reverse engineering needed here, we just call it politely.
const GAMMA_BASE = "https://gamma-api.polymarket.com";

interface GammaMarket {
  id: string;
  slug: string;
  question: string;
  outcomes: string; // stringified JSON array
  outcomePrices: string; // stringified JSON array
  clobTokenIds?: string;
  active: boolean;
  closed: boolean;
  endDate?: string;
  volumeNum?: number;
}

function safeParseArray(raw: string | undefined): string[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.map(String) : [];
  } catch {
    return [];
  }
}

const ASSET_PATTERNS: { asset: string; re: RegExp }[] = [
  { asset: "BTC", re: /\b(bitcoin|btc)\b/i },
  { asset: "ETH", re: /\b(ethereum|eth)\b/i },
  { asset: "SOL", re: /\b(solana|sol)\b/i },
];

// Parses a $-threshold out of a market question, e.g.
// "Will Bitcoin reach $150,000 by December 31?" -> 150000
function parseThreshold(question: string): number | undefined {
  const match = question.match(/\$\s?([\d,]+(?:\.\d+)?)\s?(k|K)?/);
  if (!match) return undefined;
  let value = Number(match[1].replace(/,/g, ""));
  if (Number.isNaN(value)) return undefined;
  if (match[2]) value *= 1000;
  return value;
}

function parseDirection(question: string): "above" | "below" {
  return /below|under|drop|fall|dip/i.test(question) ? "below" : "above";
}

function parseWindowSeconds(slug: string): number | undefined {
  const match = slug.match(/(\d+)(m|min|h|hr)\b/i);
  if (!match) return undefined;
  const value = Number(match[1]);
  const unit = match[2].toLowerCase();
  if (unit.startsWith("h")) return value * 3600;
  return value * 60;
}

export async function fetchPolymarketQuotes(): Promise<Quote[]> {
  const params = new URLSearchParams({
    limit: "150",
    active: "true",
    closed: "false",
    order: "volume24hr",
    ascending: "false",
  });

  const res = await fetch(`${GAMMA_BASE}/markets?${params.toString()}`, {
    headers: {
      accept: "application/json",
      "user-agent": "arb-scanner-mvp/0.1 (+educational, low-frequency polling)",
    },
    // Gamma is read-only/public; a short cache avoids hammering it if several
    // requests land in the same tick.
    cache: "no-store",
  });

  if (!res.ok) {
    throw new Error(`Gamma API ${res.status} ${res.statusText}`);
  }

  const markets = (await res.json()) as GammaMarket[];
  const fetchedAt = new Date().toISOString();
  const quotes: Quote[] = [];

  for (const market of markets) {
    const assetMatch = ASSET_PATTERNS.find((p) => p.re.test(market.question));
    if (!assetMatch) continue; // not a crypto market, skip for this MVP scope

    const outcomes = safeParseArray(market.outcomes);
    const prices = safeParseArray(market.outcomePrices).map(Number);
    if (outcomes.length !== prices.length || outcomes.length === 0) continue;

    const isUpDown = /up or down|updown/i.test(market.question) || /updown/i.test(market.slug);
    const windowSeconds = parseWindowSeconds(market.slug);
    const threshold = parseThreshold(market.question);

    let contractType: "updown" | "target" | undefined;
    if (isUpDown) contractType = "updown";
    else if (threshold !== undefined) contractType = "target";
    else continue; // can't classify -> skip, keep normalizer logic simple

    if (contractType === "target") {
      // Daily/near-term "will BTC be above $X today?" snapshot markets are
      // structurally different from the multi-day "target by date" contracts
      // this MVP is trying to match (spec 4.2) — matching them tends to
      // produce noise rather than real spreads, so we skip anything expiring
      // within 24h.
      const hoursToExpiry = market.endDate ? (new Date(market.endDate).getTime() - Date.now()) / 3_600_000 : 0;
      if (hoursToExpiry < 24) continue;
    }

    const direction = contractType === "target" ? parseDirection(market.question) : undefined;

    outcomes.forEach((outcomeLabel, idx) => {
      const prob = prices[idx];
      if (!Number.isFinite(prob) || prob <= 0 || prob >= 1) return;

      const lower = outcomeLabel.toLowerCase();
      let canonicalOutcome = lower;
      if (contractType === "updown") {
        if (lower.includes("up")) canonicalOutcome = "up";
        else if (lower.includes("down")) canonicalOutcome = "down";
      } else if (contractType === "target" && direction) {
        const isYes = lower.includes("yes");
        const isNo = lower.includes("no");
        if (isYes) canonicalOutcome = direction;
        else if (isNo) canonicalOutcome = direction === "above" ? "below" : "above";
      }

      quotes.push({
        platform: "polymarket",
        marketId: market.id,
        rawTitle: market.question,
        outcome: canonicalOutcome,
        impliedProbability: prob,
        marketType: "continuous",
        expiry: market.endDate ?? null,
        fetchedAt,
        isSimulated: false,
        meta: {
          asset: assetMatch.asset,
          contractType,
          threshold,
          direction,
          windowSeconds,
          priceIndexSource: "polymarket-uma-resolution",
          fee: config.fees.polymarket,
          slug: market.slug,
        },
      });
    });
  }

  return quotes;
}
