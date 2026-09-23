import { getLatestStatus, getStatusHistory } from "@/engine/status";
import { config } from "@/lib/config";

export const dynamic = "force-dynamic";

export async function GET() {
  return Response.json({
    latest: getLatestStatus(),
    history: getStatusHistory(),
    config: {
      pollIntervalMs: config.pollIntervalMs,
      defaultAlertThreshold: config.defaultAlertThreshold,
      fees: config.fees,
      simulate: config.simulate,
    },
  });
}
