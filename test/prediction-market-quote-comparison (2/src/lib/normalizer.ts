import { db } from "@/db";
import { events, eventMatches } from "@/db/schema";
import { and, eq, inArray } from "drizzle-orm";
import type { Quote } from "./types";

/**
 * Normalizer: превращаем разнобой формулировок в каноническое событие.
 *
 * Крипта (up/down, target, range) — полностью программно по ключу
 * (актив, тип контракта, страйк, окно старта/экспирация). Ключ НЕ содержит
 * направление: направление — свойство "ноги", иначе не сравнить YES с NO.
 *
 * Спорт/прочее — ключ по нормализованному заголовку, а связка между площадками
 * только вручную (аналог event_map.json, раздел 3 ТЗ).
 */

const WINDOW_MS_TOLERANCE = 60_000;

function roundMinute(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return null;
  return Math.round(t / WINDOW_MS_TOLERANCE) * WINDOW_MS_TOLERANCE;
}

function roundSecond(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? Math.round(t / 1000) * 1000 : null;
}

export function canonicalDirection(direction: Quote["direction"], outcome: string): string {
  const o = outcome.toLowerCase();
  if (["up", "above", "over", "yes-up", "long"].includes(o)) return "up";
  if (["down", "below", "under", "no-up", "short"].includes(o)) return "down";
  if (["inside", "in", "within"].includes(o)) return "inside";
  if (["outside", "out"].includes(o)) return "outside";
  if (o === "yes") return direction ?? "yes";
  if (o === "no") return direction === "above" ? "below" : direction === "below" ? "above" : "no";
  return direction ?? o;
}

export function buildEventKey(quote: Quote): { key: string; kind: string } | null {
  const asset = quote.asset?.toUpperCase() ?? null;
  if (asset && quote.contract_type && quote.contract_type !== "other") {
    const contractType = quote.contract_type;
    const strike =
      quote.strike !== null && quote.strike !== undefined
        ? Number(quote.strike).toFixed(quote.strike < 1000 ? 2 : 0)
        : "NA";
    // для ультра-коротких окон совпадение должно быть секунда в секунду (раздел 8.1),
    // для дневных/недельных — достаточно минуты
    const isShort = contractType === "updown";
    const t = isShort ? roundSecond(quote.expiry) : roundMinute(quote.expiry);
    if (t === null) return null;
    const start = isShort ? roundSecond(quote.window_start) : roundMinute(quote.window_start);
    const range = contractType === "range" && quote.strike ? `:${Number(quote.strike).toFixed(0)}` : "";
    return {
      key: `crypto:${asset}:${contractType}:${strike}${range}:${start ?? "NA"}:${new Date(t).toISOString()}`,
      kind: contractType === "updown" ? "updown" : contractType === "range" ? "range" : "target",
    };
  }
  const norm = quote.raw_title
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return { key: `manual:${norm.slice(0, 90)}`, kind: "other" };
}

export interface MatchStats {
  quotesProcessed: number;
  eventsCreated: number;
  matchesCreated: number;
  unmatched: number;
  assignments: Array<{ platform: string; marketId: string; outcome: string; eventKey: string; kind: string }>;
}

/** Полный прогон нормализатора по свежим котировкам. */
export async function runNormalizer(quotes: Quote[]): Promise<MatchStats> {
  const stats: MatchStats = {
    quotesProcessed: quotes.length,
    eventsCreated: 0,
    matchesCreated: 0,
    unmatched: 0,
    assignments: [],
  };
  if (quotes.length === 0) return stats;

  // 1. Ручные маппинги имеют приоритет над авто-ключом (человек правит бота).
  const manual = await db.select().from(eventMatches).where(eq(eventMatches.source, "manual"));
  const manualIndex = new Map<string, (typeof manual)[number]>();
  for (const row of manual) manualIndex.set(`${row.platform}::${row.marketId}::${row.outcome.toLowerCase()}`, row);

  const eventRows: Array<typeof events.$inferInsert> = [];
  const matchRows: Array<typeof eventMatches.$inferInsert> = [];
  const keyOf = new Map<string, { key: string; kind: string }>();

  for (const quote of quotes) {
    const manualRow = manualIndex.get(`${quote.platform}::${quote.market_id}::${quote.outcome.toLowerCase()}`);
    const resolved = manualRow ? { key: manualRow.eventKey, kind: "other" } : buildEventKey(quote);
    if (!resolved) {
      stats.unmatched += 1;
      continue;
    }
    keyOf.set(`${quote.platform}::${quote.market_id}::${quote.outcome.toLowerCase()}`, resolved);
    stats.assignments.push({
      platform: quote.platform,
      marketId: quote.market_id,
      outcome: quote.outcome.toLowerCase(),
      eventKey: resolved.key,
      kind: resolved.kind,
    });
    const dir = canonicalDirection(quote.direction, quote.outcome);
    eventRows.push({
      eventKey: resolved.key,
      title: quote.raw_title,
      asset: quote.asset?.toUpperCase() ?? null,
      contractType: quote.contract_type ?? null,
      direction: null,
      strike: quote.strike ?? null,
      expiry: quote.expiry ? new Date(quote.expiry) : null,
      windowStart: quote.window_start ? new Date(quote.window_start) : null,
      kind: resolved.kind,
      resolutionSource: quote.resolution_source ?? null,
      matchSource: manualRow ? "manual" : "auto",
    });
    matchRows.push({
      eventKey: resolved.key,
      platform: quote.platform,
      marketId: quote.market_id,
      outcome: quote.outcome.toLowerCase(),
      source: manualRow ? "manual" : "auto",
      confidence: manualRow ? 1 : 0.9,
      note: manualRow?.note ?? null,
    });
    void dir;
  }

  if (eventRows.length === 0) return stats;

  const uniqueEvents = new Map<string, (typeof eventRows)[number]>();
  for (const row of eventRows) if (!uniqueEvents.has(row.eventKey)) uniqueEvents.set(row.eventKey, row);

  const keys = [...uniqueEvents.keys()];
  const existing = keys.length
    ? await db.select({ eventKey: events.eventKey }).from(events).where(inArray(events.eventKey, keys))
    : [];
  const existingSet = new Set(existing.map((e) => e.eventKey));

  const toInsert = [...uniqueEvents.values()].filter((row) => !existingSet.has(row.eventKey));
  if (toInsert.length > 0) {
    await db.insert(events).values(toInsert).onConflictDoNothing();
    stats.eventsCreated = toInsert.length;
  }

  if (matchRows.length > 0) {
    const inserted = await db.insert(eventMatches).values(matchRows).onConflictDoNothing().returning({ id: eventMatches.id });
    stats.matchesCreated = inserted.length;
  }

  return stats;
}

/** Связки ног для одного события (по ним считается спред). */
export async function legsForEvents(keys: string[]) {
  if (keys.length === 0) return [];
  return db.select().from(eventMatches).where(inArray(eventMatches.eventKey, keys));
}

export async function deleteMatch(eventKey: string, platform: string, marketId: string, outcome: string) {
  await db
    .delete(eventMatches)
    .where(
      and(
        eq(eventMatches.eventKey, eventKey),
        eq(eventMatches.platform, platform),
        eq(eventMatches.marketId, marketId),
        eq(eventMatches.outcome, outcome),
      ),
    );
}
