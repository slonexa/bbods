import { db } from "@/db";
import { lagSamples, priceHistory } from "@/db/schema";
import { desc, eq, and } from "drizzle-orm";
import type { ModelAsset } from "../market-model";
import { spotSeries } from "../spot-history";
import type { Quote } from "../types";

/**
 * Лаборатория лага (раздел 4.4 ТЗ).
 *
 * Вопрос, на который надо ответить ДО того, как строить низколатентную инфраструктуру:
 * как быстро площадка реально обновляет коэффициент после движения спота?
 * Метод: если в ряде тиков спот сдвинулся, а коэффициент не изменился, и только через
 * N мс коэффициент дёрнулся — это и есть оценка лага.
 * Вывод интерпретируется так:
 *   лаг < 0.5s  → паттерна нет, поллинг тем более бесполезен;
 *   лаг 1-3s+   → есть edge, имеет смысл уходить на WebSocket.
 */

const SPOT_MOVE_THRESHOLD_PCT = 0.02;
const LOOKBACK_TICKS = 12;

export interface LagFinding {
  platform: string;
  marketId: string;
  outcome: string;
  lagMs: number | null;
  spotMovePct: number | null;
  probBefore: number;
  probAfter: number;
}

function referenceSpotSeries(asset: ModelAsset | null): Array<{ at: number; price: number }> {
  if (!asset) return [];
  return spotSeries(asset).map((t) => ({ at: t.at, price: t.price }));
}

function spotAt(series: Array<{ at: number; price: number }>, at: number): number | null {
  if (series.length === 0) return null;
  let best = series[0];
  for (const point of series) {
    if (Math.abs(point.at - at) < Math.abs(best.at - at)) best = point;
  }
  return best.price;
}

export async function recordLagSamples(quotes: Quote[], asset: ModelAsset | null): Promise<LagFinding[]> {
  const groups = new Map<string, Quote>();
  for (const quote of quotes) {
    if (!quote.spot_price && !asset) continue;
    groups.set(`${quote.platform}::${quote.market_id}::${quote.outcome.toLowerCase()}`, quote);
  }
  if (groups.size === 0) return [];

  const refSeries = referenceSpotSeries(asset);
  const findings: LagFinding[] = [];

  for (const [key, current] of groups) {
    const [platform, marketId, outcome] = key.split("::");

    // Ряд тиков этого рынка: цена, вероятность, время. По нему и ищем "спот двинулся, коэффициент нет".
    const rows = await db
      .select()
      .from(priceHistory)
      .where(and(eq(priceHistory.platform, platform), eq(priceHistory.marketId, marketId), eq(priceHistory.outcome, outcome)))
      .orderBy(desc(priceHistory.fetchedAt))
      .limit(LOOKBACK_TICKS + 1);
    if (rows.length < 2) continue;
    const series = [...rows].reverse();

    const spots: Array<number | null> = series.map((row) =>
      row.spotPrice !== null ? row.spotPrice : spotAt(refSeries, row.fetchedAt.getTime()),
    );
    const times = series.map((row) => row.fetchedAt.getTime());
    const probs = series.map((row) => row.impliedProbability);

    // последний индекс, где спот сдвинулся сильнее порога
    let moveIndex = -1;
    for (let i = 1; i < spots.length; i += 1) {
      const before = spots[i - 1];
      const after = spots[i];
      if (before === null || after === null || before === 0) continue;
      const move = ((after - before) / before) * 100;
      if (Math.abs(move) >= SPOT_MOVE_THRESHOLD_PCT) moveIndex = i - 1;
    }
    if (moveIndex < 0) continue;

    // первый индекс после движения, где коэффициент изменился
    let changeIndex = -1;
    for (let i = moveIndex + 1; i < probs.length; i += 1) {
      if (Math.abs(probs[i] - probs[i - 1]) >= 1e-6) {
        changeIndex = i;
        break;
      }
    }
    if (changeIndex < 0) continue;

    const lagMs = Math.max(0, times[changeIndex] - times[moveIndex]);
    const beforeSpot = spots[moveIndex];
    const afterSpot = spots[changeIndex];
    const spotMovePct =
      beforeSpot !== null && afterSpot !== null && beforeSpot > 0
        ? Math.round(((afterSpot - beforeSpot) / beforeSpot) * 1000000) / 10000
        : null;

    const sampledAt = new Date(times[changeIndex]);
    const existing = await db
      .select({ id: lagSamples.id })
      .from(lagSamples)
      .where(and(eq(lagSamples.platform, platform), eq(lagSamples.marketId, marketId), eq(lagSamples.sampledAt, sampledAt)))
      .limit(1);
    if (existing.length > 0) continue;

    findings.push({
      platform,
      marketId,
      outcome,
      lagMs,
      spotMovePct,
      probBefore: probs[changeIndex - 1],
      probAfter: probs[changeIndex],
    });

    await db.insert(lagSamples).values({
      platform,
      marketId,
      outcome,
      spotBefore: beforeSpot,
      spotAfter: afterSpot,
      spotMovePct,
      probBefore: probs[changeIndex - 1],
      probAfter: probs[changeIndex],
      lagMs,
      sampledAt,
    });
  }

  return findings;
}

export interface LagSummary {
  samples: number;
  medianLagMs: number | null;
  p90LagMs: number | null;
  shareOver1s: number;
  shareOver3s: number;
  conclusion: string;
}

export async function lagSummary(platform?: string): Promise<LagSummary> {
  const base = db
    .select({ lagMs: lagSamples.lagMs })
    .from(lagSamples)
    .orderBy(desc(lagSamples.sampledAt))
    .limit(500);
  const rows = platform ? await base.where(eq(lagSamples.platform, platform)) : await base;
  const lags = rows.map((r) => r.lagMs).filter((v): v is number => typeof v === "number" && v >= 0);
  if (lags.length === 0) {
    return {
      samples: rows.length,
      medianLagMs: null,
      p90LagMs: null,
      shareOver1s: 0,
      shareOver3s: 0,
      conclusion: "данных пока нет — нужен непрерывный сбор минимум 1-2 часа",
    };
  }
  const sorted = [...lags].sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)];
  const p90 = sorted[Math.floor(sorted.length * 0.9)];
  const over1s = lags.filter((v) => v > 1000).length / lags.length;
  const over3s = lags.filter((v) => v > 3000).length / lags.length;
  let conclusion: string;
  if (p90 < 700) conclusion = "лаг суб-секундный — паттерн «ловли лага» не работает, инфраструктуру строить рано";
  else if (p90 < 3000) conclusion = "лаг 1-3s — пограничная зона: собираем ещё данных, WebSocket может дать edge";
  else conclusion = "лаг 3s+ — есть устойчивый edge, оправдан отдельный low-latency модуль";
  conclusion +=
    " ВАЖНО: разрешение метода = интервал опроса. Лаг, равный интервалу цикла, означает «не измерено», а не «есть edge» — нужен WS или опрос 1-2 раза в секунду.";
  return {
    samples: rows.length,
    medianLagMs: median,
    p90LagMs: p90,
    shareOver1s: Math.round(over1s * 1000) / 10,
    shareOver3s: Math.round(over3s * 1000) / 10,
    conclusion,
  };
}
