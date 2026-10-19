import math
import unittest

from taistock.technical import calculate_technical_research


class TechnicalResearchTests(unittest.TestCase):
    def test_calculates_t_day_only_research_indicators(self):
        bars = [
            {"date": f"2026-10-{day:02d}", "open": close - 1, "high": close + 1,
             "low": close - 2, "close": close}
            for day, close in zip(range(10, 16), (10, 11, 12, 11, 13, 14))
        ]
        result = calculate_technical_research(bars, "2026-10-15")
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["score_effect"], "none")
        self.assertAlmostEqual(result["indicators"]["rsi_5"], 83.333333, places=5)
        self.assertAlmostEqual(result["indicators"]["atr_5_pct"], 21.428571, places=5)
        self.assertAlmostEqual(result["indicators"]["close_vs_prior5_high_pct"], 0.0)
        self.assertTrue(all(math.isfinite(value) for value in result["indicators"].values()))

    def test_requires_six_complete_sessions_and_never_imputes(self):
        result = calculate_technical_research([], "2026-10-15")
        self.assertEqual(result["status"], "insufficient_history")
        self.assertTrue(all(value is None for value in result["indicators"].values()))

    def test_flat_prices_are_finite_and_neutral(self):
        bars = [{"date": str(i), "open": 10, "high": 10, "low": 10, "close": 10} for i in range(6)]
        result = calculate_technical_research(bars, "5")
        self.assertEqual(result["indicators"]["rsi_5"], 50.0)
        self.assertEqual(result["indicators"]["atr_5_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()