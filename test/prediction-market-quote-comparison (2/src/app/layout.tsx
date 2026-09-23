import type { Metadata } from "next";
import type { ReactNode } from "react";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Prediction Market Arb Scanner — Bybit / Polymarket / MEXC",
  description:
    "Skeleton MVP: collectors → normalizer → engine → dashboard для сравнения котировок prediction markets и CEX-контрактов.",
};

const NAV = [
  { href: "/", label: "Дашборд" },
  { href: "/events", label: "Матчинг событий" },
  { href: "/lag", label: "Лаг коэффициентов" },
  { href: "/spreads", label: "История вилок" },
  { href: "/playbook", label: "Плейбук / советы" },
];

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ru">
      <body className="min-h-screen bg-slate-950 text-slate-200 antialiased">
        <header className="sticky top-0 z-20 border-b border-slate-800 bg-slate-950/90 backdrop-blur">
          <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
            <Link href="/" className="text-sm font-semibold text-slate-100">
              arb-scanner <span className="text-slate-500">/ MVP</span>
            </Link>
            <nav className="flex flex-wrap gap-4 text-xs text-slate-400">
              {NAV.map((item) => (
                <Link key={item.href} href={item.href} className="transition hover:text-sky-300">
                  {item.label}
                </Link>
              ))}
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-7xl px-4 py-6">{children}</main>
        <footer className="mx-auto max-w-7xl px-4 pb-10 text-[11px] text-slate-600">
          Скелет для сравнения котировок: Polymarket (публичный Gamma API) + CEX-контракты (Bybit Odds / MEXC / OKX /
          Gate) через внутренние эндпоинты. Не автотрейд: сначала собрать статистику по реальной частоте вилок.
        </footer>
      </body>
    </html>
  );
}
