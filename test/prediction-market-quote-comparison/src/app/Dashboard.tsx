"use client";

import { useEffect, useMemo, useState } from "react";

interface SpreadRow {
  id: number;
  eventKey: string;
  assetOrTopic: string;
  contractType: string;
  platformA: string;
  marketIdA: string;
  marketTypeA: string;
  outcomeA: string;
  probA: number;
  platformB: string;
  marketIdB: string;
  marketTypeB: string;
  outcomeB: string;
  probB: number;
  edgeRaw: number;
  feesAndSlippage: number;
  spreadAfterFees: number;
  timeframeMismatch: boolean;
  isSimulatedPair: boolean;
  suspicious: boolean;
  note: string | null;
  detectedAt: string;
}

interface QuoteRow {
  id: number;
  platform: string;
  marketId: string;
  rawTitle: string;
  outcome: string;
  impliedProbability: number;
  marketType: string;
  expiry: string | null;
  isSimulated: boolean;
  fetchedAt: string;
  meta: Record<string, unknown>;
}

interface StatusCollector {
  platform: string;
  ok: boolean;
  error?: string;
  quoteCount: number;
  durationMs: number;
  isSimulated: boolean;
}

interface CycleStatus {
  startedAt: string;
  finishedAt: string;
  durationMs: number;
  collectors: StatusCollector[];
  quoteCount: number;
  groupCount: number;
  spreadCount: number;
  actionableSpreadCount: number;
  error?: string;
}

interface StatusResponse {
  latest: CycleStatus | null;
  config: {
    pollIntervalMs: number;
    defaultAlertThreshold: number;
    fees: Record<string, number>;
    simulate: Record<string, boolean>;
  };
}

const PLATFORM_LABELS: Record<string, string> = {
  polymarket: "Polymarket",
  bybit_odds: "Bybit Odds",
  mexc_prediction: "MEXC Prediction",
};

function pct(n: number): string {
  return `${(n * 100).toFixed(2)}%`;
}

function timeAgo(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime();
  const s = Math.max(0, Math.round(diffMs / 1000));
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  return `${m}m ago`;
}

