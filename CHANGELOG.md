# Changelog

All notable changes to this project will be documented in this file.

## [0.6.0] — 2026-09-30

### Added
- **4 HFT Auto-Paper Strategies (`engine/auto_paper.py`)**: Implemented `sync_start_arb` (simultaneous Bybit+Poly entry in first 0-25s, WR=96.3%), `poly_48s_lag` (Poly CLOB lag exploitation, WR=62.9%), `two_step_hedge` (2-step hedging with locked profit), `corridor_2x` (asymmetric corridor on different strikes).
- **Polymarket CLOB 1-Second Poller**: Added real-time orderbook polling for 5MIN/15MIN Up/Down contracts via `clob.polymarket.com/books` with 5.0s staleness guard.
- **Rolling 60s TWAP**: Computes Chainlink-compatible rolling TWAP from Bybit index price stream for Polymarket settlement proxy.
- **Direction-Aware Corridor Matching**: Upgraded `process_matches` to pair 44→100+ contracts via complementary direction resolution (`ABOVE↔NO`, `BELOW↔YES`).
- **`is_arb_time_risky` Flag**: Added expiration time divergence check — surebets with >30min time gap marked as risky, not guaranteed.
- **Real Poly Opposite Price**: Replaced synthetic `1.0 - prob_poly` with actual market price from `poly_lookup` cache, with `opp_price_is_synthetic` fallback flag.
- **Flexible Strike Grid Parsing**: Bybit parser now accepts any strike step ($10, $50, $100+) without hardcoded grid assumptions. Test: `test_bybit_mixed_step_strike_grid_no_loss`.
- **Value 1-Leg Guard**: `💎 Value 1-плечо` badge only shown when Bybit and Poly strikes match exactly — prevents false value signals from different strike levels.
- **Unit Test Suite (8 tests)**: Covers spread math, Dutch Book, surebet time blocking, corridor settlement, binomial gates, Telegram dedup, lag methodology, and mixed strike grids.

### Fixed
- **Frozen Cache v4 — WS Reconnect Stale Data (`collectors/bybit.py`)**: On WebSocket disconnect, `_on_close` now clears `self.tickers` and `self.contracts` before reconnecting, then re-seeds fresh data from REST. Previously, stale ticker data from before the disconnect persisted in RAM, causing ghost odds (e.g., 8.62x on ABOVE showing +23% phantom arbitrage after internet outage).
- **WS Ticker Timestamps**: Added `_fetched_at = time.time()` to all WebSocket-received ticker updates. Previously WS data had no timestamp, making the staleness check in `fetch()` ineffective (always passed via `now_ts` fallback).
- **Staleness Threshold Tightened (180s → 60s)**: Reduced stale ticker tolerance from 3 minutes to 60 seconds to catch post-reconnect ghost data faster. REST refreshes every 10s, so 60s is safe with 6x margin.
- **Top Spreads Deduplication (`engine/db.py`)**: Added `ROW_NUMBER() OVER (PARTITION BY event_key ORDER BY detected_at DESC)` to `get_top_spreads` — one card per contract, not 3+ duplicates. Added 30-minute freshness window with fallback.
- **`ticks_db_path` Attribute Error**: Fixed `AutoPaperTrader` missing `ticks_db_path` initialization causing fast 1s cycle crashes.
- **Poly CLOB Freshness Guard**: If Polymarket orderbook data is older than 5.0s, it's zeroed out to prevent false arbitrage against frozen/lagged prices.

## [Unreleased]

