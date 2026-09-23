import { db } from "@/db";
import { collectorRuns } from "@/db/schema";
import { ensureSeed, getPlatform, getPlatformRows, getSettings } from "../settings";
import { describeError, nowIso } from "../http";
import { collectPolymarket } from "./polymarket";
import { probeCexCandidates, type CandidateProbe } from "./cexOdds";
import { simulateQuotes } from "./simulator";
import type { CollectorResult, Quote } from "../types";

/**
 * Оркестратор collector'ов. Два защитных механизма, чтобы не спамить площадки:
 *  - minIntervalMs из реестра (троттлинг в http.ts);
 *  - backoff после неудачи: сдохший эндпоинт не дёргаем 10 минут.
 */

const FAILURE_BACKOFF_MS = 10 * 60 * 1000;
const failureBackoff = new Map<string, number>();

export interface CollectorDiagnostics {
  probes?: CandidateProbe[];
  fallbackReason?: string;
}

const diagBySlug = new Map<string, CollectorDiagnostics>();

export function diagnosticsFor(slug: string): CollectorDiagnostics {
  return diagBySlug.get(slug) ?? {};
}

export async function runCollector(slug: string, opts: { force?: boolean } = {}): Promise<CollectorResult> {
  await ensureSeed();
  const platform = await getPlatform(slug);
  const started = Date.now();
  if (!platform || !platform.enabled) {
    return {
      platform: slug,
      endpoint: null,
      httpStatus: null,
      status: "disabled",
      items: [],
      parsed: 0,
      durationMs: 0,
      error: "platform disabled",
    };
  }

  const nextRetry = failureBackoff.get(slug) ?? 0;
  if (!opts.force && nextRetry > Date.now()) {
    return {
      platform: slug,
      endpoint: null,
      httpStatus: null,
      status: "throttled",
      items: [],
      parsed: 0,
      durationMs: 0,
      error: `backoff: повторный запрос через ${Math.ceil((nextRetry - Date.now()) / 1000)}s`,
    };
  }

  const settings = await getSettings();
  let quotes: Quote[] = [];
  let status: CollectorResult["status"] = "error";
  let httpStatus: number | null = null;
  let endpoint: string | null = null;
  let error: string | null = null;
  const diag: CollectorDiagnostics = {};

  try {
    if (slug === "polymarket") {
      const res = await collectPolymarket();
      quotes = res.quotes;
      httpStatus = res.status;
      endpoint = endpoint ?? null;
      diag.probes = [{ url: "gamma-api.polymarket.com/markets", ok: true, status: res.status, items: quotes.length }];
      status = quotes.length > 0 ? "ok" : "empty";
      if (status === "empty") error = "эндпоинт ответил, но распознанных рынков 0";
    } else if (platform.mode === "planned") {
      status = "disabled";
      error = "collector в статусе planned — включи после появления реального эндпоинта";
    } else {
      const probed = await probeCexCandidates(slug);
      diag.probes = probed.probes;
      httpStatus = probed.status;
      quotes = probed.quotes;
      if (quotes.length > 0) {
        endpoint = probed.endpoint;
        status = "ok";
      } else {
        status = "error";
        error = probed.error ?? "пустой ответ";
      }
    }
  } catch (err) {
    status = "error";
    error = describeError(err);
  }

  if (status !== "ok") failureBackoff.set(slug, Date.now() + FAILURE_BACKOFF_MS);
  else failureBackoff.delete(slug);

  let fellBackToSimulator = false;
  if (status !== "ok" && settings.allowSimulatedFallback) {
    fellBackToSimulator = true;
    diag.fallbackReason = error ?? "пустой ответ";
    quotes = await simulateQuotes(slug);
    status = quotes.length > 0 ? "ok" : status;
  }
  diagBySlug.set(slug, diag);

  const durationMs = Date.now() - started;
  try {
    await db.insert(collectorRuns).values({
      platform: slug,
      endpoint,
      finishedAt: new Date(),
      durationMs,
      httpStatus,
      itemsCount: quotes.length,
      parsedCount: quotes.length,
      status: fellBackToSimulator ? "simulated" : status,
      errorMessage: error,
    });
  } catch (err) {
    console.error("[collector] не удалось записать лог запуска", describeError(err));
  }

  return {
    platform: slug,
    endpoint,
    httpStatus,
    status,
    items: quotes,
    parsed: quotes.length,
    durationMs,
    error,
    fellBackToSimulator,
  };
}

export async function collectAll(slugs?: string[], opts: { force?: boolean } = {}): Promise<CollectorResult[]> {
  const rows = await getPlatformRows();
  const list = slugs ?? rows.filter((p) => p.enabled).map((p) => p.slug);
  return Promise.all(list.map((slug) => runCollector(slug, opts)));
}

export function fetchedNow(): string {
  return nowIso();
}
