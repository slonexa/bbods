import {
  boolean,
  doublePrecision,
  index,
  integer,
  jsonb,
  pgTable,
  serial,
  text,
  timestamp,
  uniqueIndex,
} from "drizzle-orm/pg-core";

/**
 * Platforms registry — один источник правды про площадку.
 * Collector каждой площадки читает отсюда флаги/фи, чтобы не хардкодить.
 */
export const platforms = pgTable("platforms", {
  id: serial("id").primaryKey(),
  slug: text("slug").notNull().unique(),
  name: text("name").notNull(),
  // cex_odds | prediction_market
  kind: text("kind").notNull().default("prediction_market"),
  enabled: boolean("enabled").notNull().default(true),
  /** live | simulated | planned */
  mode: text("mode").notNull().default("planned"),
  /** fixed_odds | continuous */
  defaultMarketType: text("default_market_type").notNull().default("continuous"),
  feeBps: doublePrecision("fee_bps").notNull().default(0),
  slippageBps: doublePrecision("slippage_bps").notNull().default(0),
  /** минимальный интервал опроса, мс — защита от rate-limit */
  minIntervalMs: integer("min_interval_ms").notNull().default(10000),
  /** верхнеуровневая подсказка, какой внутренний эндпоинт дёргать */
  endpointHint: text("endpoint_hint"),
  /** какой оракул / ценовой индекс использует площадка для резолюции */
  resolutionSource: text("resolution_source"),
  notes: text("notes"),
  updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
});

/**
 * Сырой тик котировки в унифицированном формате collector'а (раздел 2 ТЗ).
 * Пишем ВСЁ, что пришло — не только найденные спреды (нужно для бэктестов).
 */
export const quotes = pgTable(
  "quotes",
  {
    id: serial("id").primaryKey(),
    platform: text("platform").notNull(),
    marketId: text("market_id").notNull(),
    rawTitle: text("raw_title").notNull(),
    outcome: text("outcome").notNull(),
    /** fixed_odds | continuous */
    marketType: text("market_type").notNull(),
    /** десятичный коэффициент для fixed_odds, иначе null */
    odds: doublePrecision("odds"),
    /** implied probability = 1/odds или цена шеры continuous (0..1) */
    impliedProbability: doublePrecision("implied_probability").notNull(),
    /** вероятность комплементарного исхода, если площадка её отдаёт */
    complementaryProbability: doublePrecision("complementary_probability"),
    expiry: timestamp("expiry", { withTimezone: true }),
    windowStart: timestamp("window_start", { withTimezone: true }),
    asset: text("asset"),
    contractType: text("contract_type"),
    direction: text("direction"),
    strike: doublePrecision("strike"),
    spotPrice: doublePrecision("spot_price"),
    volume24h: doublePrecision("volume_24h"),
    liquidity: doublePrecision("liquidity"),
    /** live | simulated */
    source: text("source").notNull().default("live"),
    endpoint: text("endpoint"),
    fetchedAt: timestamp("fetched_at", { withTimezone: true }).notNull().defaultNow(),
    raw: jsonb("raw"),
  },
  (t) => [
    index("quotes_platform_fetched_idx").on(t.platform, t.fetchedAt),
    index("quotes_market_idx").on(t.platform, t.marketId, t.outcome),
  ],
);

/**
 * Каноническое событие = то, что получается после normalizer'а.
 * eventKey строится программно для крипты и вручную для спортивных.
 */
