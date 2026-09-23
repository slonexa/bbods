import { db } from "@/db";
import { collectorRuns, eventMatches, events, priceHistory, quotes as quotesTable, spreads } from "@/db/schema";
import { and, desc, eq, gte, inArray, sql } from "drizzle-orm";
import { diagnosticsFor } from "./collectors";
import { getPlatformRows, getSettings } from "./settings";
import { candidatesFor } from "./platforms";
import type { CollectorMeta } from "./types";

export interface SpreadRow {
  id: number;
  eventKey: string;
  title: string;
  platformA: string;
  probA: number;
  platformB: string;
  probB: number;
  marketTypeA: string;
  marketTypeB: string;
  sideA: string | null;
  sideB: string | null;
  rawEdge: number;
  feesBps: number;
  spreadAfterFeesPct: number;
  stakeA: number | null;
  stakeB: number | null;
  payout: number | null;
  verdict: string;
  warnings: string[];
  oddsA: number | null;
  oddsB: number | null;
  quoteAgeDeltaMs: number | null;
  detectedAt: string;
}

function toSpreadRow(row: typeof spreads.$inferSelect): SpreadRow {
  return {
    id: row.id,
    eventKey: row.eventKey,
    title: row.title,
    platformA: row.platformA,
    probA: row.probA,
    platformB: row.platformB,
    probB: row.probB,
      marketTypeA: row.marketTypeA,
      marketTypeB: row.marketTypeB,
      sideA: row.sideA,
      sideB: row.sideB,
      rawEdge: row.rawEdge,
    feesBps: row.feesBps,
    spreadAfterFeesPct: Math.round(row.spreadAfterFees * 10000) / 100,
    stakeA: row.stakeA,
    stakeB: row.stakeB,
    payout: row.payoutIfA,
    verdict: row.verdict,
    warnings: Array.isArray(row.warnings) ? row.warnings : [],
    oddsA: row.oddsA,
    oddsB: row.oddsB,
    quoteAgeDeltaMs: row.quoteAgeDeltaMs,
    detectedAt: row.detectedAt.toISOString(),
  };
}

export async function getLatestSpreads(limit = 60): Promise<SpreadRow[]> {
  const rows = await db.select().from(spreads).orderBy(desc(spreads.detectedAt), desc(spreads.spreadAfterFees)).limit(400);
  const seen = new Set<string>();
  const out: SpreadRow[] = [];
  for (const row of rows) {
    const key = `${row.eventKey}|${row.platformA}|${row.platformB}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(toSpreadRow(row));
    if (out.length >= limit) break;
  }
  return out.sort((a, b) => b.spreadAfterFeesPct - a.spreadAfterFeesPct);
}

export async function getSpreadHistory(limit = 200, minPct = 0, verdict?: string): Promise<SpreadRow[]> {
  const conditions = [gte(spreads.spreadAfterFees, minPct / 100)];
  if (verdict) conditions.push(eq(spreads.verdict, verdict));
  const rows = await db
    .select()
    .from(spreads)
    .where(and(...conditions))
    .orderBy(desc(spreads.detectedAt))
    .limit(limit);
  return rows.map(toSpreadRow);
}

export interface QuoteSnapshot {
  platform: string;
  marketId: string;
  title: string;
  outcome: string;
  marketType: string;
  odds: number | null;
  probability: number;
  asset: string | null;
  contractType: string | null;
  strike: number | null;
  expiry: string | null;
  windowStart: string | null;
  source: string;
  fetchedAt: string;
  matchedEventKey: string | null;
}

export async function getQuoteSnapshots(limit = 300): Promise<QuoteSnapshot[]> {
  const rows = await db.select().from(quotesTable).orderBy(desc(quotesTable.fetchedAt)).limit(limit);
  const matches = await db
    .select({
      platform: eventMatches.platform,
      marketId: eventMatches.marketId,
      outcome: eventMatches.outcome,
      eventKey: eventMatches.eventKey,
    })
    .from(eventMatches)
    .limit(4000);
  const keyOf = new Map(matches.map((m) => [`${m.platform}::${m.marketId}::${m.outcome}`, m.eventKey]));

  const seen = new Set<string>();
  const out: QuoteSnapshot[] = [];
  for (const row of rows) {
    const key = `${row.platform}::${row.marketId}::${row.outcome}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({
      platform: row.platform,
      marketId: row.marketId,
      title: row.rawTitle,
      outcome: row.outcome,
      marketType: row.marketType,
      odds: row.odds,
      probability: row.impliedProbability,
      asset: row.asset,
      contractType: row.contractType,
      strike: row.strike,
      expiry: row.expiry?.toISOString() ?? null,
      windowStart: row.windowStart?.toISOString() ?? null,
      source: row.source,
      fetchedAt: row.fetchedAt.toISOString(),
      matchedEventKey: keyOf.get(key) ?? null,
    });
  }
  return out;
}

