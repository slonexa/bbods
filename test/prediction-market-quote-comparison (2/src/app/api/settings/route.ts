import { db } from "@/db";
import { platforms } from "@/db/schema";
import { eq } from "drizzle-orm";
import { getSettings, updateSetting, type SettingsKey } from "@/lib/settings";

export const dynamic = "force-dynamic";

export async function GET() {
  const settings = await getSettings();
  return Response.json({ ok: true, settings });
}

export async function POST(request: Request) {
  const body = (await request.json()) as { settings?: Record<string, string | number | boolean> } & Record<
    string,
    string | number | boolean
  >;
  const payload = body.settings ?? body;
  for (const [key, value] of Object.entries(payload)) {
    if (key === "dashboardRefreshMs" || key === "alertThresholdPct" || key === "logThresholdPct" ||
        key === "bankrollUsd" || key === "maxQuoteAgeDeltaMs" || key === "allowSimulatedFallback") {
      await updateSetting(key as SettingsKey, String(value));
    }
  }
  // обновление фи/интервалов площадки
  if (payload.platform && typeof payload.platform === "string") {
    const patch: Record<string, unknown> = {};
    for (const field of ["feeBps", "slippageBps", "minIntervalMs"]) {
      const value = payload[field];
      if (value !== undefined && Number.isFinite(Number(value))) patch[field] = Number(value);
    }
    if (payload.enabled !== undefined) patch.enabled = payload.enabled === "true" || payload.enabled === true;
    if (Object.keys(patch).length > 0) {
      patch.updatedAt = new Date();
      await db.update(platforms).set(patch).where(eq(platforms.slug, payload.platform));
    }
  }
  const settings = await getSettings();
  return Response.json({ ok: true, settings });
}
