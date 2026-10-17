import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import daily_pipeline


class DailyPipelineTests(unittest.TestCase):
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
        backtest = {"period": {"from": "2026-05-01", "to": "2026-10-07"}, "metrics": {"signal_days": 100, "win_rate": .5},
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
        order = []
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(daily_pipeline, "DATA", Path(tmp)), \
             patch.object(daily_pipeline, "run_backtest", side_effect=lambda days: order.append(("backtest", days)) or backtest), \
             patch.object(daily_pipeline, "optimize_v2", side_effect=lambda source, output: order.append(("v2", source, output.name)) or v2), \
             patch.object(daily_pipeline, "run_strategy_optimization", side_effect=lambda: order.append(("optimize",)) or report), \
             patch.object(daily_pipeline, "update", side_effect=lambda notify: order.append(("update", notify)) or latest):
            self.assertEqual(daily_pipeline.main(), 0)
            status = json.loads((Path(tmp) / "daily_status.json").read_text(encoding="utf-8"))
            history = [json.loads(line) for line in (Path(tmp) / "strategy_history.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(order, [("backtest", 100), ("v2", backtest, "v2_optimization.json"),
                                 ("optimize",), ("update", False)])
        self.assertEqual(status["status"], "success")
        self.assertEqual(status["data_date"], "2026-10-15")
        self.assertEqual(status["v2_analysis"]["decision"],
                         {"enabled": False, "status": "research only"})
        self.assertEqual(status["v2_analysis"]["costs"],
                         {"standard": .00435, "stress": .00535})
        self.assertEqual(status["v2_analysis"]["directions"]["long"],
                         {"passed": False, "holdout": v2_holdout})
        self.assertTrue(status["v2_analysis"]["limitations"]["source_is_daily_ohlc"])
        self.assertEqual(history[0]["applied_gate"], {"long_score_min": 0})

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
        self.assertIn("offline", status["error"])
        self.assertIsNotNone(status["completed_at"])


if __name__ == "__main__":
    unittest.main()
