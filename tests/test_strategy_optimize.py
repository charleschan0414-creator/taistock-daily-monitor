import json
import tempfile
import unittest
from pathlib import Path

from taistock.strategy_optimize import (
    BASELINE_GATE,
    STANDARD_COST,
    STRESS_COST,
    VERSION,
    _holdout_checks,
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
    def test_holdout_requires_win_rate_and_pf_to_improve_over_incumbent(self):
        candidate = {"trades": 40, "average_return": 0.01, "profit_factor": 1.30,
                     "win_rate": 0.55, "max_drawdown": -0.10}
        baseline = {"profit_factor": 1.10, "win_rate": 0.50, "max_drawdown": -0.10}
        stress = {"average_return": 0.002, "profit_factor": 1.05}
        checks = _holdout_checks(candidate, baseline, stress)
        self.assertTrue(checks["standard_win_rate_above_incumbent"])
        self.assertTrue(checks["standard_profit_factor_above_incumbent"])

        candidate["win_rate"] = 0.49
        checks = _holdout_checks(candidate, baseline, stress)
        self.assertFalse(checks["standard_win_rate_above_incumbent"])

        candidate.update({"win_rate": 0.55, "profit_factor": 1.10})
        self.assertFalse(_holdout_checks(candidate, baseline, stress)["standard_profit_factor_above_incumbent"])
        candidate["profit_factor"] = None
        self.assertTrue(_holdout_checks(candidate, baseline, stress)["standard_profit_factor_above_incumbent"])
        baseline["profit_factor"] = None
        self.assertFalse(_holdout_checks(candidate, baseline, stress)["standard_profit_factor_above_incumbent"])
    def test_frozen_protective_gate_is_applied_without_claiming_cost_validation(self):
        frozen_gate = {"long_score_min": 70, "short_score_min": 70,
                       "short_enabled": True, "volume_growth_min": 20}
        report = {"version": VERSION, "decision": {"enabled": True,
                  "upgrade_approved": False, "cost_validated": False,
                  "applied_mode": "incumbent_protective", "applied_gate": frozen_gate,
                  "status": "未經新當沖模式成本驗證"}}
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "strategy_optimization.json").write_text(
                json.dumps(report), encoding="utf-8")
            state = load_strategy_gate(Path(directory))

        self.assertEqual(state["applied_gate"], frozen_gate)
        self.assertEqual(state["applied_mode"], "incumbent_protective")
        self.assertFalse(state["cost_validated"])
        rows = [{"code": "65", "signal_direction": "long", "score": 65, "volume_growth_pct": 30},
                {"code": "75", "signal_direction": "long", "score": 75, "volume_growth_pct": 30}]
        self.assertEqual([row["code"] for row in apply_strategy_gate(rows, state)], ["75"])

    def test_no_robust_training_candidate_returns_disabled_auditable_report(self):
        backtest = fixture_backtest()
        for row in backtest["trades"]:
            row["return"] = -0.02

        report = optimize_strategy(backtest)

        self.assertEqual(report["eligible_train_candidate_count"], 0)
        self.assertEqual(report["selected_gate"], BASELINE_GATE)
        self.assertFalse(report["decision"]["enabled"])
        self.assertFalse(report["decision"]["upgrade_approved"])
        self.assertFalse(report["decision"]["cost_validated"])
        self.assertEqual(report["decision"]["applied_mode"], "incumbent_protective")
        self.assertIn("沒有符合訓練穩健條件", report["decision"]["status"])

    def test_explicit_incumbent_is_used_as_baseline_and_protective_fallback(self):
        backtest = fixture_backtest()
        for row in backtest["trades"]:
            row["return"] = -0.02
        incumbent = {"long_score_min": 70, "short_score_min": 70,
                     "short_enabled": False, "volume_growth_min": 50}

        report = optimize_strategy(backtest, incumbent_gate=incumbent)

        self.assertEqual(report["method"]["incumbent"], incumbent)
        self.assertEqual(report["baseline_gate"], incumbent)
        self.assertEqual(report["decision"]["applied_gate"], incumbent)

    def test_v2_report_preserves_gross_standard_and_stress_metrics(self):
        report = optimize_strategy(fixture_backtest())
        self.assertGreaterEqual(VERSION, 2)
        self.assertEqual(STANDARD_COST, 0.00435)
        self.assertEqual(STRESS_COST, 0.00535)
        self.assertEqual(BASELINE_GATE, {"long_score_min": 55, "short_score_min": 70,
                                         "short_enabled": True, "volume_growth_min": 20})
        self.assertEqual(report["costs"], {"standard": STANDARD_COST, "stress": STRESS_COST})
        self.assertEqual(report["method"]["selection_metric"], "standard_cost_after")
        self.assertEqual(report["baseline"]["train"]["gross"]["signals"], 210)
        for subject in ("baseline", "candidate"):
            for split in ("train", "test"):
                self.assertEqual(set(report[subject][split]), {"gross", "standard", "stress"})
                gross = report[subject][split]["gross"]["average_return"]
                standard = report[subject][split]["standard"]["average_return"]
                stress = report[subject][split]["stress"]["average_return"]
                self.assertAlmostEqual(gross - standard, STANDARD_COST)
                self.assertAlmostEqual(gross - stress, STRESS_COST)

    def test_gross_positive_candidate_fails_cost_gate_and_keeps_protective_incumbent(self):
        report = optimize_strategy(fixture_backtest(test_high_return=0.004))
        decision = report["decision"]
        self.assertGreater(report["candidate"]["test"]["gross"]["average_return"], 0)
        self.assertLess(report["candidate"]["test"]["standard"]["average_return"], 0)
        self.assertFalse(decision["upgrade_approved"])
        self.assertFalse(decision["cost_validated"])
        self.assertEqual(decision["applied_mode"], "incumbent_protective")
        self.assertEqual(decision["applied_gate"], BASELINE_GATE)
        self.assertIn("保留既有保護性篩選｜成本後升級未通過", decision["status"])
        rows = [{"code": "low", "signal_direction": "long", "score": 40, "volume_growth_pct": 30},
                {"code": "kept", "signal_direction": "long", "score": 60, "volume_growth_pct": 30}]
        self.assertEqual([row["code"] for row in apply_strategy_gate(rows, decision)], ["kept"])

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
        self.assertGreaterEqual(report["candidate"]["train"]["standard"]["trades"], 100)
        self.assertGreaterEqual(report["candidate"]["test"]["standard"]["trades"], 30)
        self.assertTrue(report["decision"]["enabled"])
        self.assertTrue(report["decision"]["upgrade_approved"])
        self.assertTrue(report["decision"]["cost_validated"])
        self.assertEqual(report["decision"]["applied_mode"], "candidate")
        self.assertTrue(all(report["decision"]["checks"].values()))
        self.assertFalse(report["selected_gate"]["short_enabled"])
        self.assertGreater(report["candidate"]["test"]["standard"]["average_return"], 0)
        standard_pf = report["candidate"]["test"]["standard"]["profit_factor"]
        stress_pf = report["candidate"]["test"]["stress"]["profit_factor"]
        self.assertTrue(standard_pf is None or standard_pf > 1)
        self.assertGreater(report["candidate"]["test"]["stress"]["average_return"], 0)
        self.assertTrue(stress_pf is None or stress_pf > 1)

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
            self.assertEqual(state["applied_mode"], report["decision"]["applied_mode"])
            self.assertEqual(state["cost_validated"], report["decision"]["cost_validated"])
            self.assertIn("status", state)
            self.assertIn("applied_gate", state)

    def test_load_rejects_legacy_gross_enabled_report_and_uses_incumbent(self):
        legacy = {"version": 1, "decision": {"enabled": True,
                  "applied_gate": {"long_score_min": 0, "short_score_min": 0,
                                   "short_enabled": True, "volume_growth_min": 20},
                  "status": "已啟用"}}
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "strategy_optimization.json").write_text(
                json.dumps(legacy), encoding="utf-8")
            state = load_strategy_gate(Path(directory))
        self.assertFalse(state["enabled"])
        self.assertFalse(state["cost_validated"])
        self.assertEqual(state["applied_mode"], "incumbent_protective")
        self.assertEqual(state["applied_gate"], BASELINE_GATE)


if __name__ == "__main__":
    unittest.main()
