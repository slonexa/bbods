"use client";

import type { ReactNode } from "react";

export function Card({
  title,
  subtitle,
  actions,
  children,
  className = "",
}: {
  title?: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-xl border border-slate-800 bg-slate-900/60 p-4 shadow-lg ${className}`}>
      {(title || actions) && (
        <header className="mb-3 flex flex-wrap items-start justify-between gap-2">
          <div>
            {title && <h2 className="text-sm font-semibold tracking-wide text-slate-200 uppercase">{title}</h2>}
            {subtitle && <p className="mt-1 text-xs text-slate-400">{subtitle}</p>}
          </div>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

const TONES: Record<string, string> = {
  green: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  red: "bg-rose-500/15 text-rose-300 border-rose-500/30",
  amber: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  blue: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  slate: "bg-slate-700/40 text-slate-300 border-slate-600/40",
  violet: "bg-violet-500/15 text-violet-300 border-violet-500/30",
};

export function Badge({ children, tone = "slate" }: { children: ReactNode; tone?: keyof typeof TONES | string }) {
  return (
    <span className={`inline-flex items-center rounded-md border px-2 py-0.5 text-[11px] font-medium ${TONES[tone] ?? TONES.slate}`}>
      {children}
    </span>
  );
}

export function StatTile({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-3">
      <div className="text-[11px] tracking-wide text-slate-400 uppercase">{label}</div>
      <div className="mt-1 text-xl font-semibold text-slate-100">{value}</div>
      {hint && <div className="mt-1 text-[11px] text-slate-500">{hint}</div>}
    </div>
  );
}

export function Sparkline({
  values,
  width = 160,
  height = 36,
  stroke = "#38bdf8",
}: {
  values: number[];
  width?: number;
  height?: number;
  stroke?: string;
}) {
  if (values.length < 2) return <svg width={width} height={height} />;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const step = width / (values.length - 1);
  const points = values
    .map((v, i) => `${(i * step).toFixed(1)},${(height - ((v - min) / span) * (height - 4) - 2).toFixed(1)}`)
    .join(" ");
  return (
    <svg width={width} height={height} className="overflow-visible">
      <polyline points={points} fill="none" stroke={stroke} strokeWidth="1.5" />
    </svg>
  );
}

export function pct(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  return `${(n * 100).toFixed(digits)}%`;
}

export function money(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return "—";
  return `$${n.toFixed(2)}`;
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const diff = Date.now() - Date.parse(iso);
  if (!Number.isFinite(diff)) return "—";
  if (diff < 1000) return "только что";
  if (diff < 60000) return `${Math.round(diff / 1000)}с назад`;
  if (diff < 3600000) return `${Math.round(diff / 60000)}м назад`;
  return `${Math.round(diff / 3600000)}ч назад`;
}