export default function Dashboard() {
  const [spreads, setSpreads] = useState<SpreadRow[]>([]);
  const [quotes, setQuotes] = useState<QuoteRow[]>([]);
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [threshold, setThreshold] = useState<number>(0.0);
  const [showAllSpreads, setShowAllSpreads] = useState(false);
  const [showQuotes, setShowQuotes] = useState(false);
  const [loading, setLoading] = useState(true);
  const [triggering, setTriggering] = useState(false);

  async function refresh() {
    try {
      const [spreadsRes, statusRes] = await Promise.all([
        fetch("/api/spreads?limit=200", { cache: "no-store" }),
        fetch("/api/status", { cache: "no-store" }),
      ]);
      const spreadsJson = await spreadsRes.json();
      const statusJson = await statusRes.json();
      setSpreads(spreadsJson.spreads ?? []);
      setStatus(statusJson);
      if (statusJson?.config?.defaultAlertThreshold !== undefined && threshold === 0) {
        setThreshold(statusJson.config.defaultAlertThreshold);
      }
    } catch {
      // transient fetch error, next tick will retry
    } finally {
      setLoading(false);
    }
  }

  async function refreshQuotes() {
    try {
      const res = await fetch("/api/quotes", { cache: "no-store" });
      const json = await res.json();
      setQuotes(json.quotes ?? []);
    } catch {
      // ignore
    }
  }

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!showQuotes) return;
    refreshQuotes();
    const id = setInterval(refreshQuotes, 5000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showQuotes]);

  async function triggerCollect() {
    setTriggering(true);
    try {
      await fetch("/api/collect", { method: "POST" });
      await refresh();
    } finally {
      setTriggering(false);
    }
  }

  const filteredSpreads = useMemo(
    () => (showAllSpreads ? spreads : spreads.filter((s) => s.spreadAfterFees >= threshold)),
    [spreads, showAllSpreads, threshold],
  );

  const collectorMap = new Map((status?.latest?.collectors ?? []).map((c) => [c.platform, c]));

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100">
      <div className="mx-auto max-w-6xl px-6 py-10">
        <header className="mb-8 flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-xs uppercase tracking-[0.2em] text-emerald-400">Prediction Market Arb Scanner</p>
            <h1 className="mt-2 text-3xl font-semibold text-white">Bybit / Polymarket / MEXC spread monitor</h1>
            <p className="mt-2 max-w-2xl text-sm text-slate-400">
              Collectors → normalizer → engine → dashboard, auto-refreshing every 5s. Polymarket data is live via
              their public Gamma API. Bybit Odds and MEXC Prediction have no confirmed public endpoint yet, so
              they run in <span className="font-semibold text-amber-400">SIMULATED</span> mode — see code comments
              in <code className="rounded bg-slate-800 px-1">src/collectors/</code> for exactly where to plug in
              real scraped endpoints.
            </p>
          </div>
          <button
            onClick={triggerCollect}
            disabled={triggering}
            className="shrink-0 rounded-full bg-emerald-500 px-5 py-2 text-sm font-semibold text-slate-950 transition hover:bg-emerald-400 disabled:opacity-50"
          >
            {triggering ? "Collecting…" : "Run cycle now"}
          </button>
        </header>

        <section className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-3">
          {["polymarket", "bybit_odds", "mexc_prediction"].map((platform) => {
            const c = collectorMap.get(platform);
            return (
              <div key={platform} className="rounded-2xl border border-slate-800 bg-slate-900 p-4">
                <div className="flex items-center justify-between">
                  <h3 className="font-semibold text-white">{PLATFORM_LABELS[platform] ?? platform}</h3>
                  {c?.isSimulated ? (
                    <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-400">
                      simulated
                    </span>
                  ) : (
                    <span className="rounded-full bg-emerald-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-400">
                      live api
                    </span>
                  )}
                </div>
                {c ? (
                  <div className="mt-3 space-y-1 text-sm text-slate-400">
                    <p>
                      Status:{" "}
                      <span className={c.ok ? "text-emerald-400" : "text-red-400"}>{c.ok ? "OK" : "ERROR"}</span>
                    </p>
                    <p>Quotes last cycle: {c.quoteCount}</p>
                    <p>Fetch time: {c.durationMs}ms</p>
                    {c.error && <p className="truncate text-red-400" title={c.error}>{c.error}</p>}
                  </div>
                ) : (
                  <p className="mt-3 text-sm text-slate-500">Waiting for first cycle…</p>
                )}
              </div>
            );
          })}
        </section>

        {status?.latest && (
          <p className="mb-6 text-xs text-slate-500">
            Last cycle finished {timeAgo(status.latest.finishedAt)} · {status.latest.quoteCount} quotes ·{" "}
            {status.latest.groupCount} matched event groups · {status.latest.spreadCount} spread comparisons ·{" "}
            {status.latest.actionableSpreadCount} above default threshold · poll interval{" "}
            {(status.config.pollIntervalMs / 1000).toFixed(0)}s
            {status.latest.error && <span className="text-red-400"> · cycle error: {status.latest.error}</span>}
          </p>
        )}

        <section className="mb-4 flex flex-wrap items-center gap-4 rounded-2xl border border-slate-800 bg-slate-900 p-4">
          <label className="flex items-center gap-2 text-sm text-slate-300">
            Alert threshold
            <input
              type="number"
              step="0.005"
              value={threshold}
              onChange={(e) => setThreshold(Number(e.target.value))}
              className="w-24 rounded-lg border border-slate-700 bg-slate-950 px-2 py-1 text-slate-100"
            />
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-300">
            <input type="checkbox" checked={showAllSpreads} onChange={(e) => setShowAllSpreads(e.target.checked)} />
            Show everything (ignore threshold)
          </label>
          <label className="ml-auto flex items-center gap-2 text-sm text-slate-300">
            <input type="checkbox" checked={showQuotes} onChange={(e) => setShowQuotes(e.target.checked)} />
            Show raw quotes panel
          </label>
        </section>

        <section className="overflow-x-auto rounded-2xl border border-slate-800 bg-slate-900">
          <table className="w-full min-w-[900px] text-left text-sm">
            <thead className="border-b border-slate-800 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-3">Event</th>
                <th className="px-4 py-3">Platform A</th>
                <th className="px-4 py-3">Platform B</th>
                <th className="px-4 py-3 text-right">Edge (raw)</th>
                <th className="px-4 py-3 text-right">Fees+slip</th>
                <th className="px-4 py-3 text-right">Spread after fees</th>
                <th className="px-4 py-3">Flags</th>
                <th className="px-4 py-3">Detected</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={8} className="px-4 py-8 text-center text-slate-500">
                    Loading…
                  </td>
                </tr>
              ) : filteredSpreads.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-4 py-8 text-center text-slate-500">
                    No matched spreads yet. If this is the first minute, click &quot;Run cycle now&quot; or wait for
                    the poller.
                  </td>
                </tr>
              ) : (
                filteredSpreads.map((s) => (
                  <tr key={s.id} className="border-b border-slate-800/60 hover:bg-slate-800/40">
                    <td className="px-4 py-3">
                      <div className="font-medium text-white">{s.assetOrTopic}</div>
                      <div className="text-xs text-slate-500">{s.contractType}</div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="font-medium text-slate-200">
                        {PLATFORM_LABELS[s.platformA] ?? s.platformA}{" "}
                        <span className="text-slate-500">({s.marketTypeA})</span>
                      </div>
                      <div className="text-xs text-slate-500">
                        {s.outcomeA} @ {pct(s.probA)}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="font-medium text-slate-200">
                        {PLATFORM_LABELS[s.platformB] ?? s.platformB}{" "}
                        <span className="text-slate-500">({s.marketTypeB})</span>
                      </div>
                      <div className="text-xs text-slate-500">
                        {s.outcomeB} @ {pct(s.probB)}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-right font-mono text-slate-300">{pct(s.edgeRaw)}</td>
                    <td className="px-4 py-3 text-right font-mono text-slate-500">{pct(s.feesAndSlippage)}</td>
                    <td
                      className={`px-4 py-3 text-right font-mono font-semibold ${
                        s.suspicious
                          ? "text-fuchsia-400"
                          : s.spreadAfterFees >= threshold
                            ? "text-emerald-400"
                            : "text-slate-400"
                      }`}
                    >
                      {pct(s.spreadAfterFees)}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-1">
                        {s.isSimulatedPair && (
                          <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] text-amber-400">
                            simulated
                          </span>
                        )}
                        {s.timeframeMismatch && (
                          <span className="rounded bg-red-500/15 px-1.5 py-0.5 text-[10px] text-red-400">
                            window mismatch
                          </span>
                        )}
                        {s.suspicious && (
                          <span className="rounded bg-fuchsia-500/15 px-1.5 py-0.5 text-[10px] text-fuchsia-400">
                            verify match
                          </span>
                        )}
                      </div>
                      {s.note && <div className="mt-1 max-w-xs text-[11px] text-slate-500">{s.note}</div>}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-xs text-slate-500">{timeAgo(s.detectedAt)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </section>

        {showQuotes && (
          <section className="mt-8 overflow-x-auto rounded-2xl border border-slate-800 bg-slate-900">
            <h2 className="border-b border-slate-800 px-4 py-3 text-sm font-semibold text-white">
              Latest raw quotes (per platform / market / outcome)
            </h2>
            <table className="w-full min-w-[900px] text-left text-sm">
              <thead className="border-b border-slate-800 text-xs uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-3">Platform</th>
                  <th className="px-4 py-3">Title</th>
                  <th className="px-4 py-3">Outcome</th>
                  <th className="px-4 py-3 text-right">Implied prob.</th>
                  <th className="px-4 py-3">Type</th>
                  <th className="px-4 py-3">Fetched</th>
                </tr>
              </thead>
              <tbody>
                {quotes.map((q) => (
                  <tr key={q.id} className="border-b border-slate-800/60">
                    <td className="px-4 py-2">{PLATFORM_LABELS[q.platform] ?? q.platform}</td>
                    <td className="max-w-sm truncate px-4 py-2 text-slate-300" title={q.rawTitle}>
                      {q.rawTitle}
                    </td>
                    <td className="px-4 py-2 text-slate-400">{q.outcome}</td>
                    <td className="px-4 py-2 text-right font-mono">{pct(q.impliedProbability)}</td>
                    <td className="px-4 py-2 text-slate-500">{q.marketType}</td>
                    <td className="whitespace-nowrap px-4 py-2 text-xs text-slate-500">{timeAgo(q.fetchedAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

        <footer className="mt-10 text-xs text-slate-600">
          MVP scaffold — no auto-trading. Log everything, look at real spread frequency/size after fees for a few
          days before considering anything more automated (spec section 6/9).
        </footer>
      </div>
    </main>
  );
}
