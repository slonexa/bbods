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


if __name__ == "__main__":
    unittest.main()


