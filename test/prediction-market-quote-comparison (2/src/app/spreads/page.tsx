"use client";

import { useCallback, useEffect, useState } from "react";
import { Badge, Card, StatTile, money, pct, timeAgo } from "@/components/ui";
import type { SpreadRow } from "@/lib/queries";

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
}

export default function SpreadsHistoryPage() {
  const [rows, setRows] = useState<SpreadRow[]>([]);
  const [min, setMin] = useState(0);
  const [verdict, setVerdict] = useState("");
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    const params = new URLSearchParams({ limit: "500", min: String(min) });
    if (verdict) params.set("verdict", verdict);
    const json = (await fetch(`/api/spreads?${params.toString()}`, { cache: "no-store" }).then((r) => r.json())) as {
      spreads: SpreadRow[];
    };
    setRows(json.spreads ?? []);
    setLoading(false);
  }, [min, verdict]);

  useEffect(() => {
    void load();
  }, [load]);

  const values = rows.map((r) => r.spreadAfterFeesPct).filter((v) => v > 0);
  const byPair = new Map<string, number[]>();
  for (const row of rows) {
    const key = `${row.platformA} × ${row.platformB}`;
    byPair.set(key, [...(byPair.get(key) ?? []), row.spreadAfterFeesPct]);
  }

  return (
    <div className="space-y-5">
      <Card
        title="История вилок"
        subtitle="Зачем копить: без статистики по реальной частоте и размеру вилок после комиссий невозможно решить, стоит ли строить автотрейд."
        actions={
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <label className="flex items-center gap-1 text-slate-400">
              мин %
              <input
                type="number"
                step="0.1"
                value={min}
                onChange={(e) => setMin(Number(e.target.value))}
                className="w-16 rounded-md border border-slate-700 bg-slate-900 px-2 py-1"
              />
            </label>
            <select
              value={verdict}
              onChange={(e) => setVerdict(e.target.value)}
              className="rounded-md border border-slate-700 bg-slate-900 px-2 py-1"
            >
              <option value="">все вердикты</option>
              <option value="arb">только arb</option>
              <option value="phantom_oracle">фантом (оракул)</option>
              <option value="unsynced">несинхрон</option>
            </select>
            <button onClick={() => void load()} className="rounded-md border border-slate-700 px-2 py-1 text-slate-300">
              {loading ? "…" : "обновить"}
            </button>
          </div>
        }
      >
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <StatTile label="Строк в выборке" value={rows.length} />
          <StatTile label="Положительных" value={values.length} hint="спред > 0 после издержек" />
          <StatTile label="Медиана" value={`${median(values).toFixed(2)}%`} />
          <StatTile label="Максимум" value={values.length ? `${Math.max(...values).toFixed(2)}%` : "—"} />
          <StatTile
            label="Среднее по парам"
            value={
              byPair.size
                ? `${(values.reduce((a, b) => a + b, 0) / values.length).toFixed(2)}%`
                : "—"
            }
            hint={`${byPair.size} пар площадок`}
          />
        </div>

        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[900px] text-left text-[11px]">
            <thead className="tracking-wide text-slate-500 uppercase">
              <tr>
                <th className="py-2 pr-3">время</th>
                <th className="py-2 pr-3">A</th>
                <th className="py-2 pr-3">p(A)</th>
                <th className="py-2 pr-3">B</th>
                <th className="py-2 pr-3">p(B)</th>
                <th className="py-2 pr-3">событие</th>
                <th className="py-2 pr-3 text-right">спред</th>
                <th className="py-2 pr-3 text-right">ставки</th>
                <th className="py-2 pr-3">вердикт</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/70">
              {rows.length === 0 && (
                <tr>
                  <td colSpan={9} className="py-4 text-slate-500">
                    Пока ничего. Оставь дашборд с включённым авто-циклом — каждый замер выше порога логируется.
                  </td>
                </tr>
              )}
              {rows.map((r) => (
                <tr key={r.id}>
                  <td className="py-1.5 pr-3 text-slate-500">{timeAgo(r.detectedAt)}</td>
                  <td className="py-1.5 pr-3">{r.platformA}</td>
                  <td className="py-1.5 pr-3">{pct(r.probA, 1)}</td>
                  <td className="py-1.5 pr-3">{r.platformB}</td>
                  <td className="py-1.5 pr-3">{pct(r.probB, 1)}</td>
                  <td className="py-1.5 pr-3 max-w-[320px] truncate" title={r.title}>
                    {r.title}
                  </td>
                  <td className={`py-1.5 pr-3 text-right font-semibold ${r.spreadAfterFeesPct > 0 ? "text-emerald-300" : "text-slate-500"}`}>
                    {r.spreadAfterFeesPct.toFixed(2)}%
                  </td>
                  <td className="py-1.5 pr-3 text-right text-slate-400">
                    {money(r.stakeA)} / {money(r.stakeB)}
                  </td>
                  <td className="py-1.5 pr-3">
                    <Badge tone={r.verdict === "arb" ? "green" : r.verdict === "phantom_oracle" ? "amber" : "slate"}>
                      {r.verdict}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="Разбивка по парам площадок" subtitle="Где расхождения возникают чаще — там и стоит докручивать collector и матчинг.">
        <div className="grid gap-2 md:grid-cols-2 lg:grid-cols-3">
          {[...byPair.entries()].map(([pair, list]) => (
            <div key={pair} className="rounded-lg border border-slate-800 bg-slate-950/50 p-3 text-xs">
              <div className="text-slate-200">{pair}</div>
              <div className="mt-1 text-slate-400">
                замеров {list.length} · медиана {median(list).toFixed(2)}% · макс {Math.max(...list).toFixed(2)}%
              </div>
            </div>
          ))}
          {byPair.size === 0 && <p className="text-xs text-slate-500">Нет данных.</p>}
        </div>
      </Card>
    </div>
  );
}
