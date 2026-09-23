import { db } from "@/db";
import { lagSamples } from "@/db/schema";
import { desc, eq } from "drizzle-orm";
import { lagSummary } from "@/lib/engine/lag";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const url = new URL(request.url);
  const platform = url.searchParams.get("platform") ?? undefined;
  const [summary, rows] = await Promise.all([
    lagSummary(platform),
    db
      .select()
      .from(lagSamples)
      .where(platform ? eq(lagSamples.platform, platform) : undefined)
      .orderBy(desc(lagSamples.sampledAt))
      .limit(80),
  ]);
  return Response.json({
    ok: true,
    summary,
    samples: rows.map((r) => ({
      platform: r.platform,
      marketId: r.marketId,
      outcome: r.outcome,
      spotBefore: r.spotBefore,
      spotAfter: r.spotAfter,
      spotMovePct: r.spotMovePct,
      probBefore: r.probBefore,
      probAfter: r.probAfter,
      lagMs: r.lagMs,
      sampledAt: r.sampledAt.toISOString(),
    })),
  });
}
