import type { EventGroup, Quote } from "@/lib/types";
import { config } from "@/lib/config";
import manualEventMap from "./eventMap.json";

interface ManualEventMapEntry {
  eventKey: string;
  entries: { platform: string; marketId: string; outcome: string }[];
  note?: string;
}

const MANUAL_MAP = manualEventMap as unknown as ManualEventMapEntry[];

// Outcomes that are mutually exclusive & exhaustive for a binary event — the
// pair you need for the classic Dutch-book / surebet formula (spec 4.1/4.2).
const OPPOSITES: Record<string, string> = {
  up: "down",
  down: "up",
  above: "below",
  below: "above",
  yes: "no",
  no: "yes",
};

export function isOppositeOutcome(a: string, b: string): boolean {
  return OPPOSITES[a] === b;
}

function keyForUpDown(q: Quote, bucketedStartMs: number): string {
  return `updown:${q.meta.asset}:${bucketedStartMs}`;
}

// Clusters "up/down" quotes that share the same asset and (near-)identical
// window boundaries. Per spec 8.1, different platforms may run 5-min windows
// against different price indexes / different window starts — we only group
// windows within `updownWindowToleranceSeconds` of each other and flag
// anything that isn't an *exact* match as `timeframeMismatch` so the engine
// can down-rank/annotate it instead of silently treating it as a clean arb.
function groupUpDown(quotes: Quote[]): EventGroup[] {
  const candidates = quotes.filter((q) => q.meta.contractType === "updown" && q.meta.windowStart);
  const buckets = new Map<string, { start: number; quotes: Quote[] }>();
  const toleranceMs = config.matching.updownWindowToleranceSeconds * 1000;

  for (const q of candidates) {
    const startMs = new Date(q.meta.windowStart as string).getTime();
    let placed = false;
    for (const bucket of buckets.values()) {
      if (bucket.quotes[0].meta.asset !== q.meta.asset) continue;
      if (Math.abs(bucket.start - startMs) <= toleranceMs) {
        bucket.quotes.push(q);
        placed = true;
        break;
      }
    }
    if (!placed) {
      buckets.set(keyForUpDown(q, startMs), { start: startMs, quotes: [q] });
    }
  }

  const groups: EventGroup[] = [];
  for (const bucket of buckets.values()) {
    const platforms = new Set(bucket.quotes.map((q) => q.platform));
    if (platforms.size < 2) continue; // nothing to compare
    const exactMatch = bucket.quotes.every(
      (q) => new Date(q.meta.windowStart as string).getTime() === bucket.start,
    );
    groups.push({
      eventKey: `updown:${bucket.quotes[0].meta.asset}:${new Date(bucket.start).toISOString()}`,
      asset: bucket.quotes[0].meta.asset as string,
      contractType: "updown",
      quotes: bucket.quotes,
      timeframeMismatch: !exactMatch,
    });
  }
  return groups;
}

// Clusters "target" (threshold-by-date) quotes across platforms for the same
// asset when both the strike price and expiry are close enough to be the
// same real-world bet (spec section 3: match on asset + contract type +
// level + expiry-to-the-minute — we're a bit looser on level here since
// synthetic/simulated collectors won't hit the exact same round number).
function groupTargets(quotes: Quote[]): EventGroup[] {
  const candidates = quotes.filter(
    (q) => q.meta.contractType === "target" && typeof q.meta.threshold === "number" && q.expiry,
  );
  const groups: { asset: string; threshold: number; expiryMs: number; quotes: Quote[] }[] = [];
  const thresholdTol = config.matching.thresholdTolerancePct;
  const expiryTolMs = config.matching.expiryToleranceMinutes * 60_000;

  for (const q of candidates) {
    const threshold = q.meta.threshold as number;
    const expiryMs = new Date(q.expiry as string).getTime();
    const asset = q.meta.asset as string;
    const existing = groups.find(
      (g) =>
        g.asset === asset &&
        Math.abs(g.threshold - threshold) / g.threshold <= thresholdTol &&
        Math.abs(g.expiryMs - expiryMs) <= expiryTolMs,
    );
    if (existing) existing.quotes.push(q);
    else groups.push({ asset, threshold, expiryMs, quotes: [q] });
  }

  return groups
    .filter((g) => new Set(g.quotes.map((q) => q.platform)).size >= 2)
    .map((g) => ({
      eventKey: `target:${g.asset}:${Math.round(g.threshold)}:${new Date(g.expiryMs).toISOString().slice(0, 10)}`,
      asset: g.asset,
      contractType: "target" as const,
      quotes: g.quotes,
      timeframeMismatch: false,
    }));
}

// Ready for manual sports/news mappings (eventMap.json) — groups quotes whose
// (platform, marketId) pair was explicitly hand-linked.
function groupManual(quotes: Quote[]): EventGroup[] {
  if (MANUAL_MAP.length === 0) return [];
  const groups: EventGroup[] = [];
  for (const entry of MANUAL_MAP) {
    const matched: Quote[] = [];
    for (const target of entry.entries) {
      const q = quotes.find(
        (candidate) =>
          candidate.platform === target.platform &&
          candidate.marketId === target.marketId &&
          candidate.outcome === target.outcome,
      );
      if (q) matched.push(q);
    }
    if (new Set(matched.map((q) => q.platform)).size >= 2) {
      groups.push({
        eventKey: entry.eventKey,
        asset: "manual",
        contractType: "manual",
        quotes: matched,
        timeframeMismatch: false,
      });
    }
  }
  return groups;
}

export function buildEventGroups(quotes: Quote[]): EventGroup[] {
  return [...groupUpDown(quotes), ...groupTargets(quotes), ...groupManual(quotes)];
}
