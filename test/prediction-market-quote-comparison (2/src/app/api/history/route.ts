import { getHistorySeries } from "@/lib/queries";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const url = new URL(request.url);
  const eventKey = url.searchParams.get("eventKey");
  if (!eventKey) return Response.json({ ok: false, error: "нужен eventKey" }, { status: 400 });
  const series = await getHistorySeries(eventKey, Number(url.searchParams.get("limit") ?? 400) || 400);
  return Response.json({ ok: true, eventKey, series });
}
