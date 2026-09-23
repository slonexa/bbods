import type { CollectorResult } from "@/lib/types";

export interface CycleStatus {
  startedAt: string;
  finishedAt: string;
  durationMs: number;
  collectors: {
    platform: string;
    ok: boolean;
    error?: string;
    quoteCount: number;
    durationMs: number;
    isSimulated: boolean;
  }[];
  quoteCount: number;
  groupCount: number;
  spreadCount: number;
  actionableSpreadCount: number;
  error?: string;
}

const globalForStatus = globalThis as typeof globalThis & {
  __arbScannerStatus?: {
    history: CycleStatus[];
    pollerStarted: boolean;
  };
};

if (!globalForStatus.__arbScannerStatus) {
  globalForStatus.__arbScannerStatus = { history: [], pollerStarted: false };
}

const store = globalForStatus.__arbScannerStatus;

export function recordCycle(status: CycleStatus) {
  store.history.unshift(status);
  if (store.history.length > 30) store.history.length = 30;
}

export function getLatestStatus(): CycleStatus | null {
  return store.history[0] ?? null;
}

export function getStatusHistory(): CycleStatus[] {
  return store.history;
}

export function markPollerStarted(): boolean {
  if (store.pollerStarted) return false;
  store.pollerStarted = true;
  return true;
}

export function collectorResultsToStatus(results: CollectorResult[]) {
  return results.map((r) => ({
    platform: r.platform,
    ok: r.ok,
    error: r.error,
    quoteCount: r.quotes.length,
    durationMs: r.durationMs,
    isSimulated: r.isSimulated,
  }));
}
