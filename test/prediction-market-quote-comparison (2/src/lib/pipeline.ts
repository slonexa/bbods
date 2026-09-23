import { db } from "@/db";
import { priceHistory, quotes as quotesTable, spreads } from "@/db/schema";
import { and, gte, sql } from "drizzle-orm";
import { collectAll, diagnosticsFor } from "./collectors";
import { runNormalizer, canonicalDirection, type MatchStats } from "./normalizer";
import { evaluateEvent, type EngineLeg, type EnginePair, type PlatformMeta } from "./engine/spread";
import { recordLagSamples } from "./engine/lag";
import { getPlatformRows, getSettings } from "./settings";
import { recordSpotNow } from "./spot-history";
import type { ModelAsset } from "./market-model";
import type { Quote } from "./types";

/**
 * Один цикл сбора: collectors -> normalizer -> engine -> БД.
 * Фронтенд дёргает POST /api/cycle, поэтому фоновый воркер не нужен:
 * дашборд сам является планировщиком (плюс троттлинг в http.ts защищает площадки).
 */

export interface CycleSummary {
  startedAt: string;
  durationMs: number;
  quotesWritten: number;
  collectors: Array<{
    platform: string;
    status: string;
    items: number;
    durationMs: number;
    endpoint: string | null;
    httpStatus: number | null;
    error: string | null;
    simulated: boolean;
  }>;
  matched: MatchStats;
  spreadsFound: number;
  spreadsLogged: number;
  bestSpreadPct: number | null;
  arbCount: number;
  lagFindings: number;
  spot: Record<string, { price: number; source: string }>;
}

const QUOTE_RETENTION_MS = 6 * 3600 * 1000;
const HISTORY_RETENTION_MS = 14 * 24 * 3600 * 1000;

async function persistQuotes(quotes: Quote[]): Promise<void> {
  if (quotes.length === 0) return;
  const rows = quotes.slice(0, 400).map((q) => ({
    platform: q.platform,
    marketId: q.market_id,
    rawTitle: q.raw_title,
    outcome: q.outcome.toLowerCase(),
    marketType: q.market_type,
    odds: q.odds ?? null,
    impliedProbability: q.implied_probability,
    complementaryProbability: q.complementary_probability ?? null,
    expiry: q.expiry ? new Date(q.expiry) : null,
    windowStart: q.window_start ? new Date(q.window_start) : null,
    asset: q.asset ?? null,
    contractType: q.contract_type ?? null,
    direction: q.direction ?? null,
    strike: q.strike ?? null,
    spotPrice: q.spot_price ?? null,
    volume24h: q.volume_24h ?? null,
    liquidity: q.liquidity ?? null,
    source: q.source,
    endpoint: q.endpoint ?? null,
    fetchedAt: new Date(q.fetched_at),
    raw: q.raw ?? null,
  }));
  await db.insert(quotesTable).values(rows);
}

async function persistHistory(quotes: Quote[], assignments: MatchStats["assignments"]): Promise<void> {
  const keyOf = new Map(assignments.map((a) => [`${a.platform}::${a.marketId}::${a.outcome}`, a.eventKey]));
  const rows = quotes.slice(0, 400).map((q) => ({
    eventKey: keyOf.get(`${q.platform}::${q.market_id}::${q.outcome.toLowerCase()}`) ?? null,
    platform: q.platform,
    marketId: q.market_id,
    outcome: q.outcome.toLowerCase(),
    impliedProbability: q.implied_probability,
    odds: q.odds ?? null,
    spotPrice: q.spot_price ?? null,
    fetchedAt: new Date(q.fetched_at),
  }));
  if (rows.length > 0) await db.insert(priceHistory).values(rows);
}

async function pruneOld(): Promise<void> {
  await db.delete(quotesTable).where(sql`${quotesTable.fetchedAt} < now() - interval '${sql.raw(String(QUOTE_RETENTION_MS / 60000))} minutes'`);
  await db.delete(priceHistory).where(sql`${priceHistory.fetchedAt} < now() - interval '${sql.raw(String(HISTORY_RETENTION_MS / 60000))} minutes'`);
}

export async function runCycle(opts: { force?: boolean } = {}): Promise<CycleSummary> {
  const startedAt = new Date();
  const [settings, platformRows] = await Promise.all([getSettings(), getPlatformRows()]);

  const meta: Record<string, PlatformMeta> = {};
  for (const p of platformRows) {
    meta[p.slug] = {
      slug: p.slug,
      feeBps: p.feeBps,
      slippageBps: p.slippageBps,
      resolutionSource: p.resolutionSource,
    };
  }

  const spotAssets: ModelAsset[] = ["BTC", "ETH"];
  const spotTicks = await Promise.all(spotAssets.map((asset) => recordSpotNow(asset)));
  const spot: Record<string, { price: number; source: string }> = {};
  for (const tick of spotTicks) spot[tick.asset] = { price: tick.price, source: tick.source };

  const collectorResults = await collectAll(undefined, { force: opts.force ?? false });
  const allQuotes = collectorResults.flatMap((r) => r.items);

  await persistQuotes(allQuotes);
  const matched = await runNormalizer(allQuotes);
  await persistHistory(allQuotes, matched.assignments);

  // --- engine: строим ноги из свежих котировок и считаем спреды по событиям ---
  const legByKey = new Map<string, EngineLeg[]>();
  const titleByKey = new Map<string, string>();
  for (const assignment of matched.assignments) {
    const quote = allQuotes.find(
      (q) =>
        q.platform === assignment.platform &&
        q.market_id === assignment.marketId &&
        q.outcome.toLowerCase() === assignment.outcome,
    );
    if (!quote) continue;
    const leg: EngineLeg = {
      platform: quote.platform,
      marketId: quote.market_id,
      outcome: quote.outcome.toLowerCase(),
      direction: canonicalDirection(quote.direction, quote.outcome),
      probability: quote.implied_probability,
      odds: quote.odds ?? null,
      marketType: quote.market_type,
      fetchedAt: Date.parse(quote.fetched_at),
      rawTitle: quote.raw_title,
      liquidity: quote.liquidity ?? null,
    };
    const list = legByKey.get(assignment.eventKey) ?? [];
    list.push(leg);
    legByKey.set(assignment.eventKey, list);
    if (!titleByKey.has(assignment.eventKey)) titleByKey.set(assignment.eventKey, quote.raw_title);
  }

  const engineOptions = {
    bankrollUsd: settings.bankrollUsd,
    alertThresholdPct: settings.alertThresholdPct,
    maxQuoteAgeDeltaMs: settings.maxQuoteAgeDeltaMs,
  };

  const evaluated: EnginePair[] = [];
  for (const [eventKey, legs] of legByKey) {
    const platformsInEvent = new Set(legs.map((l) => l.platform));
    if (platformsInEvent.size < 2) continue;
    evaluated.push(...evaluateEvent(eventKey, titleByKey.get(eventKey) ?? eventKey, legs, meta, engineOptions));
  }
  evaluated.sort((a, b) => b.spreadAfterFees - a.spreadAfterFees);

  const logThreshold = settings.logThresholdPct / 100;
  const toLog = evaluated.filter((p) => p.spreadAfterFees >= logThreshold);
  if (toLog.length > 0) {
    await db.insert(spreads).values(
      toLog.slice(0, 60).map((p) => ({
        eventKey: p.eventKey,
        title: p.title,
        platformA: p.platformA,
        probA: p.probA,
        platformB: p.platformB,
        probB: p.probB,
        marketTypeA: p.marketTypeA,
        marketTypeB: p.marketTypeB,
        sideA: p.sideA,
        sideB: p.sideB,
        rawEdge: p.rawEdge,
        feesBps: p.feesBps + p.slippageBps,
        slippageBps: p.slippageBps,
        spreadAfterFees: p.spreadAfterFees,
        stakeA: p.stakeA,
        stakeB: p.stakeB,
        payoutIfA: p.payout,
        payoutIfB: p.payout,
        verdict: p.verdict,
        warnings: p.warnings,
        oddsA: p.oddsA,
        oddsB: p.oddsB,
        quoteAgeDeltaMs: Math.min(2147483647, p.quoteAgeDeltaMs),
        detectedAt: new Date(),
      })),
    );
  }

  const primaryAsset: ModelAsset | null = "BTC";
  const lagFindings = await recordLagSamples(allQuotes, primaryAsset);

  await pruneOld();

  const best = evaluated[0];
  return {
    startedAt: startedAt.toISOString(),
    durationMs: Date.now() - startedAt.getTime(),
    quotesWritten: allQuotes.length,
    collectors: collectorResults.map((r) => ({
      platform: r.platform,
      status: r.status,
      items: r.items.length,
      durationMs: r.durationMs,
      endpoint: r.endpoint,
      httpStatus: r.httpStatus,
      error: diagnosticsFor(r.platform).fallbackReason ?? r.error ?? null,
      simulated: Boolean(r.fellBackToSimulator),
    })),
    matched: { ...matched, assignments: [] },
    spreadsFound: evaluated.length,
    spreadsLogged: toLog.length,
    bestSpreadPct: best ? Math.round(best.spreadAfterFees * 10000) / 100 : null,
    arbCount: evaluated.filter((p) => p.verdict === "arb").length,
    lagFindings: lagFindings.length,
    spot,
  };
}

export async function recentSpreads(limit = 50, minSpreadPct = 0) {
  const rows = await db
    .select()
    .from(spreads)
    .where(and(gte(spreads.spreadAfterFees, minSpreadPct / 100)))
    .orderBy(sql`${spreads.detectedAt} desc, ${spreads.spreadAfterFees} desc`)
    .limit(limit);
  return rows;
}