export interface CollectorStatus extends CollectorMeta {
  lastRun: {
    status: string;
    at: string;
    items: number;
    durationMs: number | null;
    httpStatus: number | null;
    error: string | null;
  } | null;
  probes: ReturnType<typeof diagnosticsFor>["probes"];
  fallbackReason: string | null;
}

export async function getCollectorStatuses(): Promise<CollectorStatus[]> {
  const [rows, runs] = await Promise.all([
    getPlatformRows(),
    db.select().from(collectorRuns).orderBy(desc(collectorRuns.startedAt)).limit(120),
  ]);
  return rows.map((p) => {
    const last = runs.find((r) => r.platform === p.slug) ?? null;
    const diag = diagnosticsFor(p.slug);
    return {
      slug: p.slug,
      name: p.name,
      kind: p.kind,
      mode: p.mode as CollectorMeta["mode"],
      defaultMarketType: p.defaultMarketType as CollectorMeta["defaultMarketType"],
      feeBps: p.feeBps,
      slippageBps: p.slippageBps,
      minIntervalMs: p.minIntervalMs,
      endpointHint: p.endpointHint,
      resolutionSource: p.resolutionSource,
      notes: p.notes,
      enabled: p.enabled,
      candidates: candidatesFor(p.slug),
      lastRun: last
        ? {
            status: last.status,
            at: last.startedAt.toISOString(),
            items: last.itemsCount,
            durationMs: last.durationMs,
            httpStatus: last.httpStatus,
            error: last.errorMessage,
          }
        : null,
      probes: diag.probes ?? [],
      fallbackReason: diag.fallbackReason ?? null,
    };
  });
}

export interface OverviewStats {
  quotesLastHour: number;
  historyRows: number;
  eventsTotal: number;
  matchedEvents: number;
  spreadsLast24h: number;
  arbLast24h: number;
  bestSpread24h: number | null;
}

export async function getOverviewStats(): Promise<OverviewStats> {
  const [quoteCount] = await db
    .select({ n: sql<number>`count(*)::int` })
    .from(quotesTable)
    .where(gte(quotesTable.fetchedAt, new Date(Date.now() - 3600_000)));
  const [historyCount] = await db.select({ n: sql<number>`count(*)::int` }).from(priceHistory);
  const [eventCount] = await db.select({ n: sql<number>`count(*)::int` }).from(events);
  const matchedRows = await db
    .select({ n: sql<number>`count(distinct ${events.eventKey})::int` })
    .from(events)
    .innerJoin(eventMatches, eq(eventMatches.eventKey, events.eventKey));
  const spreadAgg = await db
    .select({
      n: sql<number>`count(*)::int`,
      arb: sql<number>`count(*) filter (where ${spreads.verdict} = 'arb')::int`,
      best: sql<number>`coalesce(max(${spreads.spreadAfterFees}), 0)::float8`,
    })
    .from(spreads)
    .where(gte(spreads.detectedAt, new Date(Date.now() - 24 * 3600_000)));

  return {
    quotesLastHour: quoteCount?.n ?? 0,
    historyRows: historyCount?.n ?? 0,
    eventsTotal: eventCount?.n ?? 0,
    matchedEvents: matchedRows[0]?.n ?? 0,
    spreadsLast24h: spreadAgg[0]?.n ?? 0,
    arbLast24h: spreadAgg[0]?.arb ?? 0,
    bestSpread24h: spreadAgg[0]?.best ? Math.round(spreadAgg[0].best * 10000) / 100 : null,
  };
}

