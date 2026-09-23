"use client";

import { useCallback, useEffect, useState } from "react";
import { Badge, Card, pct, timeAgo } from "@/components/ui";
import type { QuoteSnapshot } from "@/lib/queries";

interface EventRow {
  eventKey: string;
  title: string;
  asset: string | null;
  kind: string;
  strike: number | null;
  expiry: string | null;
  windowStart: string | null;
  matchSource: string;
  resolutionSource: string | null;
  legs: Array<{
    platform: string;
    marketId: string;
    outcome: string;
    source: string;
    probability: number | null;
    odds: number | null;
    fetchedAt: string | null;
  }>;
}

interface ProbeResult {
  ok: boolean;
  status?: number;
  byteLength?: number;
  arrays?: Array<{ path: string; items: number; sampleKeys: string[] }>;
  marketCandidates?: number;
  rawKeys?: string[];
  mappedCount?: number;
  mappedSample?: Array<Record<string, unknown>>;
  hints?: Array<string | null>;
  error?: string;
}

export default function EventsPage() {
  const [events, setEvents] = useState<EventRow[]>([]);
  const [unmatched, setUnmatched] = useState<QuoteSnapshot[]>([]);
  const [platforms, setPlatforms] = useState<string[]>([]);
  const [target, setTarget] = useState<QuoteSnapshot | null>(null);
  const [eventKey, setEventKey] = useState("");
  const [newTitle, setNewTitle] = useState("");
  const [probeUrl, setProbeUrl] = useState("");
  const [probePlatform, setProbePlatform] = useState("bybit_odds");
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [probing, setProbing] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [eventsRes, colRes] = await Promise.all([
      fetch("/api/events", { cache: "no-store" }).then((r) => r.json()),
      fetch("/api/collectors", { cache: "no-store" }).then((r) => r.json()),
    ]);
    setEvents((eventsRes as { events: EventRow[] }).events ?? []);
    setUnmatched((eventsRes as { unmatched: QuoteSnapshot[] }).unmatched ?? []);
    setPlatforms((colRes as { collectors: Array<{ slug: string }> }).collectors.map((c) => c.slug));
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function bindManual() {
    if (!target) return;
    const res = await fetch("/api/events", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        platform: target.platform,
        marketId: target.marketId,
        outcome: target.outcome,
        eventKey: eventKey || undefined,
        newEventTitle: eventKey ? undefined : newTitle,
      }),
    });
    const json = (await res.json()) as { ok: boolean; eventKey?: string; error?: string };
    setMessage(json.ok ? `привязано к ${json.eventKey}` : `ошибка: ${json.error}`);
    setTarget(null);
    setEventKey("");
    setNewTitle("");
    await load();
  }

  async function runProbe() {
    setProbing(true);
    setProbe(null);
    try {
      const res = await fetch("/api/probe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: probeUrl, platform: probePlatform }),
      });
      setProbe((await res.json()) as ProbeResult);
    } finally {
      setProbing(false);
    }
  }

  return (
    <div className="space-y-5">
      <Card
        title="Помощник реверса эндпоинта"
        subtitle="DevTools → Network → скопировать URL XHR/WS-запроса → вставить сюда. Сервер повторит запрос с браузерными заголовками и покажет, что удалось распознать."
      >
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={probePlatform}
            onChange={(e) => setProbePlatform(e.target.value)}
            className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1.5 text-xs"
          >
            {platforms.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
          <input
            value={probeUrl}
            onChange={(e) => setProbeUrl(e.target.value)}
            placeholder="https://... внутренний эндпоинт с ценами"
            className="min-w-[380px] flex-1 rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-xs"
          />
          <button
            onClick={() => void runProbe()}
            disabled={probing || !probeUrl}
            className="rounded-md bg-sky-500/90 px-3 py-1.5 text-xs font-semibold text-slate-950 hover:bg-sky-400 disabled:opacity-50"
          >
            {probing ? "проверяю…" : "Проверить"}
          </button>
        </div>

        {probe && (
          <div className="mt-4 space-y-3 text-xs">
            {!probe.ok && <p className="text-rose-300">Ошибка: {probe.error}</p>}
            {probe.ok && (
              <>
                <div className="flex flex-wrap gap-2">
                  <Badge tone={probe.mappedCount ? "green" : "amber"}>HTTP {probe.status}</Badge>
                  <Badge tone="slate">{probe.byteLength} байт</Badge>
                  <Badge tone={probe.marketCandidates ? "green" : "red"}>
                    похоже на рынки: {probe.marketCandidates ?? 0}
                  </Badge>
                  <Badge tone={probe.mappedCount ? "green" : "amber"}>распознано: {probe.mappedCount ?? 0}</Badge>
                </div>
                <div>
                  <div className="mb-1 text-slate-400">Массивы объектов в ответе:</div>
                  <ul className="space-y-1 font-mono text-[11px] text-slate-300">
                    {(probe.arrays ?? []).map((a) => (
                      <li key={a.path}>
                        {a.path} · {a.items} шт · ключи: {a.sampleKeys.join(", ")}
                      </li>
                    ))}
                  </ul>
                </div>
                {probe.rawKeys && probe.rawKeys.length > 0 && (
                  <div>
                    <div className="mb-1 text-slate-400">Ключи первого объекта-рынка (что мапить в cexOdds.ts):</div>
                    <code className="block rounded bg-slate-950 p-2 font-mono text-[11px] text-emerald-300">
                      {probe.rawKeys.join(" | ")}
                    </code>
                  </div>
                )}
                {(probe.hints ?? []).filter(Boolean).map((hint) => (
                  <p key={String(hint)} className="text-amber-300">
                    → {hint}
                  </p>
                ))}
                {(probe.mappedSample ?? []).length > 0 && (
                  <pre className="max-h-56 overflow-auto rounded bg-slate-950 p-2 font-mono text-[11px] text-slate-300">
                    {JSON.stringify(probe.mappedSample, null, 2)}
                  </pre>
                )}
              </>
            )}
          </div>
        )}
      </Card>

      <Card
        title="События и их ноги"
        subtitle="Крипта матчится программно (актив + тип + страйк + окно). Спорт/событийные — только вручную, как event_map.json."
      >
        <div className="space-y-2">
          {events.length === 0 && <p className="text-xs text-slate-500">Событий пока нет — запусти цикл на дашборде.</p>}
          {events.map((e) => (
            <div key={e.eventKey} className="rounded-lg border border-slate-800 bg-slate-950/50 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate text-sm text-slate-200">{e.title}</div>
                  <div className="truncate font-mono text-[11px] text-slate-500">{e.eventKey}</div>
                </div>
                <div className="flex flex-wrap items-center gap-1">
                  {new Set(e.legs.map((l) => l.platform)).size >= 2 ? (
                    <Badge tone="green">есть пара</Badge>
                  ) : (
                    <Badge tone="slate">нет пары</Badge>
                  )}
                  <Badge tone={e.kind === "updown" ? "blue" : e.kind === "target" ? "violet" : "slate"}>{e.kind}</Badge>
                  <Badge tone={e.matchSource === "manual" ? "amber" : "slate"}>{e.matchSource}</Badge>
                  {e.expiry && <Badge tone="slate">{new Date(e.expiry).toLocaleString("ru-RU")}</Badge>}
                </div>
              </div>
              <div className="mt-2 grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
                {e.legs.map((leg) => (
                  <div key={`${leg.platform}-${leg.marketId}-${leg.outcome}`} className="rounded border border-slate-800 px-2 py-1 text-[11px]">
                    <span className="text-slate-300">{leg.platform}</span> · {leg.outcome} ·{" "}
                    {leg.probability != null ? pct(leg.probability, 1) : "—"}
                    {leg.odds != null ? ` @ ${leg.odds.toFixed(2)}` : ""}
                    <div className="text-slate-500">
                      {leg.source} · {timeAgo(leg.fetchedAt)}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Card
        title="Несматченные рынки"
        subtitle="Выбери рынок, затем существующее событие или введи название нового — привязка сохранится в БД как ручная."
      >
        {message && <p className="mb-3 text-xs text-emerald-300">{message}</p>}
        <div className="grid gap-2 lg:grid-cols-2">
          <div className="max-h-80 overflow-auto rounded-lg border border-slate-800">
            <table className="w-full text-left text-[11px]">
              <tbody className="divide-y divide-slate-800/60">
                {unmatched.length === 0 && (
                  <tr>
                    <td className="p-3 text-slate-500">Все распознанные рынки имеют событие.</td>
                  </tr>
                )}
                {unmatched.map((q) => (
                  <tr
                    key={`${q.platform}-${q.marketId}-${q.outcome}`}
                    onClick={() => setTarget(q)}
                    className={`cursor-pointer hover:bg-slate-900/70 ${target?.marketId === q.marketId && target?.platform === q.platform ? "bg-sky-500/10" : ""}`}
                  >
                    <td className="p-2">
                      <div className="text-slate-300">
                        {q.platform} · {q.outcome} · {pct(q.probability, 1)}
                      </div>
                      <div className="truncate text-slate-500">{q.title}</div>
                      <div className="truncate font-mono text-[10px] text-slate-600">{q.marketId}</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="rounded-lg border border-slate-800 p-3 text-xs">
            {!target ? (
              <p className="text-slate-500">Слева выбери рынок для ручной привязки.</p>
            ) : (
              <div className="space-y-3">
                <div>
                  <div className="text-slate-300">
                    {target.platform} · {target.outcome}
                  </div>
                  <div className="text-slate-500">{target.title}</div>
                  <code className="mt-1 block truncate font-mono text-[10px] text-slate-600">{target.marketId}</code>
                </div>
                <label className="block">
                  <span className="text-slate-400">Привязать к существующему event_key</span>
                  <select
                    value={eventKey}
                    onChange={(e) => setEventKey(e.target.value)}
                    className="mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1.5"
                  >
                    <option value="">— новое событие —</option>
                    {events.map((e) => (
                      <option key={e.eventKey} value={e.eventKey}>
                        {e.title}
                      </option>
                    ))}
                  </select>
                </label>
                {!eventKey && (
                  <label className="block">
                    <span className="text-slate-400">Название нового события</span>
                    <input
                      value={newTitle}
                      onChange={(e) => setNewTitle(e.target.value)}
                      placeholder="BTC above $100k on 31 Dec 2026"
                      className="mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1.5"
                    />
                  </label>
                )}
                <button
                  onClick={() => void bindManual()}
                  className="rounded-md bg-emerald-500/90 px-3 py-1.5 font-semibold text-slate-950 hover:bg-emerald-400"
                >
                  Привязать вручную
                </button>
              </div>
            )}
          </div>
        </div>
      </Card>
    </div>
  );
}
