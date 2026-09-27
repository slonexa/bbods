import unittest
from engine.spread import SpreadEngine, MAX_TIME_DIFF_HOURS

class TestSpreadFixes(unittest.TestCase):
    def setUp(self):
        self.engine = SpreadEngine()

    def test_surebet_blocked_on_time_gap(self):
        """Fix 1: >0.5h time gap must set is_arb=False and is_arb_time_risky=True."""
        ea = {
            "platform": "bybit_odds", "market_id": "BTC-TARGET-86000",
            "title": "BTC $86,000 [ABOVE] (2026-09-24)", "outcome": "ABOVE",
            "odds": 2.50, "implied_probability": 0.40,
            "strike": 86000, "expiration": "2026-09-24 08:00:00",
            "diff_hours": 8.0, "time_diff_hours": 8.0
        }
        eb = {
            "platform": "polymarket", "market_id": "poly-101-Yes",
            "title": "BTC $86,000 Above by Sept 24", "outcome": "YES",
            "odds": 2.50, "implied_probability": 0.40,
            "strike": 86000, "expiration": "2026-09-24 16:00:00",
            "diff_hours": 8.0, "time_diff_hours": 8.0
        }
        poly_data = [
            {"market_id": "poly-101-Yes", "outcome": "YES", "implied_probability": 0.40},
            {"market_id": "poly-101-No", "outcome": "NO", "implied_probability": 0.45}
        ]
        
        # Test with 8.0h difference (exceeds 0.5h threshold)
        res_gap = self.engine.process_matches([(ea, eb)], poly_data=poly_data)
        self.assertEqual(len(res_gap), 1)
        r = res_gap[0]
        self.assertFalse(r["is_arb"])
        self.assertTrue(r["is_arb_time_risky"])
        self.assertFalse(r["opp_price_is_synthetic"])

        # Test with 0.1h difference (within 0.5h threshold -> true surebet)
        ea_synced = dict(ea, diff_hours=0.1, time_diff_hours=0.1)
        eb_synced = dict(eb, diff_hours=0.1, time_diff_hours=0.1)
        res_synced = self.engine.process_matches([(ea_synced, eb_synced)], poly_data=poly_data)
        self.assertTrue(res_synced[0]["is_arb"])
        self.assertFalse(res_synced[0]["is_arb_time_risky"])

    def test_polymarket_real_vs_synthetic_price(self):
        """Fix 2: Real Polymarket opposite outcome prices must be prioritized over 1.0 - prob."""
        ea = {
            "platform": "bybit_odds", "market_id": "ETH-TARGET-2800",
            "title": "ETH $2,800 [BELOW]", "outcome": "BELOW",
            "odds": 2.00, "implied_probability": 0.50, "diff_hours": 0.0, "time_diff_hours": 0.0
        }
        eb = {
            "platform": "polymarket", "market_id": "4617542-Yes",
            "title": "ETH $2,800 Above", "outcome": "YES",
            "odds": 2.00, "implied_probability": 0.50, "diff_hours": 0.0, "time_diff_hours": 0.0
        }
        
        # Case A: Real opposite outcome available
        poly_with_opp = [
            {"market_id": "4617542-Yes", "outcome": "YES", "implied_probability": 0.50},
            {"market_id": "4617542-No", "outcome": "NO", "implied_probability": 0.42}
        ]
        res_a = self.engine.process_matches([(ea, eb)], poly_data=poly_with_opp)
        self.assertFalse(res_a[0]["opp_price_is_synthetic"])
        self.assertAlmostEqual(res_a[0]["hedge_cost"], 0.50 + 0.42, places=3)

        # Case B: Missing opposite outcome -> fallback to synthetic
        poly_missing_opp = [
            {"market_id": "4617542-Yes", "outcome": "YES", "implied_probability": 0.50}
        ]
        res_b = self.engine.process_matches([(ea, eb)], poly_data=poly_missing_opp)
        self.assertTrue(res_b[0]["opp_price_is_synthetic"])
        self.assertAlmostEqual(res_b[0]["hedge_cost"], 0.50 + 0.50, places=3)


