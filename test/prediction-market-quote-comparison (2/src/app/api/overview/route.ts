import { getCollectorStatuses, getLatestSpreads, getOverviewStats, getQuoteSnapshots } from "@/lib/queries";
import { getSettings } from "@/lib/settings";
import { spotSeries } from "@/lib/spot-history";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const [settings, collectors, spreads, stats, snapshots] = await Promise.all([
      getSettings(),
      getCollectorStatuses(),
      getLatestSpreads(50),
      getOverviewStats(),
      getQuoteSnapshots(240),
    ]);
    return Response.json({
      ok: true,
      settings,
      collectors,
      spreads,
      stats,
      quotes: snapshots,
      spot: {
        BTC: spotSeries("BTC").slice(-60).map((t) => ({ at: t.at, price: t.price, source: t.source })),
      },
    });
  } catch (error) {
    return Response.json({ ok: false, error: error instanceof Error ? error.message : String(error) }, { status: 500 });
  }
}
