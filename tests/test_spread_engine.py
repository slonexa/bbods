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

if __name__ == "__main__":
    unittest.main()
