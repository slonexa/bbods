import { db } from "@/db";
import { platforms } from "@/db/schema";
import { eq } from "drizzle-orm";
import { runCollector } from "@/lib/collectors";
import { getCollectorStatuses } from "@/lib/queries";
import { ensureSeed } from "@/lib/settings";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

export async function GET() {
  const collectors = await getCollectorStatuses();
  return Response.json({ ok: true, collectors });
}

/** Ручной запуск одного collector'а (force игнорирует backoff после неудачи). */
export async function POST(request: Request) {
  await ensureSeed();
  const body = (await request.json().catch(() => ({}))) as { slug?: string; force?: boolean; enabled?: boolean };
  if (!body.slug) return Response.json({ ok: false, error: "нужен slug" }, { status: 400 });

  if (body.enabled !== undefined) {
    await db
      .update(platforms)
      .set({ enabled: body.enabled, updatedAt: new Date() })
      .where(eq(platforms.slug, body.slug));
  }

  const result = await runCollector(body.slug, { force: body.force ?? true });
  return Response.json({
    ok: true,
    result: {
      platform: result.platform,
      status: result.status,
      items: result.items.length,
      durationMs: result.durationMs,
      httpStatus: result.httpStatus,
      endpoint: result.endpoint,
      error: result.error ?? null,
      simulated: Boolean(result.fellBackToSimulator),
      sample: result.items.slice(0, 3),
    },
  });
}
