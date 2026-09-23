import { db } from "@/db";
import { platforms, settings } from "@/db/schema";
import { PLATFORM_SEED } from "./platforms";
import { eq, sql } from "drizzle-orm";

export const DEFAULT_SETTINGS = {
  /** порог алерта по спреду после издержек, % */
  alertThresholdPct: 2,
  /** порог для записи в таблицу spreads, % (всё ниже — шум) */
  logThresholdPct: 0.5,
  /** банк для расчёта сплита ставок, $ */
  bankrollUsd: 1000,
  /** интервал автообновления дашборда, мс */
  dashboardRefreshMs: 7000,
  /** разрешено ли collector'у падать в симулятор, если эндпоинт не отвечает */
  allowSimulatedFallback: true,
  /** максимальный разбег возраста котировок, мс — иначе замер несинхронный */
  maxQuoteAgeDeltaMs: 4000,
} as const;

export type SettingsKey = keyof typeof DEFAULT_SETTINGS;

export interface SettingsMap {
  alertThresholdPct: number;
  logThresholdPct: number;
  bankrollUsd: number;
  dashboardRefreshMs: number;
  allowSimulatedFallback: boolean;
  maxQuoteAgeDeltaMs: number;
}

let seeded = false;

export async function ensureSeed(): Promise<void> {
  if (seeded) return;
  for (const p of PLATFORM_SEED) {
    await db
      .insert(platforms)
      .values({
        slug: p.slug,
        name: p.name,
        kind: p.kind,
        mode: p.mode,
        defaultMarketType: p.defaultMarketType,
        feeBps: p.feeBps,
        slippageBps: p.slippageBps,
        minIntervalMs: p.minIntervalMs,
        endpointHint: p.endpointHint,
        resolutionSource: p.resolutionSource,
        notes: p.notes,
      })
      .onConflictDoUpdate({
        target: platforms.slug,
        set: {
          name: p.name,
          kind: p.kind,
          defaultMarketType: p.defaultMarketType,
          endpointHint: p.endpointHint,
          resolutionSource: p.resolutionSource,
          notes: p.notes,
        },
      });
  }
  for (const [key, value] of Object.entries(DEFAULT_SETTINGS)) {
    await db
      .insert(settings)
      .values({ key, value: String(value) })
      .onConflictDoNothing();
  }
  seeded = true;
}

export async function getSettings(): Promise<SettingsMap> {
  await ensureSeed();
  const rows = await db.select().from(settings);
  const map: SettingsMap = { ...DEFAULT_SETTINGS };
  for (const row of rows) {
    if (!(row.key in map)) continue;
    const key = row.key as SettingsKey;
    if (key === "allowSimulatedFallback") {
      map.allowSimulatedFallback = row.value === "true";
      continue;
    }
    const num = Number(row.value);
    if (Number.isFinite(num)) map[key] = num;
  }
  return map;
}

export async function updateSetting(key: SettingsKey, value: string): Promise<void> {
  await ensureSeed();
  await db
    .update(settings)
    .set({ value, updatedAt: new Date() })
    .where(eq(settings.key, key));
}

export async function getPlatformRows() {
  await ensureSeed();
  return db.select().from(platforms).orderBy(sql`${platforms.id} asc`);
}

export async function getPlatform(slug: string) {
  const rows = await getPlatformRows();
  return rows.find((p) => p.slug === slug) ?? null;
}