export async function getHistorySeries(eventKey: string, limit = 400) {
  const rows = await db
    .select()
    .from(priceHistory)
    .where(eq(priceHistory.eventKey, eventKey))
    .orderBy(desc(priceHistory.fetchedAt))
    .limit(limit);
  return rows
    .map((r) => ({
      platform: r.platform,
      outcome: r.outcome,
      probability: r.impliedProbability,
      odds: r.odds,
      spotPrice: r.spotPrice,
      at: r.fetchedAt.toISOString(),
    }))
    .reverse();
}

export async function getEventList(limit = 100) {
  const rows = await db.select().from(events).orderBy(desc(events.createdAt)).limit(limit);
  const keys = rows.map((r) => r.eventKey);
  const legs = keys.length
    ? await db.select().from(eventMatches).where(inArray(eventMatches.eventKey, keys))
    : [];
  // Последний тик по каждой ноге: DISTINCT ON вместо скана последних строк.
  const latestResult = await db.execute<{
    platform: string;
    market_id: string;
    outcome: string;
    implied_probability: number;
    odds: number | null;
    fetched_at: Date;
  }>(sql`select distinct on (platform, market_id, outcome) platform, market_id, outcome, implied_probability, odds, fetched_at
        from quotes order by platform, market_id, outcome, fetched_at desc`);
  const latest = latestResult.rows;
  return rows.map((event) => ({
    eventKey: event.eventKey,
    title: event.title,
    asset: event.asset,
    kind: event.kind,
    strike: event.strike,
    expiry: event.expiry?.toISOString() ?? null,
    windowStart: event.windowStart?.toISOString() ?? null,
    matchSource: event.matchSource,
    resolutionSource: event.resolutionSource,
    legs: legs
      .filter((l) => l.eventKey === event.eventKey)
      .map((leg) => {
        const quote = latest.find(
          (q) => q.platform === leg.platform && q.market_id === leg.marketId && q.outcome === leg.outcome,
        );
        return {
          platform: leg.platform,
          marketId: leg.marketId,
          outcome: leg.outcome,
          source: leg.source,
          probability: quote?.implied_probability ?? null,
          odds: quote?.odds ?? null,
          fetchedAt: quote ? new Date(quote.fetched_at).toISOString() : null,
        };
      }),
  }));
}

/**
 * "Несматченные" = рынки, у которых пока нет пары на другой площадке.
 * Именно из этого списка заполняется ручной маппинг (event_map).
 */
export async function getUnmatchedMarkets(limit = 60) {
  const [snapshots, legs] = await Promise.all([
    getQuoteSnapshots(400),
    db.select({ eventKey: eventMatches.eventKey, platform: eventMatches.platform }).from(eventMatches).limit(6000),
  ]);
  const platformsByEvent = new Map<string, Set<string>>();
  for (const leg of legs) {
    const set = platformsByEvent.get(leg.eventKey) ?? new Set<string>();
    set.add(leg.platform);
    platformsByEvent.set(leg.eventKey, set);
  }
  return snapshots
    .filter((s) => (s.matchedEventKey ? (platformsByEvent.get(s.matchedEventKey)?.size ?? 0) < 2 : true))
    .slice(0, limit);
}
