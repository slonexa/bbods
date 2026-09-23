/**
 * Тонкая обёртка над fetch для внутренних (недокументированных) эндпоинтов:
 *  - браузерные заголовки (без агрессивной маскировки под официальный клиент);
 *  - таймаут, чтобы зависший запрос не блокировал цикл;
 *  - троттлинг на хост + джиттер, чтобы не словить rate-limit/капчу (раздел 2 ТЗ).
 */

const DEFAULT_HEADERS: Record<string, string> = {
  "User-Agent":
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
  Accept: "application/json, text/plain, */*",
  "Accept-Language": "en-US,en;q=0.9",
};

const lastHitByHost = new Map<string, number>();

export function nowIso(): string {
  return new Date().toISOString();
}

export class HttpError extends Error {
  constructor(
    message: string,
    readonly status: number | null,
    readonly bodyPreview?: string,
  ) {
    super(message);
    this.name = "HttpError";
  }
}

export interface FetchJsonOptions {
  url: string;
  headers?: Record<string, string>;
  timeoutMs?: number;
  /** минимальная пауза между запросами к одному хосту */
  minIntervalMs?: number;
  method?: "GET" | "POST";
  body?: string;
}

function throttle(host: string, minIntervalMs: number): number {
  const now = Date.now();
  const last = lastHitByHost.get(host) ?? 0;
  const wait = Math.max(0, last + minIntervalMs + Math.floor(Math.random() * 250) - now);
  lastHitByHost.set(host, now + wait);
  return wait;
}

export async function fetchJson<T>(opts: FetchJsonOptions): Promise<{ data: T; status: number }> {
  const {
    url,
    headers = {},
    timeoutMs = 8000,
    minIntervalMs = 0,
    method = "GET",
    body,
  } = opts;

  const host = new URL(url).host;
  const wait = throttle(host, minIntervalMs);
  if (wait > 0) await new Promise((resolve) => setTimeout(resolve, Math.min(wait, 3000)));

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, {
      method,
      headers: { ...DEFAULT_HEADERS, ...headers },
      body,
      signal: controller.signal,
      cache: "no-store",
    });
    const text = await res.text();
    if (!res.ok) {
      throw new HttpError(`HTTP ${res.status} от ${host}`, res.status, text.slice(0, 300));
    }
    try {
      return { data: JSON.parse(text) as T, status: res.status };
    } catch {
      throw new HttpError(`Ответ ${host} не JSON (вероятно HTML/капча)`, res.status, text.slice(0, 300));
    }
  } catch (error) {
    if (error instanceof HttpError) throw error;
    if (error instanceof Error && error.name === "AbortError") {
      throw new HttpError(`Таймаут ${timeoutMs}мс у ${host}`, null);
    }
    throw new HttpError(error instanceof Error ? error.message : "неизвестная сетевая ошибка", null);
  } finally {
    clearTimeout(timer);
  }
}

export function describeError(error: unknown): string {
  if (error instanceof HttpError) return error.message;
  if (error instanceof Error) return error.message;
  return String(error);
}
