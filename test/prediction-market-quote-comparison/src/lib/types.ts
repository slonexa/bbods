// Shared contract that every collector must return. This is the ONLY thing
// normalizer/engine/dashboard know about — collectors can be swapped or added
// without touching the rest of the pipeline (see project spec, section 1).
export type MarketType = "fixed_odds" | "continuous";

export type ContractType = "updown" | "target" | "range" | "manual";

export interface QuoteMeta {
  asset?: string; // "BTC" | "ETH" | ...
  contractType?: ContractType;
  threshold?: number; // price level for target/range contracts
  direction?: "above" | "below";
  windowSeconds?: number; // e.g. 300 for a 5-minute up/down window
  windowStart?: string; // ISO
  windowEnd?: string; // ISO
  priceIndexSource?: string; // which price feed/oracle is authoritative
  fee?: number; // platform trading fee, fraction of stake
  [key: string]: unknown;
}

export interface Quote {
  platform: string;
  marketId: string;
  rawTitle: string;
  outcome: string;
  impliedProbability: number; // 0..1
  marketType: MarketType;
  expiry: string | null; // ISO timestamp
  fetchedAt: string; // ISO timestamp
  isSimulated: boolean;
  meta: QuoteMeta;
}

export interface CollectorResult {
  platform: string;
  ok: boolean;
  quotes: Quote[];
  error?: string;
  durationMs: number;
  isSimulated: boolean;
}

export interface EventGroup {
  eventKey: string;
  asset: string;
  contractType: ContractType;
  quotes: Quote[];
  timeframeMismatch: boolean;
}

export interface SpreadComputation {
  eventKey: string;
  assetOrTopic: string;
  contractType: ContractType;
  a: Quote;
  b: Quote;
  edgeRaw: number;
  feesAndSlippage: number;
  spreadAfterFees: number;
  timeframeMismatch: boolean;
  isSimulatedPair: boolean;
  suspicious: boolean;
  note?: string;
}
