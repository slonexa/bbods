import { db } from "@/db";
import { spreads } from "@/db/schema";
import { and, desc, gte } from "drizzle-orm";
import { config } from "@/lib/config";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const minSpread = searchParams.has("minSpread")
    ? Number(searchParams.get("minSpread"))
    : undefined;
  const limit = Math.min(200, Number(searchParams.get("limit") ?? "100") || 100);

  // Only the freshest row per event pair matters for the dashboard "topline"
  // (spec section 5). We also only look at recent rows (a generous multiple
  // of the poll interval) so a pairing that stopped being produced (one side
  // no longer lists that market) ages out of the live view instead of
  // lingering forever — full history stays queryable straight from Postgres.
  const freshSince = new Date(Date.now() - Math.max(config.pollIntervalMs * 12, 5 * 60_000));
  const rows = await db
    .select()
    .from(spreads)
    .where(
      and(
        gte(spreads.detectedAt, freshSince),
        minSpread !== undefined ? gte(spreads.spreadAfterFees, minSpread) : undefined,
      ),
    )
    .orderBy(desc(spreads.detectedAt))
    .limit(1000);

  const latestByKey = new Map<string, (typeof rows)[number]>();
  for (const row of rows) {
    const key = `${row.eventKey}|${row.platformA}|${row.platformB}|${row.outcomeA}|${row.outcomeB}`;
    if (!latestByKey.has(key)) latestByKey.set(key, row);
  }

  const deduped = Array.from(latestByKey.values())
    .sort((a, b) => b.spreadAfterFees - a.spreadAfterFees)
    .slice(0, limit);

  return Response.json({
    defaultAlertThreshold: config.defaultAlertThreshold,
    count: deduped.length,
    spreads: deduped,
  });
}
