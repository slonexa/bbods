import type { EventGroup, Quote, SpreadComputation } from "@/lib/types";
import { config } from "@/lib/config";
import { isOppositeOutcome } from "@/normalizer";

function feeFor(platform: string): number {
  return config.fees[platform] ?? 0.02;
}

// Classic Dutch-book edge for two complementary outcomes across two
// platforms: edge = 1 - (probA + probB). Positive edge means, staked
// proportionally (stake_a * O_a = stake_b * O_b), you lock the same payout
// regardless of outcome (spec section 4.1). The same formula is reused for
// fixed-vs-continuous per section 4.2 — the caveats below live in `note`,
// not in the formula itself.
const SUSPICIOUS_EDGE_THRESHOLD = 0.15;

function buildNote(a: Quote, b: Quote, edgeRaw: number): string | undefined {
  const notes: string[] = [];
  if (Math.abs(edgeRaw) >= SUSPICIOUS_EDGE_THRESHOLD) {
    notes.push(
      "Unusually large edge — this almost always means the normalizer matched two markets that are NOT really the same bet (different strike/date/resolution), not a real arbitrage. Verify manually before trusting it.",
    );
  }
  if (a.marketType !== b.marketType) {
    notes.push(
      "fixed_odds x continuous: capture both quotes at the SAME instant — the continuous leg can move after you lock the fixed-odds leg, and you cannot exit the fixed-odds leg early to rehedge.",
    );
  }
  if (a.isSimulated || b.isSimulated) {
    notes.push(
      `${[a.isSimulated ? a.platform : null, b.isSimulated ? b.platform : null].filter(Boolean).join(" & ")} data is SIMULATED (no confirmed public endpoint yet) — treat this spread as a pipeline demo, not a real signal.`,
    );
  }
  if (a.meta.priceIndexSource && b.meta.priceIndexSource && a.meta.priceIndexSource !== b.meta.priceIndexSource) {
    notes.push("Different price index sources assumed for each leg — verify both resolve off the same feed before trusting this as arbitrage rather than feed noise (spec 8.1).");
  }
  return notes.length ? notes.join(" ") : undefined;
}

export function computeGroupSpreads(group: EventGroup): SpreadComputation[] {
  const results: SpreadComputation[] = [];
  const { quotes } = group;

  for (let i = 0; i < quotes.length; i++) {
    for (let j = i + 1; j < quotes.length; j++) {
      const a = quotes[i];
      const b = quotes[j];
      if (a.platform === b.platform) continue; // same-platform margin isn't an arb, skip
      if (!isOppositeOutcome(a.outcome, b.outcome)) continue; // need exhaustive/exclusive pair

      const edgeRaw = 1 - (a.impliedProbability + b.impliedProbability);
      const feesAndSlippage = feeFor(a.platform) + feeFor(b.platform) + config.extraSlippageBuffer;
      const spreadAfterFees = edgeRaw - feesAndSlippage;

      results.push({
        eventKey: group.eventKey,
        assetOrTopic: group.asset,
        contractType: group.contractType,
        a,
        b,
        edgeRaw,
        feesAndSlippage,
        spreadAfterFees,
        timeframeMismatch: group.timeframeMismatch,
        isSimulatedPair: a.isSimulated || b.isSimulated,
        suspicious: Math.abs(edgeRaw) >= SUSPICIOUS_EDGE_THRESHOLD,
        note: buildNote(a, b, edgeRaw),
      });
    }
  }

  return results;
}

export function computeAllSpreads(groups: EventGroup[]): SpreadComputation[] {
  return groups.flatMap(computeGroupSpreads);
}