class TestStatisticalAndLagMethodology(unittest.TestCase):
    def test_binomial_and_sample_size_gate(self):
        """Section 12: Never declare edge when N < 100 or when binomial p-value >= 0.05."""
        from scripts.analyze_hypotheses import binomial_p_value, wilson_ci_95, format_verdict, BREAKEVEN_180X

        # 1) Small sample (e.g. 30 wins out of 45 = 66.7%) must be blocked by N < 100 gate
        v_small = format_verdict(30, 45, p0=BREAKEVEN_180X, min_n=100)
        self.assertIn("МАЛО ДАННЫХ", v_small)
        self.assertNotIn("СТАТИСТИЧЕСКИ ЗНАЧИМЫЙ ЭДЖ ПОДТВЕРЖДЕН", v_small)

        # 2) Large sample (N=120) with 70 wins (58.3% > 55.56%, but p-value > 0.05 -> noise)
        p_noise = binomial_p_value(70, 120, BREAKEVEN_180X)
        self.assertGreaterEqual(p_noise, 0.05)
        v_noise = format_verdict(70, 120, p0=BREAKEVEN_180X, min_n=100)
        self.assertIn("НЕ ЗНАЧИМО", v_noise)
        self.assertNotIn("СТАТИСТИЧЕСКИ ЗНАЧИМЫЙ ЭДЖ ПОДТВЕРЖДЕН", v_noise)

        # 3) Large sample (N=150) with 102 wins (68.0%, p-value < 0.05 -> true significant edge)
        p_sig = binomial_p_value(102, 150, BREAKEVEN_180X)
        self.assertLess(p_sig, 0.01)
        ci_low, ci_high = wilson_ci_95(102, 150)
        self.assertGreater(ci_low, 55.56)
        v_sig = format_verdict(102, 150, p0=BREAKEVEN_180X, min_n=100)
        self.assertIn("СТАТИСТИЧЕСКИ ЗНАЧИМЫЙ ЭДЖ ПОДТВЕРЖДЕН", v_sig)

    def test_lag_ts_ported_methodology(self):
        """Section 10 & 12: Verify exact ms lag measurement between spot impulse and quote reaction."""
        from scripts.analyze_hypotheses import compute_lag_study

        # Simulate a 5m window where spot jumps +0.05% at t=1000ms, but poly_up_ask reacts at t=3500ms (lag = 2500ms)
        ticks = [
            {"symbol": "BTCUSDT", "window_id": 1, "ts_ms": 0.0,    "index_price": 84000.0, "spot_price": 84000.0, "poly_up_ask": 0.50},
            {"symbol": "BTCUSDT", "window_id": 1, "ts_ms": 1000.0, "index_price": 84050.0, "spot_price": 84050.0, "poly_up_ask": 0.50},
            {"symbol": "BTCUSDT", "window_id": 1, "ts_ms": 2000.0, "index_price": 84050.0, "spot_price": 84050.0, "poly_up_ask": 0.50},
            {"symbol": "BTCUSDT", "window_id": 1, "ts_ms": 3500.0, "index_price": 84050.0, "spot_price": 84050.0, "poly_up_ask": 0.56},
        ]
        res = compute_lag_study(ticks, "poly_up_ask", "Polymarket Test")
        self.assertEqual(res["samples"], 1)
        self.assertEqual(res["medianLagMs"], 2500)
        self.assertEqual(res["p90LagMs"], 2500)

    def test_telegram_alert_deduplication_above_below(self):
        """Ensure complementary [ABOVE] and [BELOW] rows for the same strike only fire 1 alert (best margin)."""
        from engine.alerts import AlertManager
        mgr = AlertManager(config_path="non_existent_test_cfg.json")
        dispatched = []
        mgr._dispatch_alert = lambda payload: dispatched.append(payload)

        spreads = [
            {
                "event_key": "ETH-2700-BELOW::poly-No",
                "title": "ETH $2,700 [BELOW] (2026-09-25)",
                "odds_a": 1.85,
                "odds_b": 2.10,
                "spread_after_fees": 0.125,
                "hedge_margin": -0.30,
                "is_arb": False,
            },
            {
                "event_key": "ETH-2700-ABOVE::poly-Yes",
                "title": "ETH $2,700 [ABOVE] (2026-09-25)",
                "odds_a": 1.85,
                "odds_b": 2.10,
                "spread_after_fees": 0.125,
                "hedge_margin": -0.02,
                "is_arb": False,
            },
        ]
        mgr.process_spreads(spreads)
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0]["title"], "ETH $2,700 [ABOVE] (2026-09-25)")

    def test_bybit_mixed_step_strike_grid_no_loss(self):
        """Task 1: Ensure BybitOddsCollector parses and retains mixed-step strikes ($10, $100, decimal) without loss or rounding."""
        import threading
        import time
        from collectors.bybit import BybitOddsCollector
        from normalizer.mapper import Normalizer

        collector = BybitOddsCollector.__new__(BybitOddsCollector)
        collector._lock = threading.Lock()
        collector.contracts = {}
        collector._refresh_tickers_from_rest = lambda: True

        raw_strikes = [2600, 2610, 2640, 2650, 2690, 2700, 2645.5]
        now_ts = time.time()
        collector.tickers = {}
        for idx, st in enumerate(raw_strikes):
            wp_val = round(0.75 - idx * 0.08, 4)
            pr_val = round(1.0 / wp_val, 4)
            sym = f"ETHUSDT-27DEC99-{st}-ABOVE"
            collector.tickers[sym] = {
                "symbol": sym,
                "wp": str(wp_val),
                "pr": str(pr_val),
                "_fetched_at": now_ts,
            }

        contracts = collector.fetch()
        self.assertEqual(len(contracts), len(raw_strikes))
        parsed_strikes = [c["strike"] for c in contracts]
        self.assertEqual(parsed_strikes, [float(s) for s in raw_strikes])
        # Verify decimal strike is preserved without integer truncation
        self.assertIn(2645.5, parsed_strikes)

        # Verify Normalizer.auto_match_crypto_targets matches 2700 (exact) and 2690 (within 1% of 2700) with proper exact_strike_match flag
        norm = Normalizer()
        poly_events = [{
            "platform": "polymarket",
            "market_id": "poly-eth-2700-Yes",
            "asset": "ETH",
            "contract_type": "Target",
            "strike_price": 2700.0,
            "settle_date": "2099-12-27",
            "settle_time": "2099-12-27T16:00:00Z",
            "outcome": "Yes",
            "direction": "ABOVE",
            "implied_probability": 0.25,
            "odds": 4.00,
        }]
        matched = norm.auto_match_crypto_targets(contracts, poly_events)
        matched_map = {m[0]["strike_price"]: m[0]["exact_strike_match"] for m in matched}
        self.assertTrue(matched_map.get(2700.0))
        self.assertFalse(matched_map.get(2690.0))

    def test_value_1leg_only_on_exact_strike_match(self):
        """Task 2: Value 1-leg must be allowed ONLY when Bybit and Polymarket strikes match 1-to-1."""
        engine = SpreadEngine()

        # Case 1: Differing strikes (ETH 2690 vs Poly 2700) -> exact_strike_match=False, can_show_value_1leg=False
        ea_diff = {
            "platform": "bybit_odds", "market_id": "ETHUSDT-27SEP26-2690-ABOVE",
            "asset": "ETH", "settle_date": "2026-09-27",
            "title": "ETH > 2690", "outcome": "ABOVE",
            "odds": 2.00, "implied_probability": 0.50,
            "strike": 2690.0, "expiration": "2026-09-27 08:00:00",
            "diff_hours": 8.0, "time_diff_hours": 8.0,
        }
        eb_diff = {
            "platform": "polymarket", "market_id": "poly-eth-2700-Yes",
            "asset": "ETH", "settle_date": "2026-09-27",
            "title": "ETH > 2700", "outcome": "YES",
            "odds": 2.85, "implied_probability": 0.35,
            "strike": 2700.0, "expiration": "2026-09-27 16:00:00",
            "diff_hours": 8.0, "time_diff_hours": 8.0,
        }
        res_diff = engine.process_matches([(ea_diff, eb_diff)])
        self.assertEqual(len(res_diff), 1)
        self.assertFalse(res_diff[0]["exact_strike_match"])
        self.assertFalse(res_diff[0]["can_show_value_1leg"])
        self.assertIn("Poly $2,700", res_diff[0]["title"])

        # Case 2: Identical strikes (ETH 2700 vs Poly 2700) -> exact_strike_match=True, can_show_value_1leg=True
        ea_exact = dict(ea_diff, market_id="ETHUSDT-27SEP26-2700-ABOVE", strike=2700.0, title="ETH > 2700")
        res_exact = engine.process_matches([(ea_exact, eb_diff)])
        self.assertEqual(len(res_exact), 1)
        self.assertTrue(res_exact[0]["exact_strike_match"])
        self.assertTrue(res_exact[0]["can_show_value_1leg"])
        self.assertNotIn("/ Poly", res_exact[0]["title"])

    def test_auto_paper_corridor_2x_settlement_and_gap_blocking(self):
        """Verify AutoPaperTrader blocks strike-gap deals, places corridor_2x deals, and settles 2x inside corridor vs 1x outside."""
        import tempfile
        import os
        import json
        import sqlite3
        from engine.auto_paper import AutoPaperTrader

        import sys
        if hasattr(sys.stdout, "reconfigure"):
            try:
                sys.stdout.reconfigure(encoding="utf-8")
            except Exception:
                pass

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test_spreads.db")
            health_path = os.path.join(tmpdir, "test_health.json")

            conn = sqlite3.connect(db_path)
            conn.execute('''
                CREATE TABLE spreads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT, title TEXT, platform_a TEXT, platform_b TEXT,
                    prob_a REAL, prob_b REAL, odds_a REAL, odds_b REAL,
                    spread_after_fees REAL, action_a TEXT, action_b TEXT,
                    stake_a REAL, stake_b REAL, is_arb INTEGER,
                    hedge_cost REAL, hedge_margin REAL, time_warning TEXT,
                    detected_at TEXT
                )
            ''')
            # 1) Corridor 2x deal (should be placed as corridor_2x)
            conn.execute('''
                INSERT INTO spreads (
                    event_key, title, platform_a, platform_b, prob_a, prob_b, odds_a, odds_b,
                    spread_after_fees, action_a, action_b, stake_a, stake_b, is_arb,
                    hedge_cost, hedge_margin, time_warning, detected_at
                ) VALUES (
                    'ETH-2690-ABOVE::poly-2700', 'ETH $2,690 / Poly $2,700 [ABOVE] (2099-09-27)',
                    'bybit_odds', 'polymarket', 0.50, 0.35, 2.00, 1.72,
                    0.149, 'Bybit ABOVE $2,690', 'Poly NO $2,700', 0.46, 0.54, 0,
                    1.08, -0.074, '🎯 Коридор 2x выигрыша: $2,690–$2,700', '2099-01-01T00:00:00+00:00'
                )
            ''')
            # 2) Strike gap deal (must be strictly blocked!)
            conn.execute('''
                INSERT INTO spreads (
                    event_key, title, platform_a, platform_b, prob_a, prob_b, odds_a, odds_b,
                    spread_after_fees, action_a, action_b, stake_a, stake_b, is_arb,
                    hedge_cost, hedge_margin, time_warning, detected_at
                ) VALUES (
                    'ETH-2710-ABOVE::poly-2700', 'ETH $2,710 / Poly $2,700 [ABOVE] (2099-09-27)',
                    'bybit_odds', 'polymarket', 0.45, 0.30, 2.22, 1.65,
                    0.149, 'Bybit ABOVE $2,710', 'Poly NO $2,700', 0.43, 0.57, 0,
                    1.05, -0.048, '⚠️ Зазор страйков $2,700–$2,710 (риск проигрыша обоих плеч!)', '2099-01-01T00:00:00+00:00'
                )
            ''')
            conn.commit()
            conn.close()

            bot = AutoPaperTrader(db_path=db_path)
            bot.sec_health_file = health_path
            bot.ticks_db_path = os.path.join(tmpdir, "no_ticks.db")
            bot.check_and_place_cross_trades()

            trades = bot.get_trades(mode="main")
            self.assertEqual(len(trades), 1)
            self.assertEqual(trades[0]["deal_type"], "corridor_2x")
            self.assertAlmostEqual(trades[0]["strike_price"], 2690.0)
            self.assertAlmostEqual(trades[0]["strike_b"], 2700.0)

            # Case A: Settle INSIDE corridor ($2,695 in [$2,690, $2,700]) -> 2x Jackpot!
            conn = sqlite3.connect(db_path)
            conn.execute("UPDATE paper_trades SET expiry_time = '2020-01-01T00:00:00+00:00'")
            conn.commit()
            conn.close()

            with open(health_path, "w", encoding="utf-8") as hf:
                json.dump({"prices": {"ETH": {"index": 2695.0}}, "windows": []}, hf)
            bot.settle_expired_trades()

            settled_inside = bot.get_trades(mode="main")[0]
            self.assertEqual(settled_inside["status"], "WON")
            # Payout = 2 * (5.0 / 1.08) = 9.26, Net Profit = +4.26
            self.assertAlmostEqual(settled_inside["payout"], 9.26, places=2)
            self.assertAlmostEqual(settled_inside["net_profit"], 4.26, places=2)
            self.assertIn("2X ДЖЕКПОТ", settled_inside["resolution_note"])

            # Case B: Settle OUTSIDE corridor ($2,750 > $2,700) -> 1 leg of 2 wins!
            conn = sqlite3.connect(db_path)
            conn.execute("UPDATE paper_trades SET status = 'OPEN', expiry_time = '2020-01-01T00:00:00+00:00'")
            conn.commit()
            conn.close()

            with open(health_path, "w", encoding="utf-8") as hf:
                json.dump({"prices": {"ETH": {"index": 2750.0}}, "windows": []}, hf)
            bot.settle_expired_trades()

            settled_outside = bot.get_trades(mode="main")[0]
            # Payout = 1 * (5.0 / 1.08) = 4.63, Net Profit = -0.37 (small hedge cost, NOT -$5.00!)
            self.assertAlmostEqual(settled_outside["payout"], 4.63, places=2)
            self.assertAlmostEqual(settled_outside["net_profit"], -0.37, places=2)
            self.assertIn("1 плечо из 2", settled_outside["resolution_note"])


if __name__ == "__main__":
    unittest.main()

