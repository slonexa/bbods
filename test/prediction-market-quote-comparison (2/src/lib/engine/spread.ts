import type { MarketType } from "../types";

/**
 * Engine: расчёт спреда. Ключевая идея — привести обе ноги к цене за $1 выплаты.
 *   continuous:  цена шеры = implied probability
 *   fixed_odds:  p = 1 / O
 * Тогда:
 *   cost  = p_A + p_B          (стоимость пары, гарантирующей $1 в любом исходе)
 *   edge  = 1 - (p_A + p_B)    (Synthetic Dutch Book)
 *   spread_after_fees = edge - (p_A + p_B) * (fee_A + slip_A + fee_B + slip_B) / 10000
 * Для fixed-odds это ровно классика 1 - (1/O_a + 1/O_b), просто в другой записи.
 */

export interface PlatformMeta {
  slug: string;
  feeBps: number;
  slippageBps: number;
  resolutionSource: string | null;
}

export interface EngineLeg {
  platform: string;
  marketId: string;
  outcome: string;
  direction: string;
  probability: number;
  odds: number | null;
  marketType: MarketType;
  fetchedAt: number;
  rawTitle: string;
  liquidity: number | null;
}

export interface EnginePair {
  eventKey: string;
  title: string;
  platformA: string;
  probA: number;
  platformB: string;
  probB: number;
  marketTypeA: MarketType;
  marketTypeB: MarketType;
  oddsA: number | null;
  oddsB: number | null;
  sideA: string;
  sideB: string;
  rawEdge: number;
  feesBps: number;
  slippageBps: number;
  spreadAfterFees: number;
  stakeA: number;
  stakeB: number;
  payout: number;
  profit: number;
  roiPct: number;
  verdict: "arb" | "lag" | "none" | "phantom_oracle" | "unsynced";
  warnings: string[];
  quoteAgeDeltaMs: number;
}

export interface EvaluateOptions {
  bankrollUsd: number;
  alertThresholdPct: number;
  maxQuoteAgeDeltaMs: number;
}

function probFor(leg: EngineLeg, side: string): number | null {
  if (leg.direction === side) return leg.probability;
  // комплементарная сторона: continuous отдаёт цену NO, fixed_odds пересчитываем из O
  return Math.min(0.999, Math.max(0.001, 1 - leg.probability));
}

function expiryOf(eventKey: string): number | null {
  const match = eventKey.match(/(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})/);
  if (!match) return null;
  const t = Date.parse(`${match[1]}Z`);
  return Number.isFinite(t) ? t : null;
}

const round4 = (n: number) => Math.round(n * 10000) / 10000;
const round2 = (n: number) => Math.round(n * 100) / 100;

export function evaluateEvent(
  eventKey: string,
  title: string,
  legs: EngineLeg[],
  meta: Record<string, PlatformMeta>,
  opts: EvaluateOptions,
): EnginePair[] {
  const byPlatform = new Map<string, EngineLeg[]>();
  for (const leg of legs) {
    const list = byPlatform.get(leg.platform) ?? [];
    list.push(leg);
    byPlatform.set(leg.platform, list);
  }
  const platformSlugs = [...byPlatform.keys()];
  if (platformSlugs.length < 2) return [];

  const results: EnginePair[] = [];
  const expiry = expiryOf(eventKey);

  for (let i = 0; i < platformSlugs.length; i += 1) {
    for (let j = 0; j < platformSlugs.length; j += 1) {
      if (i === j) continue;
      const legsA = byPlatform.get(platformSlugs[i]) ?? [];
      const legsB = byPlatform.get(platformSlugs[j]) ?? [];

      for (const legA of legsA) {
        for (const legB of legsB) {
          if (legA.direction === legB.direction) continue;
          const probA = probFor(legA, legA.direction);
          const probB = probFor(legB, legB.direction);
          if (probA === null || probB === null) continue;

          const mA = meta[legA.platform];
          const mB = meta[legB.platform];
          if (!mA || !mB) continue;

          const feesBps = mA.feeBps + mB.feeBps;
          const slippageBps = mA.slippageBps + mB.slippageBps;
          const cost = probA + probB;
          const rawEdge = 1 - cost;
          const spreadAfterFees = rawEdge - (cost * (feesBps + slippageBps)) / 10000;

          const bankroll = opts.bankrollUsd;
          const stakeA = (bankroll * probA) / cost;
          const stakeB = (bankroll * probB) / cost;
          const payout = bankroll / cost;
          const profit = payout - bankroll;

          const warnings: string[] = [];
          let verdict: EnginePair["verdict"] = "none";

          if (legA.marketType !== legB.marketType) {
            warnings.push(
              "fixed_odds × continuous: фикс-ногу нельзя закрыть раньше экспирации — хедж держим до её резолюции",
            );
          }
          if (legA.marketType === "fixed_odds" && legB.marketType === "fixed_odds") {
            warnings.push("fixed_odds × fixed_odds: таймфрейм и price index обязаны совпадать секунда в секунду");
          }
          if ((mA.resolutionSource ?? "") !== (mB.resolutionSource ?? "")) {
            warnings.push(
              `разный источник резолюции/цены: ${mA.resolutionSource ?? "?"} vs ${mB.resolutionSource ?? "?"} — возможно, это не одно событие`,
            );
            verdict = "phantom_oracle";
          }
          const quoteAgeDeltaMs = Math.abs(legA.fetchedAt - legB.fetchedAt);
          if (quoteAgeDeltaMs > opts.maxQuoteAgeDeltaMs) {
            warnings.push(`несинхронный замер: Δ возраста котировок ${quoteAgeDeltaMs}мс`);
            verdict = "unsynced";
          }
          if (expiry !== null && expiry < Date.now()) {
            warnings.push("событие уже экспирировалось — расчёт исторический");
            verdict = "unsynced";
          }

          if (verdict === "none") verdict = spreadAfterFees > 0 ? "arb" : "none";

          results.push({
            eventKey,
            title,
            platformA: legA.platform,
            probA: round4(probA),
            platformB: legB.platform,
            probB: round4(probB),
            marketTypeA: legA.marketType,
            marketTypeB: legB.marketType,
            oddsA: legA.odds,
            oddsB: legB.odds,
            sideA: legA.direction,
            sideB: legB.direction,
            rawEdge: round4(rawEdge),
            feesBps,
            slippageBps,
            spreadAfterFees: round4(spreadAfterFees),
            stakeA: round2(stakeA),
            stakeB: round2(stakeB),
            payout: round2(payout),
            profit: round2(profit),
            roiPct: round4((profit / bankroll) * 100),
            verdict,
            warnings,
            quoteAgeDeltaMs,
          });
        }
      }
    }
  }

  // дедуп: на пару площадок оставляем только лучшую комбинацию сторон
  const best = new Map<string, EnginePair>();
  for (const pair of results) {
    const key = `${pair.platformA}|${pair.platformB}`;
    const prev = best.get(key);
    if (!prev || pair.spreadAfterFees > prev.spreadAfterFees) best.set(key, pair);
  }
  return [...best.values()].sort((a, b) => b.spreadAfterFees - a.spreadAfterFees);
}
