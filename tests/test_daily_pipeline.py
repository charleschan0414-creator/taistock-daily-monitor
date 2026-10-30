import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import daily_pipeline


class DailyPipelineTests(unittest.TestCase):
    def test_market_closure_carries_previous_status_without_running_backtest(self):
        previous = {
            "status": "success", "completed_at": "2026-10-22T16:47:33+08:00",
            "data_date": "2026-10-22", "backtest": {"marker": "preserved"},
            "strategy_campaign": {"unique_signal_dates": ["2026-10-22"]},
            "health": {"old": True},
        }
        paused = {
            "data_date": "2026-10-22",
            "market_session": {"status": "paused_no_new_official_session",
                               "requested_date": "2026-10-23",
                               "latest_official_date": "2026-10-22"},
            "execution_eligibility": {"eligible_count": 0,
                                      "execution_for_signal_date": "paused_no_new_official_session"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "daily_status.json").write_text(json.dumps(previous), encoding="utf-8")
            with patch.object(daily_pipeline, "DATA", data), \
                 patch.object(daily_pipeline, "carry_forward_if_market_closed", return_value=paused), \
                 patch.object(daily_pipeline, "run_backtest") as backtest:
                self.assertEqual(daily_pipeline.main(), 0)
            status = json.loads((data / "daily_status.json").read_text(encoding="utf-8"))
        backtest.assert_not_called()
        self.assertEqual(status["status"], "success")
        self.assertEqual(status["data_date"], "2026-10-22")
        self.assertEqual(status["backtest"], {"marker": "preserved"})
        self.assertEqual(status["market_session"]["requested_date"], "2026-10-23")
        self.assertEqual(status["run_mode"], "market_closed_carry_forward")
        self.assertNotIn("old", status["health"])

    def test_campaign_id_is_only_attached_inside_campaign_window(self):
        config = {"id": "campaign-a", "start_date": "2026-10-20", "end_date": "2026-11-20"}
        self.assertIsNone(daily_pipeline.campaign_id_for_date(config, "2026-10-19"))
        self.assertEqual(daily_pipeline.campaign_id_for_date(config, "2026-10-20"), "campaign-a")
        self.assertEqual(daily_pipeline.campaign_id_for_date(config, "2026-11-20"), "campaign-a")
        self.assertIsNone(daily_pipeline.campaign_id_for_date(config, "2026-11-21"))

    def test_campaign_rejects_cross_campaign_and_out_of_window_pending_candidate(self):
        config = {
            "id": "campaign-a", "start_date": "2026-10-20", "end_date": "2026-11-20",
            "objective": "improve_standard_win_rate_and_profit_factor",
            "baseline": {"win_rate": .35, "profit_factor": .49, "average_return": -.01},
        }
        backtest = {"metrics_scenarios": {"standard": {
            "win_rate": .40, "profit_factor": .60, "average_return": -.005,
        }}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "campaign.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with patch.object(daily_pipeline, "CAMPAIGN_CONFIG", path):
                wrong_id = daily_pipeline.load_campaign_status(
                    backtest, "2026-10-21",
                    {"campaign_id": "campaign-b", "proposed_on": "2026-10-20", "forward_signal_days": 20})
                too_early = daily_pipeline.load_campaign_status(
                    backtest, "2026-10-21",
                    {"campaign_id": "campaign-a", "proposed_on": "2026-10-19", "forward_signal_days": 20})
        self.assertFalse(wrong_id["candidate_found"])
        self.assertFalse(too_early["candidate_found"])

    def test_today_result_separates_triggered_from_untriggered_and_applies_cost(self):
        backtest = {"costs": {"standard": .00435}, "trades": [
            {"signal_date": "2026-10-16", "code": "A", "name": "甲", "direction": "long",
             "status": "settled", "triggered": True, "exit_date": "2026-10-19", "gross_return": .10},
            {"signal_date": "2026-10-16", "code": "B", "name": "乙", "direction": "short",
             "status": "settled", "triggered": True, "exit_date": "2026-10-19", "gross_return": -.05},
            {"signal_date": "2026-10-16", "code": "C", "name": "丙", "direction": "long",
             "status": "untriggered", "triggered": False},
        ]}
        gate = {"long_score_min": 0, "short_score_min": 0, "short_enabled": True,
                "volume_growth_min": 0}
        result = daily_pipeline.build_today_result(backtest, gate)
        self.assertEqual((result["signals"], result["triggered"], result["untriggered"]), (3, 2, 1))
        self.assertEqual(result["trade_date"], "2026-10-19")
        self.assertAlmostEqual(result["standard"]["average_return"], .02065)
        self.assertIsNone(result["items"][2]["net_return_standard"])

    def test_validate_outputs_accepts_complete_daily_result(self):
        backtest = {"period": {"from": "2026-01-01", "to": "2026-10-15"}, "metrics": {"signal_days": 100}}
        latest = {
            "data_date": "2026-10-15", "top100_count": 100,
            "history_success_count": 100, "errors": [],
            "long_top10": [{"entry_trigger": 101, "stop_loss": 98, "target1": 105, "risk_pct": 3}],
            "short_top10": [{"entry_trigger": 99, "stop_loss": 102, "target1": 95, "risk_pct": 3}],
        }
        daily_pipeline.validate_outputs(backtest, latest, "2026-10-15")

    def test_main_runs_steps_in_order_and_writes_success_audit(self):
        portfolio = {"initial_capital": 1_000_000, "final_capital": 1_010_000,
                     "net_return": .01, "max_drawdown": -.02, "realized_only_equity": True}
        backtest = {"period": {"from": "2026-05-01", "to": "2026-10-07"}, "metrics": {"signal_days": 100, "win_rate": .5},
                    "portfolio_standard": portfolio,
                    "data_collection": {"latest_complete_date": "2026-10-15"}}
        report = {
            "baseline": {"train": {"win_rate": .4}, "test": {"win_rate": .3}},
            "candidate": {"train": {"win_rate": .5}, "test": {"win_rate": .4}},
            "decision": {"enabled": True, "status": "enabled", "applied_gate": {"long_score_min": 0}},
        }
        v2_holdout = {"standard": {"trades": 6, "win_rate": .4, "average_return": -.01,
                                    "profit_factor": .64, "max_drawdown": -.05,
                                    "cumulative_return": -.02},
                      "stress": {"trades": 6, "win_rate": .3, "average_return": -.011,
                                 "profit_factor": .55, "max_drawdown": -.06,
                                 "cumulative_return": -.03}}
        v2 = {"version": "2.1", "costs": {"standard": .00435, "stress": .00535},
              "decision": {"enabled": False, "status": "research only"},
              "limitations": {"source_is_daily_ohlc": True, "not_true_intraday": True},
              "directions": {"long": {"passed": False, "holdout": v2_holdout},
                             "short": {"passed": False, "holdout": v2_holdout}}}
        latest = {
            "data_date": "2026-10-15", "top100_count": 100, "history_success_count": 100,
            "errors": [], "long_top10": [], "short_top10": [],
            "optimization": {"new_weights": {"volume_growth_pct": .35}},
        }
        market_history = {
            "status": "ok", "snapshot_id": "2026-10-17:post_close",
            "regime": {"snapshot_count": 1, "insufficient_history": True,
                       "affects_stock_scores": False},
            "affects_stock_scores": False,
        }
        order = []
        effective_gates = []
        def run_update(notify):
            order.append(("update", notify))
            effective = json.loads((Path(tmp) / "strategy_optimization.json").read_text(encoding="utf-8"))
            effective_gates.append(effective["decision"]["applied_gate"])
            (Path(tmp) / "latest.json").write_text(json.dumps(latest), encoding="utf-8")
            return latest
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(daily_pipeline, "DATA", Path(tmp)), \
             patch.object(daily_pipeline, "run_backtest", side_effect=lambda days: order.append(("backtest", days)) or backtest), \
             patch.object(daily_pipeline, "optimize_v2", side_effect=lambda source, output: order.append(("v2", source, output.name)) or v2), \
             patch.object(daily_pipeline, "run_strategy_optimization",
                          side_effect=lambda **kwargs: order.append(("optimize", kwargs)) or report), \
             patch.object(daily_pipeline, "update", side_effect=run_update), \
             patch.object(daily_pipeline, "record_market_history", return_value=market_history):
            self.assertEqual(daily_pipeline.main(), 0)
            status = json.loads((Path(tmp) / "daily_status.json").read_text(encoding="utf-8"))
            history = [json.loads(line) for line in (Path(tmp) / "strategy_history.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(order, [("backtest", 100), ("v2", backtest, "v2_optimization.json"),
                                 ("optimize", {"backtest": backtest, "incumbent_gate": None,
                                               "write_output": False}),
                                 ("update", False)])
        self.assertEqual(status["status"], "success")
        self.assertIn("health", status)
        self.assertIn(status["health"]["overall"], {"ok", "warning"})
        self.assertNotIn("raw_daily_status", status["health"])
        self.assertEqual(status["data_date"], "2026-10-15")
        self.assertEqual(status["backtest"]["metrics"], backtest["metrics"])
        self.assertEqual(status["backtest"]["metrics_scenarios"], {"gross": backtest["metrics"]})
        self.assertEqual(status["backtest"]["costs"],
                         {"gross": 0.0, "standard": .00435, "stress": .00535})
        self.assertEqual(status["backtest"]["portfolio_standard"], portfolio)
        self.assertEqual(effective_gates, [{
            "long_score_min": 55, "short_score_min": 70,
            "short_enabled": True, "volume_growth_min": 20,
        }])
        self.assertEqual(status["strategy_freeze"]["forward_signal_days"], 0)
        self.assertEqual(status["strategy_freeze"]["minimum_forward_signal_days"], 20)
        self.assertTrue(status["strategy_freeze"]["not_holdout_reuse"])
        self.assertFalse(status["strategy_freeze"]["cost_validated"])
        self.assertEqual(status["strategy_freeze"]["execution_model"],
                         "next_day_intraday_forced_close_v1")
        self.assertEqual(status["v2_analysis"]["decision"],
                         {"enabled": False, "status": "research only"})
        self.assertEqual(status["v2_analysis"]["costs"],
                         {"standard": .00435, "stress": .00535})
        self.assertEqual(status["v2_analysis"]["directions"]["long"],
                         {"passed": False, "holdout": v2_holdout})
        self.assertTrue(status["v2_analysis"]["limitations"]["source_is_daily_ohlc"])
        self.assertEqual(status["market_history"], market_history)
        self.assertFalse(status["market_history"]["affects_stock_scores"])
        self.assertEqual(history[0]["applied_gate"], {
            "long_score_min": 55, "short_score_min": 70,
            "short_enabled": True, "volume_growth_min": 20,
        })

    def test_market_history_exception_is_isolated_and_keeps_old_regime_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            old = {"snapshot_count": 8, "insufficient_history": True,
                   "affects_stock_scores": False}
            (data / "market_regime.json").write_text(json.dumps(old), encoding="utf-8")
            with patch.object(daily_pipeline, "DATA", data), \
                 patch.object(daily_pipeline, "capture_market_history", side_effect=ValueError("corrupt history")):
                result = daily_pipeline.record_market_history(client=object(), clock=lambda: None)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["history_snapshot"]["status"], "error")
        self.assertIn("corrupt history", result["error"])
        self.assertEqual(result["regime"], old)
        self.assertFalse(result["affects_stock_scores"])

    def test_update_uses_frozen_gate_and_failure_does_not_replace_registry(self):
        gate_a = {"long_score_min": 55, "short_score_min": 70,
                  "short_enabled": True, "volume_growth_min": 20}
        gate_b = {"long_score_min": 70, "short_score_min": 70,
                  "short_enabled": False, "volume_growth_min": 50}
        registry = {
            "active_gate": gate_a, "strategy_version": 3, "activated_on": "2026-10-01",
            "minimum_forward_signal_days": 20, "forward_signal_days": 5,
            "last_observed_date": "2026-10-14", "status": "forward_observation",
            "forward_observation": {"not_holdout_reuse": True},
        }
        backtest = {"period": {}, "metrics": {"signal_days": 100},
                    "data_collection": {"latest_complete_date": "2026-10-15"}}
        report = {
            "baseline": {"train": {}, "test": {}}, "candidate": {"train": {}, "test": {}},
            "decision": {"enabled": True, "upgrade_approved": True, "cost_validated": True,
                         "applied_mode": "candidate", "applied_gate": gate_b, "status": "approved"},
        }
        seen = []
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "strategy_registry.json").write_text(json.dumps(registry), encoding="utf-8")
            old_report = {"decision": {"applied_gate": gate_a}, "marker": "previous"}
            (data / "strategy_optimization.json").write_text(json.dumps(old_report), encoding="utf-8")
            prior_campaign = {"id": "campaign-a", "unique_signal_dates": ["2026-10-20"]}
            (data / "daily_status.json").write_text(
                json.dumps({"strategy_campaign": prior_campaign}), encoding="utf-8")
            def failing_update(notify):
                effective = json.loads((data / "strategy_optimization.json").read_text(encoding="utf-8"))
                seen.append(effective["decision"]["applied_gate"])
                raise RuntimeError("scan failed")
            with patch.object(daily_pipeline, "DATA", data), \
                 patch.object(daily_pipeline, "run_backtest", return_value=backtest), \
                 patch.object(daily_pipeline, "optimize_v2", return_value={}), \
                 patch.object(daily_pipeline, "run_strategy_optimization", return_value=report), \
                 patch.object(daily_pipeline, "update", side_effect=failing_update):
                self.assertEqual(daily_pipeline.main(), 1)
            unchanged = json.loads((data / "strategy_registry.json").read_text(encoding="utf-8"))
            restored_report = json.loads(
                (data / "strategy_optimization.json").read_text(encoding="utf-8"))
            failed_status = json.loads((data / "daily_status.json").read_text(encoding="utf-8"))
        self.assertEqual(seen, [gate_a])
        self.assertEqual(unchanged, registry)
        self.assertEqual(restored_report, old_report)
        self.assertEqual(failed_status["strategy_campaign"], prior_campaign)

    def test_history_is_idempotent_by_data_date(self):
        record = {"data_date": "2026-10-15", "applied_gate": {}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "strategy_history.jsonl"
            self.assertTrue(daily_pipeline.append_history_once(path, record))
            self.assertFalse(daily_pipeline.append_history_once(path, {**record, "applied_gate": {"changed": True}}))
            rows = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(rows), 1)

    def test_v2_failure_records_error_and_returns_nonzero(self):
        backtest = {"signal_dates": [], "trades": []}
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(daily_pipeline, "DATA", Path(tmp)), \
             patch.object(daily_pipeline, "run_backtest", return_value=backtest), \
             patch.object(daily_pipeline, "optimize_v2", side_effect=RuntimeError("v2 failed")), \
             patch.object(daily_pipeline, "update") as update_mock:
            self.assertEqual(daily_pipeline.main(), 1)
            status = json.loads((Path(tmp) / "daily_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "error")
        self.assertIn("v2 failed", status["error"])
        update_mock.assert_not_called()

    def test_main_records_error_and_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(daily_pipeline, "DATA", Path(tmp)), \
             patch.object(daily_pipeline, "run_backtest", side_effect=RuntimeError("offline")):
            self.assertEqual(daily_pipeline.main(), 1)
            status = json.loads((Path(tmp) / "daily_status.json").read_text(encoding="utf-8"))
        self.assertEqual(status["status"], "error")
        self.assertEqual(status["health"]["overall"], "error")
        self.assertNotIn("daily_status", status["health"].get("documents", {}))
        self.assertIn("offline", status["error"])
        self.assertIsNotNone(status["completed_at"])


if __name__ == "__main__":
    unittest.main()
