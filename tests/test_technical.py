import math
import unittest

from taistock.technical import (analyze_candlestick_window, assess_candlestick_conflict,
                                calculate_technical_research)


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
        self.assertAlmostEqual(result["indicators"]["candle_body_pct"], 100 / 13, places=5)
        self.assertAlmostEqual(result["indicators"]["close_change_pct"], 100 / 13, places=5)
        self.assertTrue(all(math.isfinite(value) for value in result["indicators"].values()))
        self.assertEqual(len(result["recent_candles"]), 5)
        self.assertEqual(result["recent_candles"][-1]["date"], "2026-10-15")

    def test_three_day_pattern_uses_last_three_of_five_without_affecting_score(self):
        candles = [
            {"date": "2026-10-09", "open": 10.0, "high": 10.3, "low": 9.8, "close": 10.1},
            {"date": "2026-10-10", "open": 10.1, "high": 10.2, "low": 9.9, "close": 10.0},
            {"date": "2026-10-13", "open": 10.0, "high": 10.8, "low": 9.9, "close": 10.7},
            {"date": "2026-10-14", "open": 10.4, "high": 11.4, "low": 10.3, "close": 11.3},
            {"date": "2026-10-15", "open": 11.0, "high": 12.0, "low": 10.9, "close": 11.9},
        ]
        result = analyze_candlestick_window({"status": "available", "recent_candles": candles}, "long")
        self.assertEqual(result["pattern_name"], "三白兵")
        self.assertEqual(result["pattern_sessions"], 3)
        self.assertEqual(result["chart_sessions"], 5)
        self.assertEqual(result["bias"], "bullish")
        self.assertEqual(result["score_effect"], "none")
        self.assertEqual(result["filter_effect"], "none")
        self.assertIn("連續三根紅K", result["explanation"])

    def test_recognizes_playlist_core_three_day_bullish_and_bearish_patterns(self):
        prefix = [
            {"date": "1", "open": 10, "high": 10.2, "low": 9.8, "close": 10.0},
            {"date": "2", "open": 10, "high": 10.2, "low": 9.8, "close": 10.0},
        ]
        cases = {
            "晨星": [
                {"date": "3", "open": 12, "high": 12.2, "low": 9.8, "close": 10},
                {"date": "4", "open": 9.9, "high": 10.2, "low": 9.7, "close": 10.0},
                {"date": "5", "open": 10.1, "high": 11.5, "low": 10.0, "close": 11.2},
            ],
            "夜星": [
                {"date": "3", "open": 10, "high": 12.2, "low": 9.8, "close": 12},
                {"date": "4", "open": 12.1, "high": 12.3, "low": 11.9, "close": 12.0},
                {"date": "5", "open": 11.9, "high": 12.0, "low": 10.4, "close": 10.7},
            ],
            "內困三日翻紅": [
                {"date": "3", "open": 12, "high": 12.2, "low": 9.8, "close": 10},
                {"date": "4", "open": 10.4, "high": 11.5, "low": 10.3, "close": 11.4},
                {"date": "5", "open": 11.3, "high": 12.4, "low": 11.2, "close": 12.2},
            ],
            "外側三日下跌": [
                {"date": "3", "open": 10, "high": 11.2, "low": 9.8, "close": 11},
                {"date": "4", "open": 11.2, "high": 11.3, "low": 9.5, "close": 9.7},
                {"date": "5", "open": 9.8, "high": 9.9, "low": 9.0, "close": 9.1},
            ],
            "三烏鴉": [
                {"date": "3", "open": 12.0, "high": 12.1, "low": 11.1, "close": 11.2},
                {"date": "4", "open": 11.7, "high": 11.8, "low": 10.7, "close": 10.8},
                {"date": "5", "open": 11.3, "high": 11.4, "low": 10.2, "close": 10.3},
            ],
        }
        for expected, last3 in cases.items():
            with self.subTest(expected=expected):
                result = analyze_candlestick_window(
                    {"status": "available", "recent_candles": prefix + last3}, "long")
                self.assertEqual(result["pattern_name"], expected)
                self.assertEqual(result["score_effect"], "none")

    def test_flags_bullish_daily_candle_against_short_without_filtering(self):
        research = {"status": "available", "indicators": {
            "candle_body_pct": 0.82, "close_change_pct": 1.37,
            "gap_pct": 0.55, "close_location_pct": 20.97,
        }}
        conflict = assess_candlestick_conflict(research, "short")
        self.assertEqual(conflict["status"], "direction_conflict")
        self.assertEqual(conflict["score_effect"], "none")
        self.assertEqual(conflict["filter_effect"], "none")
        self.assertIn("紅K", conflict["message"])

    def test_marks_high_volume_black_long_conflict_as_high_risk_without_changing_signal(self):
        research = {"status": "available", "indicators": {
            "candle_body_pct": -3.99, "close_change_pct": -1.93,
            "gap_pct": 2.15, "close_location_pct": 17.95,
        }}
        conflict = assess_candlestick_conflict(research, "long", volume_growth_pct=346.81)
        self.assertEqual(conflict["status"], "direction_conflict")
        self.assertEqual(conflict["risk_level"], "high")
        self.assertEqual(conflict["shadow_candidate"], "long_black_volume_veto_v1")
        self.assertEqual(conflict["score_effect"], "none")
        self.assertEqual(conflict["filter_effect"], "none")
        self.assertIn("爆量收黑K", conflict["message"])
        self.assertIn("突破觸發價", conflict["message"])

    def test_high_volume_conflict_requires_finite_real_threshold_value(self):
        research = {"status": "available", "indicators": {
            "candle_body_pct": -3.0, "close_change_pct": -2.0,
        }}
        huge = 10 ** 10000
        cases = (("below", 99.99, "normal"), ("threshold", 100.0, "high"),
                 ("nan", float("nan"), "normal"), ("positive infinity", float("inf"), "normal"),
                 ("negative infinity", float("-inf"), "normal"), ("huge integer", huge, "high"),
                 ("huge negative integer", -huge, "normal"), ("boolean", True, "normal"),
                 ("string", "346.81", "normal"))
        for label, value, expected in cases:
            with self.subTest(case=label):
                result = assess_candlestick_conflict(research, "long", volume_growth_pct=value)
                self.assertEqual(result["risk_level"], expected)

    def test_requires_six_complete_sessions_and_never_imputes(self):
        result = calculate_technical_research([], "2026-10-15")
        self.assertEqual(result["status"], "insufficient_history")
        self.assertTrue(all(value is None for value in result["indicators"].values()))

    def test_invalid_ohlc_never_crashes_window_analysis(self):
        bars = [{"date": str(i), "open": 10, "high": 11, "low": 9, "close": 10}
                for i in range(6)]
        bars[0]["close"] = 0
        research = calculate_technical_research(bars, "5")
        self.assertEqual(research["status"], "insufficient_history")
        self.assertEqual(research["missing_reason"], "invalid_ohlc")

        malformed = [{"date": str(i), "open": 10, "high": 11, "low": 9, "close": 10}
                     for i in range(5)]
        malformed[0]["close"] = 0
        result = analyze_candlestick_window(
            {"status": "available", "recent_candles": malformed}, "long")
        self.assertEqual(result["status"], "invalid_ohlc")
        self.assertEqual(result["score_effect"], "none")
        self.assertEqual(result["filter_effect"], "none")

    def test_overflow_tiny_rounding_and_nonfinite_indicators_fail_closed(self):
        valid = [{"date": str(i), "open": 10, "high": 11, "low": 9, "close": 10}
                 for i in range(6)]
        oversized = [dict(row) for row in valid]
        oversized[0]["close"] = 10 ** 10000
        self.assertEqual(calculate_technical_research(oversized, "5")["missing_reason"],
                         "invalid_ohlc")

        tiny = [{"date": str(i), "open": 1e-7, "high": 2e-7,
                 "low": 1e-7, "close": 1e-7} for i in range(6)]
        self.assertEqual(calculate_technical_research(tiny, "5")["missing_reason"],
                         "invalid_ohlc")

        extreme = [dict(row) for row in valid]
        extreme[-1].update({"open": 1.0, "high": 1e308, "low": 1.0, "close": 1.0})
        self.assertEqual(calculate_technical_research(extreme, "5")["missing_reason"],
                         "invalid_ohlc")

    def test_flat_prices_are_finite_and_neutral(self):
        bars = [{"date": str(i), "open": 10, "high": 10, "low": 10, "close": 10} for i in range(6)]
        result = calculate_technical_research(bars, "5")
        self.assertEqual(result["indicators"]["rsi_5"], 50.0)
        self.assertEqual(result["indicators"]["atr_5_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()