"use client";

import { useCallback, useEffect, useState } from "react";
import { Badge, Card, Sparkline, StatTile } from "@/components/ui";

interface LagResponse {
  ok: boolean;
  summary: {
    samples: number;
    medianLagMs: number | null;
    p90LagMs: number | null;
    shareOver1s: number;
    shareOver3s: number;
    conclusion: string;
  };
  samples: Array<{
    platform: string;
    marketId: string;
    outcome: string;
    spotBefore: number | null;
    spotAfter: number | null;
    spotMovePct: number | null;
    probBefore: number;
    probAfter: number;
    lagMs: number | null;
    sampledAt: string;
  }>;
}

export default function LagPage() {
  const [data, setData] = useState<LagResponse | null>(null);
  const [spot, setSpot] = useState<Array<{ at: number; price: number }>>([]);

  const load = useCallback(async () => {
    const [lag, overview] = await Promise.all([
      fetch("/api/lag", { cache: "no-store" }).then((r) => r.json() as Promise<LagResponse>),
      fetch("/api/overview", { cache: "no-store" }).then(
        (r) => r.json() as Promise<{ spot: { BTC: Array<{ at: number; price: number }> } }>,
      ),
    ]);
    setData(lag);
    setSpot(overview.spot?.BTC ?? []);
  }, []);

  useEffect(() => {
    void load();
    const id = setInterval(() => void load(), 10000);
    return () => clearInterval(id);
  }, [load]);

  const summary = data?.summary;

  return (
    <div className="space-y-5">
      <Card
        title="Лаборатория лага коэффициентов"
        subtitle="Раздел 4.4 ТЗ: прежде чем строить низколатентную инфраструктуру, дешёвый эксперимент — логировать, как быстро площадка обновляет коэффициент после движения спота."
      >
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <StatTile label="Сэмплов" value={summary?.samples ?? 0} />
          <StatTile label="Медиана лага" value={summary?.medianLagMs != null ? `${summary.medianLagMs} мс` : "—"} />
          <StatTile label="P90 лага" value={summary?.p90LagMs != null ? `${summary.p90LagMs} мс` : "—"} />
          <StatTile label="Лаг > 1s" value={summary ? `${summary.shareOver1s}%` : "—"} />
          <StatTile label="Лаг > 3s" value={summary ? `${summary.shareOver3s}%` : "—"} />
        </div>
        <p className="mt-3 text-sm text-amber-300">{summary?.conclusion}</p>
        {spot.length > 2 && (
          <div className="mt-3 flex items-center gap-3">
            <span className="text-[11px] text-slate-500">эталонный ряд спота ({spot.length} тиков)</span>
            <Sparkline values={spot.map((p) => p.price)} width={480} height={44} stroke="#34d399" />
          </div>
        )}
        <div className="mt-4 space-y-1 rounded-lg border border-slate-800 bg-slate-950/60 p-3 text-[11px] text-slate-400">
          <p>
            Метод: в ряде тиков ищем момент, когда спот сдвинулся сильнее порога (0.02%), а коэффициент не изменился.
            Следующее изменение коэффициента даёт оценку лага = t(обновления) − t(движения спота).
          </p>
          <p className="text-slate-500">
            Поллинг раз в 5–15с для ловли лага бесполезен: если P90 лага &gt; 1–3с — только тогда оправдан WebSocket
            и отдельный low-latency модуль. Если лаг суб-секундный — паттерна нет, экономим недели работы.
          </p>
        </div>
      </Card>

      <Card title="Последние сэмплы" subtitle="Каждая строка: движение спота и последовавшее (или нет) обновление коэффициента.">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px] text-left text-[11px]">
            <thead className="tracking-wide text-slate-500 uppercase">
              <tr>
                <th className="py-2 pr-3">platform</th>
                <th className="py-2 pr-3">market</th>
                <th className="py-2 pr-3">outcome</th>
                <th className="py-2 pr-3 text-right">спот до → после</th>
                <th className="py-2 pr-3 text-right">Δ спот</th>
                <th className="py-2 pr-3 text-right">p до → после</th>
                <th className="py-2 pr-3 text-right">лаг</th>
                <th className="py-2 pr-3">время</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/70">
              {(data?.samples ?? []).length === 0 && (
                <tr>
                  <td colSpan={8} className="py-4 text-slate-500">
                    Сэмплов нет. Нужно 1–2 часа непрерывного сбора — оставь дашборд открытым (авто-цикл).
                  </td>
                </tr>
              )}
              {(data?.samples ?? []).map((s, i) => (
                <tr key={`${s.marketId}-${s.sampledAt}-${i}`}>
                  <td className="py-1.5 pr-3 text-slate-300">{s.platform}</td>
                  <td className="py-1.5 pr-3 truncate font-mono text-slate-500" title={s.marketId}>
                    {s.marketId}
                  </td>
                  <td className="py-1.5 pr-3">{s.outcome}</td>
                  <td className="py-1.5 pr-3 text-right">
                    {s.spotBefore?.toFixed(1) ?? "—"} → {s.spotAfter?.toFixed(1) ?? "—"}
                  </td>
                  <td className={`py-1.5 pr-3 text-right ${(s.spotMovePct ?? 0) >= 0 ? "text-emerald-300" : "text-rose-300"}`}>
                    {s.spotMovePct != null ? `${s.spotMovePct.toFixed(3)}%` : "—"}
                  </td>
                  <td className="py-1.5 pr-3 text-right">
                    {(s.probBefore * 100).toFixed(1)}% → {(s.probAfter * 100).toFixed(1)}%
                  </td>
                  <td className="py-1.5 pr-3 text-right">
                    {s.lagMs != null ? (
                      <Badge tone={s.lagMs > 3000 ? "green" : s.lagMs > 1000 ? "amber" : "slate"}>{s.lagMs} мс</Badge>
                    ) : (
                      <span className="text-slate-600">нет движения</span>
                    )}
                  </td>
                  <td className="py-1.5 pr-3 text-slate-500">{new Date(s.sampledAt).toLocaleTimeString("ru-RU")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
