/** Унифицированный формат, который отдаёт каждый collector (раздел 2 ТЗ). */
export type MarketType = "fixed_odds" | "continuous";
export type QuoteSource = "live" | "simulated";
export type ContractType = "updown" | "target" | "range" | "sport" | "other";

export interface Quote {
  platform: string;
  market_id: string;
  raw_title: string;
  outcome: string;
  market_type: MarketType;
  /** десятичный коэффициент, только для fixed_odds */
  odds?: number | null;
  implied_probability: number;
  complementary_probability?: number | null;
  expiry?: string | null;
  window_start?: string | null;
  asset?: string | null;
  contract_type?: ContractType | null;
  direction?: "up" | "down" | "above" | "below" | "inside" | "outside" | null;
  strike?: number | null;
  spot_price?: number | null;
  volume_24h?: number | null;
  liquidity?: number | null;
  resolution_source?: string | null;
  source: QuoteSource;
  endpoint?: string | null;
  fetched_at: string;
  raw?: unknown;
}

export interface CollectorResult {
  platform: string;
  endpoint: string | null;
  httpStatus: number | null;
  status: "ok" | "empty" | "throttled" | "error" | "disabled";
  items: Quote[];
  parsed: number;
  durationMs: number;
  error?: string | null;
  /** true, если данные сгенерированы симулятором, а не получены с площадки */
  fellBackToSimulator?: boolean;
}

export interface CollectorMeta {
  slug: string;
  name: string;
  kind: string;
  mode: "live" | "simulated" | "planned";
  defaultMarketType: MarketType;
  feeBps: number;
  slippageBps: number;
  minIntervalMs: number;
  endpointHint: string | null;
  resolutionSource: string | null;
  notes: string | null;
  enabled: boolean;
  candidates: string[];
}