### Added
- **Collectors Module**: Base structure and Polymarket integration via public API. 
- **Bybit Odds Integration**: Fully working real-time collector using `curl_cffi` to bypass Cloudflare and fetch raw internal JSON APIs. Now fetches all active options contracts (~100+ items).
- **Dutch Book Engine**: Added internal arbitrage matching for Bybit to automatically match complementary pairs (UP/DOWN, IN/OUT, ABOVE/BELOW).
- **Hedging Calculator**: Added optimal stake distribution (Kelly-style split %) to the spread engine for perfect risk-free hedging.
- **Price History DB (V2 Feature)**: Added `price_history` table logging all real-time tick data for future statistical arbitrage and momentum strategies.
- **Normalizer Module**: Two-way ID matching system and template `event_map.json`.
- **Engine Module**: Spread calculation logic with fee and slippage subtraction, plus an SQLite database (`spreads.db`) for tracking opportunities over time.
- **Dashboard Module**: FastAPI server and a lightweight, vanilla JS frontend to display arbitrage opportunities in real-time. Displays real-time odds, probability sums, net margins, and recommended stake splits.
- **Documentation**: Added comprehensive docs (`docs/` folder) for architecture, adding new platforms, and AI agent instructions.
- **Polymarket Target Parsing**: Enhanced Polymarket collector with automatic recognition of daily crypto price contracts, strike prices, and volume-based event merging.
- **Automated Cross-Platform Matching Engine**: Implemented `auto_match_crypto_targets()` in `normalizer/mapper.py` to automatically match Bybit Target contracts with Polymarket daily price targets (same asset, exact strike price, compatible direction, same date) without requiring manual entries in `event_map.json`.
- **Action Recommendation Engine**: Automated calculation of trade sides (`action_a`, `action_b`, `action_summary`) informing the user exactly which direction to take on each exchange (e.g., `Bybit: BELOW + Poly: YES`) to exploit price divergences and lock in synthetic hedges.
- **Direct Exchange Navigation**: Added prominent one-click buttons (`Bybit ↗`, `Poly ↗`) and a dual-tab opener (`Оба ↗`) on top opportunity cards and table rows.
- **Top Opportunities Live Bar**: Added an All-Time Highs / top opportunities bar to the dashboard showing the top spreads with direct actions, split percentages, and exchange deep links.

- **Permanent Surebets & Opportunities Archive (`arbs_archive`)**: Created dedicated permanent archive table in SQLite ensuring that every confirmed surebet (`is_arb = 1`), positive hedge margin (`hedge_margin > 0`), and high-spread opportunity (>= 5%) is saved forever with full ticket metadata before older raw records are pruned.
- **Aggressive Database Cleanup & Automated VACUUM**: Upgraded retention policy to 24 hours for spreads and 12 hours for price history with periodic `VACUUM` and `PRAGMA wal_checkpoint(TRUNCATE)` every 300 cycles. Reclaimed over 260 MB of disk space on first run and added `/api/stats` and `/api/vacuum` endpoints.
- **Smart Dynamic Tick Deduplication**: Implemented delta-based deduplication in `run.py` that only writes to `price_history` when prices or odds actually shift (or on a 5-minute heartbeat), slashing redundant SQLite write I/O by ~90% and halting unbounded database bloat.
- **Polymarket Gamma Collector Optimization**: Replaced per-request connections with a persistent `requests.Session()` connection pool, reducing API request latency by ~50%. Added automated parsing of 24h volume and order book liquidity depth across all 400 scanned markets.
- **Token Coverage Expansion**: Added automatic symbol recognition and matching for additional crypto assets (SOL, XRP, DOGE, BNB) across both Bybit and Polymarket collectors.
- **Order Book Liquidity Badges in UI**: Displayed live liquidity depth tags (`💧 $49.8k`) on Polymarket trading legs directly within the cross-platform comparison table.
- **Data Freshness & Stale Feed Indicator**: Connected real-time latency tracker to the navbar `LIVE` beacon. If market quotes are older than 50 seconds, the badge turns red (`⚠️ Xс назад`) alerting the trader to stale data.
- **Health Check API (`/api/health`)**: Added dedicated diagnostics endpoint reporting scanner uptime, cycle duration, collector status, and active database statistics.
- **Spread History Area Chart in Deal Calculator Modal**: Embedded an interactive SVG area chart with timeframe selectors (`1ч`, `6ч`, `12ч`, `24ч`) inside the deal calculator modal. Features glowing gradient fills, zero/max reference lines, surebet highlight markers (`is_arb = 1`), cursor hover tooltips, and a stats summary strip showing minimum, maximum, average spread, and maximum hedge margin.
- **Spread History API Endpoint (`/api/spread_history`)**: Added backend endpoint querying time-series data from `spreads` with automated downsampling (up to 150 points) and fuzzy strike title matching for lightning-fast chart rendering.
- **Smart Alerts Engine & Telegram Bot Integration**: Built server-side notification engine in `engine/alerts.py` (`AlertManager`) supporting configurable thresholds (min spread %, hedge margin %, surebet priority, per-market cooldowns), file logging (`alerts.log`), Telegram Bot API message dispatch, and dashboard settings modal (`✈️ Telegram`). Added API routes `/api/alerts/config`, `/api/alerts/test_telegram`, and `/api/alerts/history`.
- **P&L Analytics Terminal & Virtual Equity Curve**: Built comprehensive paper trading and portfolio analytics dashboard:
  - Permanent primary navigation tab `📊 P&L и Портфель (N)` directly accessible in the terminal.
  - 6 KPI Analytics Strip: Total turnover, net realized P&L, Win Rate %, open vs closed ratio, average ROI %, and total capital balance.
  - Interactive SVG Virtual Equity Curve: Real-time balance progression starting from custom bankroll ($20, $100, etc.), neon emerald area fill, dotted starting baseline, and mouse hover tooltips showing individual deal profit and total equity.
  - Asset Performance Breakdown: Grouped metrics and badges for BTC, ETH, SOL, and other assets.
  - Historical Surebets Simulation: Embedded viewer for SQLite `arbs_archive` records with 1-click `⚡ В симулятор ($20)` to immediately backtest historical market divergences.
  - Sample Trade Generator (`🧪 + Тестовая сделка ($20)`) for immediate testing and verification.
  - Trade Management & CSV Export: Settle trades (`💰 Зафиксировать` / `✕ Отменить`), filter by status, and export full portfolio logs to CSV.
