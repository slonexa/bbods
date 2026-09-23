# Changelog

All notable changes to this project will be documented in this file.

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

- **Custom Bankroll Calculator ($X)**: Added real-time bankroll input on the dashboard (default $20) with instant presets (`$10`, `$20`, `$50`, `$100`, `$500`). Recalculates exact dollar allocations for both legs across all table rows and cards instantly without server roundtrips.
- **Recommended Bankroll Protection**: Added minimum order limit detection ($1.00 exchange minimum) with automated calculation of recommended bankroll for skewed odds (`⚠️ Доля < $1.00. Рек. банк: $X`), preventing order rejection on exchanges.
- **One-Click Deal Copying**: Added `📋 Скопировать сделку` button on each trade card to copy ticket symbols, actions, and exact dollar stakes directly to clipboard.
- **Audio Chime for Surebets**: Integrated Web Audio API chime triggered when a true guaranteed arbitrage (`is_arb == 1 && hedge_margin > 0`) is detected, complete with a persistent toggle button (`🔔 Звук: ВКЛ / ВЫКЛ`).
- **Clean Strike Selection & Sorting**: Upgraded `get_latest_clean_spreads()` in `engine/db.py` to intelligently select the superior trade leg (highest `is_arb` and `hedge_margin`) for each clean strike and pin true surebets to the top of the table.
- **Dynamic Spread & Column Sorting**: Added fast one-click sorting by spreads (`📈 По спреду ▼`), margin (`🔥 По марже`), and timestamp (`⏱ По времени`), along with clickable table column headers (`Спред (net)`, `Коэфф A`, `Коэфф B`, `Время`, `Событие`) with toggleable ASC/DESC indicators.

### Fixed
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
