import { fetchJson, describeError } from "@/lib/http";
import { findMarketArray, mapGenericMarket } from "@/lib/collectors/cexOdds";

export const dynamic = "force-dynamic";
export const maxDuration = 30;

interface Found {
  path: string;
  items: number;
  sampleKeys: string[];
}

function walk(data: unknown, path: string, out: Found[], depth = 0): void {
  if (depth > 8 || data === null || typeof data !== "object") return;
  if (Array.isArray(data)) {
    const first = data.find((x) => x && typeof x === "object" && !Array.isArray(x));
    if (first) {
      out.push({ path: path || "$", items: data.length, sampleKeys: Object.keys(first as object).slice(0, 30) });
    }
    for (const item of data.slice(0, 3)) walk(item, `${path}[]`, out, depth + 1);
    return;
  }
  for (const [key, value] of Object.entries(data as Record<string, unknown>)) {
    walk(value, path ? `${path}.${key}` : key, out, depth + 1);
  }
}

/**
 * Помощник для реверса внутренних эндпоинтов (раздел 2 ТЗ).
 * Workflow: DevTools → Network → скопировать URL запроса → вставить сюда →
 * получаешь статус, форму JSON, сколько "рынков" удалось распознать и как они смапились.
 */
export async function POST(request: Request) {
  const body = (await request.json().catch(() => ({}))) as { url?: string; platform?: string };
  const url = body.url?.trim();
  if (!url || !/^https?:\/\//i.test(url)) {
    return Response.json({ ok: false, error: "нужен абсолютный http(s) URL" }, { status: 400 });
  }
  try {
    const { data, status } = await fetchJson<unknown>({ url, timeoutMs: 9000, minIntervalMs: 0 });
    const arrays: Found[] = [];
    walk(data, "", arrays);
    arrays.sort((a, b) => b.items - a.items);

    const markets = findMarketArray(data);
    const platform = body.platform ?? "unknown";
    const mapped = markets.slice(0, 25).flatMap((m) => mapGenericMarket(m, platform, url));
    const rawKeys = markets[0] ? Object.keys(markets[0]) : [];

    return Response.json({
      ok: true,
      status,
      byteLength: JSON.stringify(data).length,
      isArrayRoot: Array.isArray(data),
      arrays: arrays.slice(0, 12),
      marketCandidates: markets.length,
      rawKeys,
      mappedCount: mapped.length,
      mappedSample: mapped.slice(0, 4),
      hints: [
        markets.length === 0
          ? "структура не похожа на список рынков: ищи массив объектов с id/title/price"
          : `найдено ${markets.length} объектов, распознано ${mapped.length} — смотри rawKeys, чтобы добавить синонимы полей в cexOdds.ts`,
        mapped.length > 0 && mapped.every((m) => m.expiry === null)
          ? "expiry не распознан: проверь, в каком поле лежит время окончания окна (EXPIRY_FIELDS)"
          : null,
        mapped.length > 0 && mapped.every((m) => m.market_type === "fixed_odds")
          ? "значения > 1 → считаем fixed_odds (коэффициент). Если это проценты — правь эвристику типа рынка"
          : null,
      ].filter(Boolean),
    });
  } catch (error) {
    return Response.json({ ok: false, error: describeError(error), url }, { status: 200 });
  }
}
