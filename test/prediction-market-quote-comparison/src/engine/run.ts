import { db } from "@/db";
import { rawQuotes, spreads } from "@/db/schema";
import { sql } from "drizzle-orm";
import { runAllCollectors } from "@/collectors";
import { buildEventGroups } from "@/normalizer";
import { computeAllSpreads } from "@/engine/spread";
import { config } from "@/lib/config";
import { collectorResultsToStatus, recordCycle } from "@/engine/status";

// One full poll cycle: collect -> normalize -> compute spreads -> persist.
// Every collector is isolated (one failing doesn't stop the others) and every
// quote is logged to `raw_quotes` regardless of whether it matched anything,
// so later you can backtest ideas like section 4.5 on real history.
export async function runCycle(): Promise<void> {
  const startedAt = new Date();
  try {
    const results = await runAllCollectors();
    const allQuotes = results.flatMap((r) => r.quotes);

    if (allQuotes.length > 0) {
      await db.insert(rawQuotes).values(
        allQuotes.map((q) => ({
          platform: q.platform,
          marketId: q.marketId,
          rawTitle: q.rawTitle,
          outcome: q.outcome,
          impliedProbability: q.impliedProbability,
          marketType: q.marketType,
          expiry: q.expiry ? new Date(q.expiry) : null,
          meta: q.meta,
          isSimulated: q.isSimulated,
          fetchedAt: new Date(q.fetchedAt),
        })),
      );
    }

    const groups = buildEventGroups(allQuotes);
    const computed = computeAllSpreads(groups);

    if (computed.length > 0) {
      await db.insert(spreads).values(
        computed.map((c) => ({
          eventKey: c.eventKey,
          assetOrTopic: c.assetOrTopic,
          contractType: c.contractType,
          platformA: c.a.platform,
          marketIdA: c.a.marketId,
          marketTypeA: c.a.marketType,
          outcomeA: c.a.outcome,
          probA: c.a.impliedProbability,
          platformB: c.b.platform,
          marketIdB: c.b.marketId,
          marketTypeB: c.b.marketType,
          outcomeB: c.b.outcome,
          probB: c.b.impliedProbability,
          edgeRaw: c.edgeRaw,
          feesAndSlippage: c.feesAndSlippage,
          spreadAfterFees: c.spreadAfterFees,
          timeframeMismatch: c.timeframeMismatch,
          isSimulatedPair: c.isSimulatedPair,
          suspicious: c.suspicious,
          note: c.note,
        })),
      );
    }

    // Cheap housekeeping so raw_quotes/spreads don't grow forever on a
    // long-running local dashboard, and so stale pairings (a market that no
    // longer exists on one side) age out of the "latest" dashboard view
    // instead of lingering forever.
    await db.execute(sql`
      delete from ${rawQuotes}
      where id in (
        select id from ${rawQuotes}
        order by id desc
        offset ${config.maxRawQuoteRowsKeep}
      )
    `);
    await db.execute(sql`
      delete from ${spreads}
      where id in (
        select id from ${spreads}
        order by id desc
        offset ${config.maxRawQuoteRowsKeep}
      )
    `);

    const finishedAt = new Date();
    recordCycle({
      startedAt: startedAt.toISOString(),
      finishedAt: finishedAt.toISOString(),
      durationMs: finishedAt.getTime() - startedAt.getTime(),
      collectors: collectorResultsToStatus(results),
      quoteCount: allQuotes.length,
      groupCount: groups.length,
      spreadCount: computed.length,
      actionableSpreadCount: computed.filter((c) => c.spreadAfterFees >= config.defaultAlertThreshold).length,
    });
  } catch (err) {
    const finishedAt = new Date();
    recordCycle({
      startedAt: startedAt.toISOString(),
      finishedAt: finishedAt.toISOString(),
      durationMs: finishedAt.getTime() - startedAt.getTime(),
      collectors: [],
      quoteCount: 0,
      groupCount: 0,
      spreadCount: 0,
      actionableSpreadCount: 0,
      error: err instanceof Error ? err.message : String(err),
    });
  }
}
