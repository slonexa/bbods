import { runCycle } from "@/lib/pipeline";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

export async function POST(request: Request) {
  let force = false;
  try {
    const body = (await request.json()) as { force?: boolean } | null;
    force = Boolean(body?.force);
  } catch {
    force = false;
  }
  try {
    const summary = await runCycle({ force });
    return Response.json({ ok: true, summary });
  } catch (error) {
    return Response.json(
      { ok: false, error: error instanceof Error ? error.message : String(error) },
      { status: 500 },
    );
  }
}

export async function GET() {
  return POST(new Request("http://local/cycle", { method: "POST" }));
}
