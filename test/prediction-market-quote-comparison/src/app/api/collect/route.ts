import { runCycle } from "@/engine/run";
import { getLatestStatus } from "@/engine/status";

export const dynamic = "force-dynamic";

// Manual trigger — handy right after startup (before the first automatic
// poll tick fires) or for debugging a single collector change.
export async function POST() {
  await runCycle();
  return Response.json({ ok: true, latest: getLatestStatus() });
}

export async function GET() {
  await runCycle();
  return Response.json({ ok: true, latest: getLatestStatus() });
}
