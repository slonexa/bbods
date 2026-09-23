import { getSpreadHistory } from "@/lib/queries";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const url = new URL(request.url);
  const limit = Math.min(1000, Number(url.searchParams.get("limit") ?? 200) || 200);
  const minPct = Number(url.searchParams.get("min") ?? 0) || 0;
  const verdict = url.searchParams.get("verdict") ?? undefined;
  const rows = await getSpreadHistory(limit, minPct, verdict || undefined);
  return Response.json({ ok: true, count: rows.length, spreads: rows });
}