export const events = pgTable(
  "events",
  {
    id: serial("id").primaryKey(),
    eventKey: text("event_key").notNull().unique(),
    title: text("title").notNull(),
    asset: text("asset"),
    contractType: text("contract_type"),
    direction: text("direction"),
    strike: doublePrecision("strike"),
    expiry: timestamp("expiry", { withTimezone: true }),
    windowStart: timestamp("window_start", { withTimezone: true }),
    /** updown_5m | target_by_date | range | sport | other */
    kind: text("kind").notNull().default("other"),
    resolutionSource: text("resolution_source"),
    matchSource: text("match_source").notNull().default("auto"),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => [index("events_expiry_idx").on(t.expiry)],
);

/**
 * Связка event -> leg на площадке (аналог event_map.json, но в БД).
 */
export const eventMatches = pgTable(
  "event_matches",
  {
    id: serial("id").primaryKey(),
    eventKey: text("event_key").notNull(),
    platform: text("platform").notNull(),
    marketId: text("market_id").notNull(),
    outcome: text("outcome").notNull(),
    /** auto | manual */
    source: text("source").notNull().default("auto"),
    confidence: doublePrecision("confidence").notNull().default(1),
    note: text("note"),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => [
    uniqueIndex("event_matches_unique").on(t.eventKey, t.platform, t.marketId, t.outcome),
    index("event_matches_event_idx").on(t.eventKey),
  ],
);

export const spreads = pgTable(
  "spreads",
  {
    id: serial("id").primaryKey(),
    eventKey: text("event_key").notNull(),
    title: text("title").notNull(),
    platformA: text("platform_a").notNull(),
    probA: doublePrecision("prob_a").notNull(),
    platformB: text("platform_b").notNull(),
    probB: doublePrecision("prob_b").notNull(),
    marketTypeA: text("market_type_a").notNull(),
    marketTypeB: text("market_type_b").notNull(),
    sideA: text("side_a"),
    sideB: text("side_b"),
    /** сырой эдж до издержек */
    rawEdge: doublePrecision("raw_edge").notNull(),
    feesBps: doublePrecision("fees_bps").notNull().default(0),
    slippageBps: doublePrecision("slippage_bps").notNull().default(0),
    spreadAfterFees: doublePrecision("spread_after_fees").notNull(),
    stakeA: doublePrecision("stake_a"),
    stakeB: doublePrecision("stake_b"),
    payoutIfA: doublePrecision("payout_if_a"),
    payoutIfB: doublePrecision("payout_if_b"),
    /** arb | lag | none | phantom_oracle */
    verdict: text("verdict").notNull().default("none"),
    warnings: jsonb("warnings").$type<string[]>(),
    oddsA: doublePrecision("odds_a"),
    oddsB: doublePrecision("odds_b"),
    /** возраст котировок в мс — расхождение > N мс = несинхронный замер */
    quoteAgeDeltaMs: integer("quote_age_delta_ms"),
    detectedAt: timestamp("detected_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => [
    index("spreads_detected_idx").on(t.detectedAt),
    index("spreads_event_idx").on(t.eventKey),
  ],
);

/**
 * price_history — сырые тики по событию, основа для будущих бэктестов (раздел 4.5).
 */
export const priceHistory = pgTable(
  "price_history",
  {
    id: serial("id").primaryKey(),
    eventKey: text("event_key"),
    platform: text("platform").notNull(),
    marketId: text("market_id").notNull(),
    outcome: text("outcome").notNull(),
    impliedProbability: doublePrecision("implied_probability").notNull(),
    odds: doublePrecision("odds"),
    spotPrice: doublePrecision("spot_price"),
    fetchedAt: timestamp("fetched_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => [
    index("price_history_event_idx").on(t.eventKey, t.fetchedAt),
    index("price_history_platform_idx").on(t.platform, t.fetchedAt),
  ],
);

/**
 * Лог запусков collector'ов — видно, какая площадка живая, какая отдаёт 403/капчу.
 */
export const collectorRuns = pgTable(
  "collector_runs",
  {
    id: serial("id").primaryKey(),
    platform: text("platform").notNull(),
    endpoint: text("endpoint"),
    startedAt: timestamp("started_at", { withTimezone: true }).notNull().defaultNow(),
    finishedAt: timestamp("finished_at", { withTimezone: true }),
    durationMs: integer("duration_ms"),
    httpStatus: integer("http_status"),
    itemsCount: integer("items_count").notNull().default(0),
    parsedCount: integer("parsed_count").notNull().default(0),
    status: text("status").notNull().default("ok"),
    errorMessage: text("error_message"),
  },
  (t) => [index("collector_runs_platform_idx").on(t.platform, t.startedAt)],
);

/**
 * Лаборатория лага (раздел 4.4): как часто площадка реально обновляет коэффициент
 * относительно тиков спота. Без этих данных паттерн "ловли лага" строить нельзя.
 */
export const lagSamples = pgTable(
  "lag_samples",
  {
    id: serial("id").primaryKey(),
    platform: text("platform").notNull(),
    marketId: text("market_id").notNull(),
    outcome: text("outcome").notNull(),
    spotBefore: doublePrecision("spot_before"),
    spotAfter: doublePrecision("spot_after"),
    spotMovePct: doublePrecision("spot_move_pct"),
    probBefore: doublePrecision("prob_before"),
    probAfter: doublePrecision("prob_after"),
    /** мс между движением спота и обновлением коэффициента (оценка) */
    lagMs: integer("lag_ms"),
    sampledAt: timestamp("sampled_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (t) => [index("lag_samples_platform_idx").on(t.platform, t.sampledAt)],
);

/** Настройки приложения (порог алерта и пр.) — key/value, чтобы ничего не хардкодить. */
export const settings = pgTable("settings", {
  key: text("key").primaryKey(),
  value: text("value").notNull(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
});
