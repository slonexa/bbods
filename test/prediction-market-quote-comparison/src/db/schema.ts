import {
  pgTable,
  serial,
  text,
  real,
  boolean,
  timestamp,
  jsonb,
  index,
} from "drizzle-orm/pg-core";

// Raw, append-only log of every quote fetched from every collector on every
// poll cycle. Nothing is thrown away here (section 4.5 of the spec) so we can
// later backtest momentum/latency ideas on real history, not on "gut feel".
export const rawQuotes = pgTable(
  "raw_quotes",
  {
    id: serial("id").primaryKey(),
    platform: text("platform").notNull(),
    marketId: text("market_id").notNull(),
    rawTitle: text("raw_title").notNull(),
    outcome: text("outcome").notNull(),
    impliedProbability: real("implied_probability").notNull(),
    marketType: text("market_type").notNull(), // "fixed_odds" | "continuous"
    expiry: timestamp("expiry", { withTimezone: true }),
    // free-form metadata: asset, contractType, threshold, windowStart/End...
    meta: jsonb("meta").$type<Record<string, unknown>>(),
    isSimulated: boolean("is_simulated").notNull().default(false),
    fetchedAt: timestamp("fetched_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => [
    index("raw_quotes_platform_idx").on(table.platform),
    index("raw_quotes_fetched_at_idx").on(table.fetchedAt),
    index("raw_quotes_market_idx").on(table.platform, table.marketId),
  ],
);

// Every pairwise comparison the engine has computed between two platforms for
// the same normalized event, whether or not it clears the alert threshold.
export const spreads = pgTable(
  "spreads",
  {
    id: serial("id").primaryKey(),
    eventKey: text("event_key").notNull(),
    assetOrTopic: text("asset_or_topic").notNull(),
    contractType: text("contract_type").notNull(), // updown | target | range | manual

    platformA: text("platform_a").notNull(),
    marketIdA: text("market_id_a").notNull(),
    marketTypeA: text("market_type_a").notNull(),
    outcomeA: text("outcome_a").notNull(),
    probA: real("prob_a").notNull(),

    platformB: text("platform_b").notNull(),
    marketIdB: text("market_id_b").notNull(),
    marketTypeB: text("market_type_b").notNull(),
    outcomeB: text("outcome_b").notNull(),
    probB: real("prob_b").notNull(),

    edgeRaw: real("edge_raw").notNull(),
    feesAndSlippage: real("fees_and_slippage").notNull(),
    spreadAfterFees: real("spread_after_fees").notNull(),

    timeframeMismatch: boolean("timeframe_mismatch").notNull().default(false),
    isSimulatedPair: boolean("is_simulated_pair").notNull().default(false),
    suspicious: boolean("suspicious").notNull().default(false),
    note: text("note"),

    detectedAt: timestamp("detected_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => [
    index("spreads_detected_at_idx").on(table.detectedAt),
    index("spreads_spread_idx").on(table.spreadAfterFees),
    index("spreads_event_key_idx").on(table.eventKey),
  ],
);

// Manual event_map entries for markets that cannot be matched programmatically
// (sports / news-style events). Seeded from normalizer/eventMap.json but kept
// in the DB too so it survives edits made from the dashboard later.
export const eventMapOverrides = pgTable("event_map_overrides", {
  id: serial("id").primaryKey(),
  eventKey: text("event_key").notNull(),
  platform: text("platform").notNull(),
  marketId: text("market_id").notNull(),
  outcome: text("outcome").notNull(),
  note: text("note"),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});
