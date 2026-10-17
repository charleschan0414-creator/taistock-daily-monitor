import json
import tempfile
import unittest
from pathlib import Path

from build_software_manifest import RELEASE_FILES
from taistock.v2_optimize import GRID, _net_metrics, _select_rows, optimize_v2


def fixture_backtest(holdout_return=None):
    dates = [f"2026-{1 + index // 28:02d}-{1 + index % 28:02d}" for index in range(100)]
    trades = []
    for index, day in enumerate(dates):
        good_return = 0.03 if index % 4 else -0.01
        if index >= 70 and holdout_return is not None:
            good_return = holdout_return
        for direction in ("long", "short"):
            trades.append({"signal_date": day, "code": f"{direction[0]}{index}G", "direction": direction,
                           "score": 90, "volume_rank": 10, "volume_growth_pct": 100,
                           "ma5_distance_pct": 2, "triggered": True, "return": good_return})
            trades.append({"signal_date": day, "code": f"{direction[0]}{index}B", "direction": direction,
                           "score": 50, "volume_rank": 100, "volume_growth_pct": 20,
                           "ma5_distance_pct": 10, "triggered": True, "return": -0.03})
    return {"signal_dates": dates, "trades": trades}


class V2OptimizationTests(unittest.TestCase):
    def test_desktop_release_includes_v2_optimizer_module(self):
        self.assertIn("taistock/v2_optimize.py", RELEASE_FILES)

    def test_net_metrics_deducts_each_round_trip_cost_in_both_scenarios(self):
        trades = [
            {"signal_date": "2026-01-01", "triggered": True, "return": 0.01},
            {"signal_date": "2026-01-01", "triggered": True, "return": -0.01},
            {"signal_date": "2026-01-01", "triggered": False, "return": None},
        ]

        standard = _net_metrics(trades, ["2026-01-01"], 0.00435)
        stress = _net_metrics(trades, ["2026-01-01"], 0.00535)

        self.assertEqual(standard["trades"], 2)
        self.assertAlmostEqual(standard["average_return"], -0.00435)
        self.assertAlmostEqual(stress["average_return"], -0.00535)
        self.assertAlmostEqual(standard["cumulative_return"], -0.00435)
        self.assertAlmostEqual(standard["profit_factor"], 0.00565 / 0.01435)
        self.assertEqual(
            set(standard),
            {"trades", "win_rate", "average_return", "profit_factor", "max_drawdown", "cumulative_return"},
        )

    def test_selects_each_direction_by_score_then_volume_rank_and_daily_limit(self):
        rows = [
            {"signal_date": "2026-01-01", "code": "A", "direction": "long", "score": 80,
             "volume_rank": 9, "volume_growth_pct": 100, "ma5_distance_pct": 2},
            {"signal_date": "2026-01-01", "code": "B", "direction": "long", "score": 90,
             "volume_rank": 20, "volume_growth_pct": 100, "ma5_distance_pct": 2},
            {"signal_date": "2026-01-01", "code": "C", "direction": "long", "score": 80,
             "volume_rank": 3, "volume_growth_pct": 100, "ma5_distance_pct": 2},
            {"signal_date": "2026-01-01", "code": "S", "direction": "short", "score": 99,
             "volume_rank": 1, "volume_growth_pct": 100, "ma5_distance_pct": -2},
        ]
        parameters = {"score_min": 50, "volume_rank_max": 100, "volume_growth_min": 20,
                      "ma5_distance_max": 3, "daily_limit": 3}

        selected = _select_rows(rows, "long", parameters)

        self.assertEqual([row["code"] for row in selected], ["B", "C", "A"])

    def test_optimizes_directions_on_first_70_days_and_uses_holdout_only_for_activation(self):
        good = optimize_v2(fixture_backtest())
        bad = optimize_v2(fixture_backtest(holdout_return=-0.03))

        self.assertEqual(good["method"]["split"], "first_70_signal_days_selection_last_30_activation_only")
        self.assertFalse(good["method"]["selection_uses_holdout_outcomes"])
        self.assertEqual(good["train_dates"]["days"], 70)
        self.assertEqual(good["holdout_dates"]["days"], 30)
        self.assertEqual(good["directions"]["long"]["selected_parameters"],
                         bad["directions"]["long"]["selected_parameters"])
        self.assertEqual(good["directions"]["short"]["selected_parameters"],
                         bad["directions"]["short"]["selected_parameters"])
        self.assertTrue(good["decision"]["enabled"])
        self.assertFalse(bad["decision"]["enabled"])
        for direction in ("long", "short"):
            scenario = good["directions"][direction]["holdout"]
            self.assertEqual(set(scenario), {"standard", "stress"})
            self.assertGreaterEqual(scenario["standard"]["trades"], 30)
            self.assertTrue(all(good["directions"][direction]["activation_checks"].values()))

    def test_report_contains_full_grid_costs_rolling_checks_and_daily_ohlc_limitations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v2_optimization.json"
            report = optimize_v2(fixture_backtest(), path)
            saved = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(report["version"], "2.1")
        self.assertEqual(report["costs"], {"standard": 0.00435, "stress": 0.00535})
        self.assertEqual(report["grid"], {key: list(values) for key, values in GRID.items()})
        self.assertEqual(report["candidate_count_per_direction"], 360)
        self.assertTrue(report["limitations"]["source_is_daily_ohlc"])
        self.assertTrue(report["limitations"]["not_true_intraday"])
        self.assertIn("不可宣稱13:20當沖", report["limitations"]["claims"])
        self.assertIn("不可宣稱60%勝率", report["limitations"]["claims"])
        self.assertEqual(saved["decision"], report["decision"])
        rolling = report["directions"]["long"]["rolling_20_signal_day_checks"]
        self.assertEqual(len(rolling["windows"]), 3)
        self.assertGreaterEqual(rolling["profitable_windows"], 2)
        self.assertTrue(rolling["passed"])

    def test_insufficient_rolling_window_samples_explicitly_disable_decision(self):
        backtest = fixture_backtest()
        holdout = set(backtest["signal_dates"][70:])
        for row in backtest["trades"]:
            if row["signal_date"] in holdout:
                row["triggered"] = False
                row["return"] = None

        report = optimize_v2(backtest)

        self.assertFalse(report["decision"]["enabled"])
        for direction in ("long", "short"):
            rolling = report["directions"][direction]["rolling_20_signal_day_checks"]
            self.assertFalse(rolling["data_sufficient"])
            self.assertFalse(rolling["passed"])
            self.assertIn("資料不足", rolling["reason"])


if __name__ == "__main__":
    unittest.main()
