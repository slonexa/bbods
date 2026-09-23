import { db } from "@/db";
import { rawQuotes } from "@/db/schema";
import { desc } from "drizzle-orm";

export const dynamic = "force-dynamic";

// Returns the most recent quote per (platform, marketId, outcome) — i.e. a
// live snapshot of what every collector currently sees, useful for debugging
// the collectors/normalizer without digging through raw history.
export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const platform = searchParams.get("platform") ?? undefined;
  const limitRows = Math.min(2000, Number(searchParams.get("scan") ?? "600") || 600);

  const rows = await db
    .select()
    .from(rawQuotes)
    .orderBy(desc(rawQuotes.fetchedAt))
    .limit(limitRows);

  const filtered = platform ? rows.filter((r) => r.platform === platform) : rows;

  const latestByKey = new Map<string, (typeof rows)[number]>();
  for (const row of filtered) {
    const key = `${row.platform}|${row.marketId}|${row.outcome}`;
    if (!latestByKey.has(key)) latestByKey.set(key, row);
  }

  const quotes = Array.from(latestByKey.values()).sort((a, b) => a.platform.localeCompare(b.platform));

  return Response.json({ count: quotes.length, quotes });
}
