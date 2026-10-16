import json
import tempfile
import unittest
from pathlib import Path

from taistock.strategy_optimize import (
    _train_robust,
    apply_strategy_gate,
    load_strategy_gate,
    optimize_strategy,
)


def fixture_backtest(test_high_return=0.04):
    dates = [f"2026-{1 + index // 28:02d}-{1 + index % 28:02d}" for index in range(100)]
    trades = []
    for index, day in enumerate(dates):
        high_return = 0.04 if index < 70 else test_high_return
        for suffix in ("A", "B"):
            trades.append({"signal_date": day, "code": f"{index:02d}{suffix}", "direction": "long",
                           "score": 80, "volume_growth_pct": 100, "ma5_distance_pct": 3,
                           "institutional_abs_lots": 5000, "status": "settled", "triggered": True,
                           "return": high_return})
        trades.append({"signal_date": day, "code": f"{index:02d}L", "direction": "long",
                       "score": 40, "volume_growth_pct": 30, "ma5_distance_pct": 1,
                       "institutional_abs_lots": 500, "status": "settled", "triggered": True,
                       "return": -0.02})
        trades.append({"signal_date": day, "code": f"{index:02d}S", "direction": "short",
                       "score": 80, "volume_growth_pct": 100, "ma5_distance_pct": 3,
                       "institutional_abs_lots": 5000, "status": "settled", "triggered": True,
                       "return": -0.01})
    return {"signal_dates": dates, "trades": trades}


class StrategyOptimizationTests(unittest.TestCase):
    def test_training_candidate_requires_pf_above_one_and_drawdown_within_five_points(self):
        baseline = {"max_drawdown": -0.10}
        self.assertTrue(_train_robust({"profit_factor": 1.1, "max_drawdown": -0.15}, baseline))
        self.assertFalse(_train_robust({"profit_factor": 0.9, "max_drawdown": -0.05}, baseline))
        self.assertFalse(_train_robust({"profit_factor": 1.2, "max_drawdown": -0.151}, baseline))

    def test_uses_first_70_days_for_selection_and_last_30_only_for_holdout_decision(self):
        report = optimize_strategy(fixture_backtest())
        self.assertEqual(report["method"]["split"], "first_70_signal_days_train_last_30_holdout")
        self.assertEqual(report["train_dates"], {"from": "2026-01-01", "to": "2026-03-14", "days": 70})
        self.assertEqual(report["test_dates"], {"from": "2026-03-15", "to": "2026-04-16", "days": 30})
        self.assertEqual(report["minimum_samples"], {"train_trades": 100, "test_trades": 30})
        self.assertGreater(report["candidate_count"], 1)
        self.assertGreaterEqual(report["candidate"]["train"]["trades"], 100)
        self.assertGreaterEqual(report["candidate"]["test"]["trades"], 30)
        self.assertTrue(report["decision"]["enabled"])
        self.assertFalse(report["selected_gate"]["short_enabled"])
        self.assertGreater(report["candidate"]["test"]["average_return"],
                           report["baseline"]["test"]["average_return"])

    def test_holdout_outcomes_do_not_change_training_selected_gate(self):
        good = optimize_strategy(fixture_backtest(test_high_return=0.04))
        bad = optimize_strategy(fixture_backtest(test_high_return=-0.04))
        self.assertEqual(good["selected_gate"], bad["selected_gate"])
        self.assertTrue(good["decision"]["enabled"])
        self.assertFalse(bad["decision"]["enabled"])
        self.assertEqual(bad["decision"]["applied_gate"], bad["baseline_gate"])

    def test_disabled_gate_is_noop_and_enabled_gate_filters_directions_and_thresholds(self):
        rows = [{"code": "A", "signal_direction": "long", "score": 80, "volume_growth_pct": 60},
                {"code": "B", "signal_direction": "long", "score": 40, "volume_growth_pct": 60},
                {"code": "C", "signal_direction": "short", "score": 90, "volume_growth_pct": 60}]
        disabled = {"enabled": False, "applied_gate": {"long_score_min": 0, "short_score_min": 0,
                                                         "short_enabled": True, "volume_growth_min": 20}}
        self.assertEqual(apply_strategy_gate(rows, disabled), rows)
        enabled = {"enabled": True, "applied_gate": {"long_score_min": 70, "short_score_min": 70,
                                                       "short_enabled": False, "volume_growth_min": 50}}
        self.assertEqual([row["code"] for row in apply_strategy_gate(rows, enabled)], ["A"])

    def test_writes_and_loads_auditable_report(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "strategy_optimization.json"
            report = optimize_strategy(fixture_backtest(), path)
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["version"], report["version"])
            state = load_strategy_gate(Path(directory))
            self.assertEqual(state["enabled"], report["decision"]["enabled"])
            self.assertIn("status", state)
            self.assertIn("applied_gate", state)


if __name__ == "__main__":
    unittest.main()