- **High-Frequency 1-Second Tick Logger (`engine/sec_logger.py`)**: Implemented dedicated 1Hz daemon logging Bybit Odds `5MIN` and `15MIN` Up/Down contracts into an isolated WAL-mode database (`ticks.db`) to prevent lock contention with `spreads.db`. Simultaneously streams 3 real-time Bybit V5 WebSocket feeds (`wss://stream.bybit.com/v5/public/spot` and `wss://stream.bybit.com/v5/public/linear`) to record `index_price` (`ecIndexPrice` — Bybit's official Odds settlement benchmark), `spot_price`, `futures_price`, and `mark_price` alongside `seconds_to_expiry` and `window_open_index`.
- **Automated Paper Trading Bot (`engine/auto_paper.py`)**: Built autonomous demo trading engine that automatically enters simulated positions ($20 default stake) on cross-platform surebets/spreads and 5-minute momentum breakouts, tracks open deals in SQLite (`paper_trades`), and automatically settles them upon expiration against real Bybit Index Price. Added `🤖 Авто-сделки: ВКЛ/ВЫКЛ` toggle and real-time portfolio synchronization to the P&L terminal.
- **Statistical Hypotheses Validator (`scripts/analyze_hypotheses.py`)**: Created analytical CLI tool to test Hypothesis 4.4 (market maker odds update lag in the final 60s before expiration) and Hypothesis 4.5 (early-window momentum continuation Win Rate vs 55.55% breakeven threshold).

### Fixed
- **Bybit Stale Ticker & Shifted Odds Bug (Frozen RAM Cache)**: Root-caused and fixed the issue where the scanner showed a profitable spread on BTC $84k with Bybit at 1.72x, but upon clicking through, 1.72x actually belonged to $85k on Bybit. Bybit's public WebSocket topic `event.ticker.all` never emits ticker updates for Target options, leaving initial startup quotes frozen in memory while Polymarket updated dynamically. As spot Bitcoin rose, the true 1.72x odds shifted to $85k, but the scanner kept serving stale $84k quotes. Fixed by adding persistent session-pooled REST polling (~100ms latency) in `BybitOddsCollector.fetch()`, atomic ticker swapping, and auto-purging contracts older than 60s.
- **Polymarket Target & Strike Classification Hardening**: Enhanced question regex in `collectors/polymarket.py` to explicitly exclude range contracts (`between`) and touch contracts (`reach`, `hit`, `touch`, `dip`) from single-strike Target matching. Added support for `$XXk`, `$XX.Xk`, `$XX,XXX` strike prices, and `greater than` / `less than` direction phrasing.
- **Explicit Exchange Leg Attribution**: Added explicit `url_bybit`, `url_poly`, `odds_bybit`, `odds_poly`, `prob_bybit`, `prob_poly` fields across `SpreadEngine` and dashboard UI handlers, guaranteeing that one-click trade buttons and deal modal deep links always point to the correct exchange legs.
- **True Dutch Book Mathematical Staking**: Completely replaced flawed probability-based stake splits with rigorous inverse-odds sizing ($S_A \propto 1/O_A, S_B \propto 1/O_B$). Payouts on both outcomes are now mathematically guaranteed to be identical down to the cent ($Payout_A = Payout_B = \text{Total\_Stake} / \text{Hedge\_Cost}$), eliminating unhedged capital allocation (such as the 5%/95% gamble).
- **Elimination of Phantom Arbitrage (`✓ ВИЛКА`)**: Removed fictitious synthetic opposite cost formulas ($1.0 - cost_a$) that assumed Bybit had 0% bookmaker margin. The scanner now only triggers `✓ ВИЛКА` when the true combined hedge cost across actual live ticket coefficients is strictly $< 1.0$ after fees.
- **Equal-Payout Trade Recommendations in Dashboard**: Redesigned trade plan cards in `index.html` to clearly display exact stake percentages, live ticket odds/prices, and expected payouts ($Payout per $X) for both outcomes.
- **Action Signal Inversion**: Resolved critical bug in `engine/spread.py` where trade direction recommendations were inverted for Polymarket markets phrased with `BELOW`. Replaced heuristic with strict economic direction mapping (`LONG`/`SHORT`) ensuring true opposite-side hedging.
- **Cross-Platform Synthetic Hedge Cost**: Corrected `best_hedge_cost` formula to account for platform-specific payout coefficients and ensure true cross-platform leg allocation instead of picking both legs from a single venue.
- **Run Loop Resilience**: Wrapped main collection cycle in `run.py` with `try...except` to prevent complete scanner crash on transient network, API timeout, or SQLite concurrency errors.
- **Bybit WebSocket Thread Safety**: Introduced `threading.Lock` across `self.tickers` and `self.contracts` to prevent `RuntimeError: dictionary changed size during iteration` during concurrent WebSocket updates and REST fetch loops.
- **Polymarket Target False Positives**: Replaced substring searches with regex word boundaries (`\b`) to prevent false triggers on words like `resolution`, `white`, `understand`.
- **Dashboard Colspan Bug**: Fixed table alignment in complementary pairs table (`colspan="12"`).
- **SQLite Performance & Unbounded Growth**: Created indexes on `detected_at`, `spread_after_fees`, `timestamp`, and `market_id`. Added automated 48-hour retention cleanup to prevent uncontrolled DB size explosion.
- **Implied Cost Persistence**: Stored and displayed true odds-based `implied_cost` in the database and dashboard rather than falling back to `prob_sum`.

### Changed
- **Bybit URL Deep Links**: Fixed deep links to accurately redirect to the exact Bybit contract symbol (e.g., `/trade/odds/BTCUSDT-5MIN-UP`).
- **Symbol Parsing**: Upgraded Bybit parser to reliably handle 3-part (UpDown), 4-part (Target), and 5-part (Range) string formats, including date extraction from `expiry_tag` and human-readable Target titles.
- **Polymarket URL Logic**: Updated URL builder to support precise `market_slug` routing.
- **Dashboard Cross-Platform Table**: Added dedicated columns for `🎯 Сделка (Что брать)` and `Ссылки` with exchange action buttons.

### Planned
- **MEXC, OKX, Gate Collectors**: Expand data collection to new CEX prediction markets (5-min / 15-min contracts).
- **Telegram Alerts**: Push instant notifications when profitable spreads exceed 10-15%.
