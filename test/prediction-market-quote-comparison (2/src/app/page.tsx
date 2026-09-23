"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Badge, Card, Sparkline, StatTile, money, pct, timeAgo } from "@/components/ui";
import type { CollectorStatus, OverviewStats, QuoteSnapshot, SpreadRow } from "@/lib/queries";

interface Overview {
  ok: boolean;
  settings: {
    alertThresholdPct: number;
    logThresholdPct: number;
    bankrollUsd: number;
    dashboardRefreshMs: number;
    allowSimulatedFallback: boolean;
    maxQuoteAgeDeltaMs: number;
  };
  collectors: CollectorStatus[];
  spreads: SpreadRow[];
  stats: OverviewStats;
  quotes: QuoteSnapshot[];
  spot: { BTC: Array<{ at: number; price: number; source: string }> };
  error?: string;
}

interface CycleSummary {
  durationMs: number;
  quotesWritten: number;
  spreadsFound: number;
  arbCount: number;
  bestSpreadPct: number | null;
  matched: { eventsCreated: number; matchesCreated: number; unmatched: number };
  lagFindings: number;
  collectors: Array<{ platform: string; status: string; items: number; simulated: boolean; error: string | null }>;
}

const VERDICT_TONE: Record<string, string> = {
  arb: "green",
  lag: "violet",
  phantom_oracle: "amber",
  unsynced: "red",
  none: "slate",
};

const VERDICT_LABEL: Record<string, string> = {
  arb: "вилка",
  lag: "лаг",
  phantom_oracle: "фантом (оракул)",
  unsynced: "несинхрон",
  none: "нет",
};

export default function DashboardPage() {
  const [data, setData] = useState<Overview | null>(null);
  const [summary, setSummary] = useState<CycleSummary | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [auto, setAuto] = useState(true);
  const [busy, setBusy] = useState(false);
  const [threshold, setThreshold] = useState(2);
  const [interval_, setIntervalMs] = useState(7000);
  const busyRef = useRef(false);

  const pushLog = useCallback((line: string) => {
    setLog((prev) => [`${new Date().toLocaleTimeString("ru-RU")} — ${line}`, ...prev].slice(0, 14));
  }, []);

  const tick = useCallback(
    async (force = false) => {
      if (busyRef.current) return;
      busyRef.current = true;
      setBusy(true);
      try {
        const res = await fetch("/api/cycle", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ force }),
        });
        const json = (await res.json()) as { ok: boolean; summary?: CycleSummary; error?: string };
        if (json.ok && json.summary) {
          setSummary(json.summary);
          pushLog(
            `цикл ${json.summary.durationMs}мс · котировок ${json.summary.quotesWritten} · вилок ${json.summary.arbCount} · лучших ${json.summary.bestSpreadPct ?? 0}%`,
          );
        } else {
          pushLog(`ошибка цикла: ${json.error ?? "неизвестно"}`);
        }
        const overviewRes = await fetch("/api/overview", { cache: "no-store" });
        const overview = (await overviewRes.json()) as Overview;
        setData(overview);
        setThreshold((current) => (current === 2 ? overview.settings.alertThresholdPct : current));
        setIntervalMs((current) => (current === 7000 ? overview.settings.dashboardRefreshMs : current));
      } catch (error) {
        pushLog(`сеть: ${error instanceof Error ? error.message : String(error)}`);
      } finally {
        busyRef.current = false;
        setBusy(false);
      }
    },
    [pushLog],
  );

  useEffect(() => {
    void tick();
  }, [tick]);

  useEffect(() => {
    if (!auto) return;
    const id = setInterval(() => void tick(), Math.max(3000, interval_));
    return () => clearInterval(id);
  }, [auto, interval_, tick]);

  async function saveSettings(patch: Record<string, number | boolean | string>) {
    await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    pushLog(`настройки сохранены: ${Object.entries(patch).map(([k, v]) => `${k}=${v}`).join(", ")}`);
    await fetch("/api/overview", { cache: "no-store" })
      .then((r) => r.json())
      .then((json: Overview) => setData(json));
  }

  async function runCollector(slug: string) {
    pushLog(`ручной запуск collector ${slug}…`);
    const res = await fetch("/api/collectors", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slug, force: true }),
    });
    const json = (await res.json()) as {
      ok: boolean;
      result?: { status: string; items: number; simulated: boolean; endpoint: string | null; error: string | null };
    };
    if (json.result) {
      pushLog(
        `${slug}: ${json.result.status}, распознано ${json.result.items}${json.result.simulated ? " (симулятор)" : ""}${
          json.result.endpoint ? ` · ${json.result.endpoint}` : ""
        }${json.result.error ? ` · ${json.result.error}` : ""}`,
      );
    }
    await tick();
  }

  const spreads = data?.spreads ?? [];
  const hot = spreads.filter((s) => s.spreadAfterFeesPct >= threshold);
  const rest = spreads.filter((s) => s.spreadAfterFeesPct < threshold).slice(0, 12);
  const spotSeries = data?.spot.BTC ?? [];

  return (
    <div className="space-y-5">
      <Card
        title="Цикл сбора"
        subtitle="Фронтенд сам дёргает /api/cycle: collectors → normalizer → engine → PostgreSQL. Троттлинг и backoff защищают от rate-limit."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={() => void tick(true)}
              disabled={busy}
              className="rounded-md bg-sky-500/90 px-3 py-1.5 text-xs font-semibold text-slate-950 hover:bg-sky-400 disabled:opacity-50"
            >
              {busy ? "сбор…" : "Запустить цикл"}
            </button>
            <label className="flex items-center gap-1 text-xs text-slate-400">
              <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} />
              авто
            </label>
            <select
              value={interval_}
              onChange={(e) => {
                const value = Number(e.target.value);
                setIntervalMs(value);
                void saveSettings({ dashboardRefreshMs: value });
              }}
              className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-xs"
            >
              {[5000, 7000, 10000, 15000, 30000].map((v) => (
                <option key={v} value={v}>
                  {v / 1000}s
                </option>
              ))}
            </select>
            <label className="flex items-center gap-1 text-xs text-slate-400">
              порог
              <input
                type="number"
                step="0.5"
                min="0"
                value={threshold}
                onChange={(e) => {
                  const value = Number(e.target.value);
                  setThreshold(value);
                  void saveSettings({ alertThresholdPct: value });
                }}
                className="w-16 rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-xs"
              />
              %
            </label>
          </div>
        }
      >
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
          <StatTile label="Котировок / час" value={data?.stats.quotesLastHour ?? "—"} />
          <StatTile label="Событий всего" value={data?.stats.eventsTotal ?? "—"} hint={`сматчено: ${data?.stats.matchedEvents ?? 0}`} />
          <StatTile label="Записей истории" value={data?.stats.historyRows ?? "—"} hint="сырые тики для бэктестов" />
          <StatTile label="Вилок за 24ч" value={data?.stats.arbLast24h ?? "—"} hint={`всего строк: ${data?.stats.spreadsLast24h ?? 0}`} />
          <StatTile
            label="Лучший спред 24ч"
            value={data?.stats.bestSpread24h != null ? `${data.stats.bestSpread24h.toFixed(2)}%` : "—"}
            hint="после комиссий и слиппеджа"
          />
          <StatTile
            label="BTC спот"
            value={spotSeries.length ? spotSeries[spotSeries.length - 1].price.toFixed(1) : "—"}
            hint={spotSeries.length ? `источник: ${spotSeries[spotSeries.length - 1].source}` : "нет данных"}
          />
        </div>
        {spotSeries.length > 2 && (
          <div className="mt-3 flex items-center gap-3">
            <span className="text-[11px] text-slate-500">тик спота (эталон для лаборатории лага)</span>
            <Sparkline values={spotSeries.map((p) => p.price)} width={420} height={40} />
          </div>
        )}
      </Card>

      <Card title="Collectors" subtitle="По одному модулю на площадку. Live-эндпоинт ищется через DevTools → /probe (см. страницу матчинга).">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {(data?.collectors ?? []).map((c) => (
            <div key={c.slug} className="rounded-lg border border-slate-800 bg-slate-950/50 p-3">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="text-sm font-semibold text-slate-100">{c.name}</div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    <Badge tone={c.lastRun?.status === "ok" ? "green" : c.lastRun ? "red" : "slate"}>
                      {c.lastRun?.status ?? "нет запусков"}
                    </Badge>
                    <Badge tone={c.defaultMarketType === "fixed_odds" ? "blue" : "violet"}>{c.defaultMarketType}</Badge>
                    <Badge tone={c.lastRun?.error || !c.lastRun ? "amber" : "slate"}>
                      fee {c.feeBps}bps · slip {c.slippageBps}bps
                    </Badge>
                  </div>
                </div>
                <button
                  onClick={() => void runCollector(c.slug)}
                  className="rounded-md border border-slate-700 px-2 py-1 text-[11px] text-slate-300 hover:border-sky-500 hover:text-sky-300"
                >
                  run
                </button>
              </div>
              <dl className="mt-2 space-y-0.5 text-[11px] text-slate-400">
                <div>
                  последний запуск: {timeAgo(c.lastRun?.at)} · items {c.lastRun?.items ?? 0}
                  {c.lastRun?.durationMs != null ? ` · ${c.lastRun.durationMs}мс` : ""}
                </div>
                <div>интервал опроса: {(c.minIntervalMs / 1000).toFixed(0)}s · endpoint: {c.candidates[0] ?? "—"}</div>
                <div>резолюция: {c.resolutionSource ?? "неизвестно"}</div>
              </dl>
              {c.lastRun?.error && <p className="mt-2 text-[11px] text-amber-300">{c.lastRun.error}</p>}
            </div>
          ))}
        </div>
      </Card>

      <Card
        title={`Топлайн: спред после комиссий (порог ${threshold}%)`}
        subtitle="Сортировка по убыванию. Жёлтый вердикт = расхождение ценовых фидов/оракула, а не чистая вилка."
      >
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1000px] text-left text-xs">
            <thead className="text-[11px] tracking-wide text-slate-500 uppercase">
              <tr>
                <th className="py-2 pr-3">Площадка A</th>
                <th className="py-2 pr-3">p(A)</th>
                <th className="py-2 pr-3">Площадка B</th>
                <th className="py-2 pr-3">p(B)</th>
                <th className="py-2 pr-3">Событие</th>
                <th className="py-2 pr-3">Типы</th>
                <th className="py-2 pr-3 text-right">Сырой эдж</th>
                <th className="py-2 pr-3 text-right">Издержки</th>
                <th className="py-2 pr-3 text-right">Спред</th>
                <th className="py-2 pr-3 text-right">Ставки / выплата</th>
                <th className="py-2 pr-3">Вердикт</th>
                <th className="py-2 pr-3">Найдено</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/70">
              {hot.length === 0 && (
                <tr>
                  <td colSpan={12} className="py-4 text-slate-500">
                    Нет вилок выше порога. Это нормально: в новом сегменте их мало, счёт идёт на единицы в день.
                    История всё равно пишется — см. <Link className="text-sky-400" href="/spreads">историю</Link>.
                  </td>
                </tr>
              )}
              {hot.map((s) => (
                <tr key={s.id} className="align-top hover:bg-slate-900/60">
                  <td className="py-2 pr-3 font-medium text-slate-200">
                    {s.platformA}
                    <div className="text-[11px] text-slate-500">{s.sideA} @ {s.oddsA ? s.oddsA.toFixed(3) : "—"}</div>
                  </td>
                  <td className="py-2 pr-3">{pct(s.probA, 1)}</td>
                  <td className="py-2 pr-3 font-medium text-slate-200">
                    {s.platformB}
                    <div className="text-[11px] text-slate-500">{s.sideB} @ {s.oddsB ? s.oddsB.toFixed(3) : "—"}</div>
                  </td>
                  <td className="py-2 pr-3">{pct(s.probB, 1)}</td>
                  <td className="py-2 pr-3 max-w-[280px]">
                    <div className="truncate text-slate-300" title={s.title}>{s.title}</div>
                    <div className="truncate text-[11px] text-slate-500" title={s.eventKey}>{s.eventKey}</div>
                  </td>
                  <td className="py-2 pr-3 text-[11px] text-slate-400">
                    {s.marketTypeA} × {s.marketTypeB}
                  </td>
                  <td className="py-2 pr-3 text-right">{pct(s.rawEdge, 2)}</td>
                  <td className="py-2 pr-3 text-right text-amber-300">{s.feesBps} bps</td>
                  <td className="py-2 pr-3 text-right font-semibold text-emerald-300">{s.spreadAfterFeesPct.toFixed(2)}%</td>
                  <td className="py-2 pr-3 text-right text-[11px] text-slate-400">
                    {money(s.stakeA)} / {money(s.stakeB)}
                    <div>выплата {money(s.payout)}</div>
                  </td>
                  <td className="py-2 pr-3">
                    <Badge tone={VERDICT_TONE[s.verdict] ?? "slate"}>{VERDICT_LABEL[s.verdict] ?? s.verdict}</Badge>
                    {s.warnings.length > 0 && (
                      <ul className="mt-1 max-w-[240px] space-y-0.5 text-[10px] text-amber-300/80">
                        {s.warnings.map((w) => (
                          <li key={w}>· {w}</li>
                        ))}
                      </ul>
                    )}
                  </td>
                  <td className="py-2 pr-3 text-[11px] text-slate-500">{timeAgo(s.detectedAt)}</td>
                </tr>
              ))}
              {rest.map((s) => (
                <tr key={s.id} className="align-top text-slate-500 hover:bg-slate-900/40">
                  <td className="py-1.5 pr-3">{s.platformA}</td>
                  <td className="py-1.5 pr-3">{pct(s.probA, 1)}</td>
                  <td className="py-1.5 pr-3">{s.platformB}</td>
                  <td className="py-1.5 pr-3">{pct(s.probB, 1)}</td>
                  <td className="py-1.5 pr-3 max-w-[280px] truncate">{s.title}</td>
                  <td className="py-1.5 pr-3 text-[11px]">{s.marketTypeA} × {s.marketTypeB}</td>
                  <td className="py-1.5 pr-3 text-right">{pct(s.rawEdge, 2)}</td>
                  <td className="py-1.5 pr-3 text-right">{s.feesBps} bps</td>
                  <td className="py-1.5 pr-3 text-right">{s.spreadAfterFeesPct.toFixed(2)}%</td>
                  <td className="py-1.5 pr-3 text-right text-[11px]">{money(s.stakeA)} / {money(s.stakeB)}</td>
                  <td className="py-1.5 pr-3">{s.verdict}</td>
                  <td className="py-1.5 pr-3 text-[11px]">{timeAgo(s.detectedAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Лог циклов" subtitle="Что происходило при каждом опросе (collector статусы, распознанные рынки).">
          <ul className="space-y-1 font-mono text-[11px] text-slate-400">
            {log.length === 0 && <li className="text-slate-600">пока пусто</li>}
            {log.map((line, i) => (
              <li key={`${line}-${i}`} className="truncate">
                {line}
              </li>
            ))}
          </ul>
          {summary && (
            <p className="mt-3 text-[11px] text-slate-500">
              последний цикл: {summary.quotesWritten} котировок · новых событий {summary.matched.eventsCreated} ·
              связок {summary.matched.matchesCreated} · без матчинга {summary.matched.unmatched} · лаг-сэмплов{" "}
              {summary.lagFindings}
            </p>
          )}
        </Card>

        <Card title="Последние котировки" subtitle="Снимок унифицированного формата collector'а (platform, market_id, outcome, prob).">
          <div className="max-h-72 overflow-auto">
            <table className="w-full text-left text-[11px]">
              <thead className="text-slate-500 uppercase">
                <tr>
                  <th className="py-1 pr-2">platform</th>
                  <th className="py-1 pr-2">market_id</th>
                  <th className="py-1 pr-2">outcome</th>
                  <th className="py-1 pr-2">type</th>
                  <th className="py-1 pr-2 text-right">p / O</th>
                  <th className="py-1 pr-2">age</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {(data?.quotes ?? []).slice(0, 40).map((q) => (
                  <tr key={`${q.platform}-${q.marketId}-${q.outcome}`}>
                    <td className="py-1 pr-2 text-slate-300">{q.platform}</td>
                    <td className="py-1 pr-2 truncate font-mono text-slate-500" title={q.marketId}>
                      {q.marketId}
                    </td>
                    <td className="py-1 pr-2">{q.outcome}</td>
                    <td className="py-1 pr-2 text-slate-500">{q.marketType}</td>
                    <td className="py-1 pr-2 text-right">
                      {(q.probability * 100).toFixed(1)}%{q.odds ? ` / ${q.odds.toFixed(2)}` : ""}
                    </td>
                    <td className="py-1 pr-2 text-slate-500">{timeAgo(q.fetchedAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </div>
  );
}
