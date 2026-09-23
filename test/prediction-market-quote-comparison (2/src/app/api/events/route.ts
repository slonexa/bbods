import { db } from "@/db";
import { eventMatches } from "@/db/schema";
import { eq } from "drizzle-orm";
import { getEventList, getUnmatchedMarkets } from "@/lib/queries";
import { ensureSeed } from "@/lib/settings";

export const dynamic = "force-dynamic";

export async function GET() {
  const [events, unmatched] = await Promise.all([getEventList(120), getUnmatchedMarkets(80)]);
  return Response.json({ ok: true, events, unmatched });
}

/**
 * Ручной маппинг (аналог event_map.json из раздела 3 ТЗ):
 * привязываем market_id одной площадки к событию. Человек правит бота — это норма для спорт/событийных рынков.
 */
export async function POST(request: Request) {
  await ensureSeed();
  const body = (await request.json()) as {
    eventKey?: string;
    newEventTitle?: string;
    platform?: string;
    marketId?: string;
    outcome?: string;
    note?: string;
  };
  if (!body.platform || !body.marketId || !body.outcome) {
    return Response.json({ ok: false, error: "нужны platform, marketId, outcome" }, { status: 400 });
  }
  let eventKey = body.eventKey ?? null;
  if (!eventKey && body.newEventTitle) {
    eventKey = `manual:${body.newEventTitle.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim().slice(0, 90)}`;
  }
  if (!eventKey) {
    return Response.json({ ok: false, error: "нужен eventKey или newEventTitle" }, { status: 400 });
  }
  await db
    .insert(eventMatches)
    .values({
      eventKey,
      platform: body.platform,
      marketId: body.marketId,
      outcome: body.outcome.toLowerCase(),
      source: "manual",
      confidence: 1,
      note: body.note ?? null,
    })
    .onConflictDoNothing();
  return Response.json({ ok: true, eventKey });
}

export async function DELETE(request: Request) {
  const url = new URL(request.url);
  const id = Number(url.searchParams.get("id"));
  if (!Number.isFinite(id)) return Response.json({ ok: false, error: "нужен id" }, { status: 400 });
  await db.delete(eventMatches).where(eq(eventMatches.id, id));
  return Response.json({ ok: true });
}
