import unittest
import json
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import app
from tests.test_strategy_optimize import fixture_backtest


class AppCliTests(unittest.TestCase):
    def test_closed_session_carries_latest_data_and_skips_full_market_scan(self):
        previous = {
            "data_date": "2026-10-22", "top100_count": 100,
            "history_success_count": 100, "errors": [],
            "signals": [{"code": "6269", "score": 88.0}],
            "long_top10": [], "short_top10": [],
            "data_collection": {"latest_complete_date": "2026-10-22"},
        }
        paused = json.loads(json.dumps(previous))
        paused["market_session"] = {
            "status": "paused_no_new_official_session",
            "requested_date": "2026-10-23",
            "latest_official_date": "2026-10-22",
        }
        paused["execution_eligibility"] = {
            "enforced": True, "eligible_count": 0,
            "execution_for_signal_date": "paused_no_new_official_session",
        }
        now = datetime(2026, 10, 23, 6, 30, tzinfo=app.TAIPEI)
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            (data / "latest.json").write_text(json.dumps(previous), encoding="utf-8")
            with patch("app.market_session_status", return_value={
                     "status": "closed", "date": "2026-10-23",
                     "reason": "taipei_disaster_stop_work", "source": "DGPA"}), \
                 patch("app.latest_official_quote_date", return_value="2026-10-22"), \
                 patch("app.fetch_and_apply_execution_eligibility") as eligibility_fetch, \
                 patch("app.OfficialHistoryFetcher") as history_fetcher:
                result = app.carry_forward_if_market_closed(data, now=now, client=object())
            saved = json.loads((data / "latest.json").read_text(encoding="utf-8"))
        self.assertEqual(result["data_date"], "2026-10-22")
        self.assertEqual(saved["market_session"]["requested_date"], "2026-10-23")
        self.assertEqual(saved["market_session"]["latest_official_date"], "2026-10-22")
        self.assertEqual(saved["data_collection"]["latest_official_quote_date"], "2026-10-22")
        self.assertEqual(saved["market_session"]["closure_reason"], "taipei_disaster_stop_work")
        self.assertTrue(saved["data_collection"]["carried_forward"])
        self.assertEqual(saved["signals"][0]["score"], 88.0)
        self.assertEqual(saved["execution_eligibility"]["eligible_count"], 0)
        eligibility_fetch.assert_not_called()
        history_fetcher.assert_not_called()

    def test_closed_session_refuses_to_promote_stale_previous_data(self):
        previous = {"data_date": "2026-10-21", "signals": [],
                    "long_top10": [], "short_top10": []}
        now = datetime(2026, 10, 23, 6, 30, tzinfo=app.TAIPEI)
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            path = data / "latest.json"
            path.write_text(json.dumps(previous), encoding="utf-8")
            with patch("app.market_session_status", return_value={
                     "status": "closed", "reason": "taipei_disaster_stop_work", "source": "DGPA"}), \
                 patch("app.latest_official_quote_date", return_value="2026-10-22"), \
                 patch("app.fetch_and_apply_execution_eligibility") as eligibility_fetch:
                self.assertIsNone(app.carry_forward_if_market_closed(data, now=now, client=object()))
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved, previous)
        eligibility_fetch.assert_not_called()

    def test_session_preflight_does_not_reuse_data_when_official_session_advanced(self):
        previous = {"data_date": "2026-10-22", "signals": [],
                    "long_top10": [], "short_top10": []}
        lagged = {**previous, "market_session": {
            "status": "paused_no_new_official_session",
            "requested_date": "2026-10-23", "latest_official_date": "2026-10-22"}}
        now = datetime(2026, 10, 23, 6, 30, tzinfo=app.TAIPEI)
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            (data / "latest.json").write_text(json.dumps(previous), encoding="utf-8")
            with patch("app.market_session_status", return_value={"status": "open"}), \
                 patch("app.latest_official_quote_date", return_value="2026-10-23"), \
                 patch("app.fetch_and_apply_execution_eligibility", return_value=lagged):
                self.assertIsNone(app.carry_forward_if_market_closed(data, now=now, client=object()))

    def test_open_session_preflight_continues_full_refresh_when_quote_feeds_disagree(self):
        previous = {"data_date": "2026-10-22", "signals": [],
                    "long_top10": [], "short_top10": []}
        now = datetime(2026, 10, 27, 20, 0, tzinfo=app.TAIPEI)
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            path = data / "latest.json"
            path.write_text(json.dumps(previous), encoding="utf-8")
            with patch("app.market_session_status", return_value={"status": "open"}), \
                 patch("app.latest_official_quote_date", side_effect=ValueError(
                     "上市與上櫃最新官方行情日期不一致")), \
                 patch("app.fetch_and_apply_execution_eligibility") as eligibility_fetch:
                self.assertIsNone(app.carry_forward_if_market_closed(data, now=now, client=object()))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), previous)
        eligibility_fetch.assert_not_called()

    def test_daily_scan_rejects_partial_top100_before_persistence(self):
        complete = {"data_date": "2026-10-20", "top100_count": 100,
                    "history_success_count": 100, "errors": [],
                    "long_top10": [{"entry_trigger": 101, "stop_loss": 98,
                                    "target1": 105, "risk_pct": 3}], "short_top10": []}
        app.validate_daily_scan(complete, "2026-10-20")
        with self.assertRaisesRegex(ValueError, "100檔歷史資料完整"):
            app.validate_daily_scan({"top100_count": 100, "history_success_count": 99,
                                     "data_date": "2026-10-20", "errors": ["6173 歷史不足"],
                                     "long_top10": [], "short_top10": []}, "2026-10-20")

    def test_daily_scan_rejects_wrong_date_and_invalid_directional_prices(self):
        base = {"data_date": "2026-10-19", "top100_count": 100,
                "history_success_count": 100, "errors": [], "long_top10": [], "short_top10": []}
        with self.assertRaisesRegex(ValueError, "最新完整官方交易日"):
            app.validate_daily_scan(base, "2026-10-20")
        base["data_date"] = "2026-10-20"
        base["long_top10"] = [{"entry_trigger": 101, "stop_loss": 105,
                               "target1": 98, "risk_pct": 3}]
        with self.assertRaisesRegex(ValueError, "價格方向"):
            app.validate_daily_scan(base, "2026-10-20")

    def test_daily_scan_rejects_missing_required_schema_lists(self):
        malformed = {"data_date": "2026-10-20", "top100_count": 100,
                     "history_success_count": 100}
        with self.assertRaisesRegex(ValueError, "必要欄位"):
            app.validate_daily_scan(malformed, "2026-10-20")

    def test_data_end_date_uses_taipei_close_boundary(self):
        before = datetime(2026, 10, 16, 7, 59, tzinfo=timezone.utc)
        after = datetime(2026, 10, 16, 8, 0, tzinfo=timezone.utc)
        self.assertEqual(app.data_end_date(before), "2026-10-15")
        self.assertEqual(app.data_end_date(after), "2026-10-16")

    def test_latest_complete_date_uses_last_collected_official_day(self):
        histories = [{"date": "2026-10-14"}, {"date": "2026-10-15"}]
        self.assertEqual(app.latest_complete_date(histories), "2026-10-15")

    def test_backtest_option_runs_requested_days_without_opening_ui(self):
        with patch("app.run_backtest", return_value={}) as runner:
            self.assertEqual(app.main(["--backtest", "100"]), 0)
        runner.assert_called_once_with(100)

    def test_backtest_output_discloses_cost_scenarios_and_signal_batch_limit(self):
        def metric(average):
            values = {"signals": 1, "trades": 1, "untriggered": 0, "unresolved": 0,
                      "win_rate": 1.0, "average_return": average, "median_return": average,
                      "cumulative_return": average, "max_drawdown": 0.0, "profit_factor": None}
            return {**values, "signal_days": 1, "long": dict(values),
                    "short": {**values, "signals": 0, "trades": 0}}

        scenarios = {"gross": metric(0.01), "standard": metric(0.00565),
                     "stress": metric(0.00465)}
        result = {"period": {"from": "2026-01-01", "to": "2026-01-01"},
                  "metrics": scenarios["gross"], "metrics_scenarios": scenarios,
                  "costs": {"gross": 0.0, "standard": 0.00435, "stress": 0.00535},
                  "portfolio_standard": {"initial_capital": 1_000_000, "final_capital": 1_010_000,
                                         "net_return": .01, "max_drawdown": -.02,
                                         "peak_positions": 3, "costs": 435},
                  "cost_assumption": "fixture"}
        fetcher = unittest.mock.Mock()
        fetcher.collect.return_value = [{"date": "2026-01-01"}]
        fetcher.requests = 0
        fetcher.cache_hits = 1
        output = StringIO()
        with patch("app.OfficialHistoryFetcher", return_value=fetcher), \
             patch("app.build_backtest", return_value=result), \
             patch("app.write_backtest_outputs"), redirect_stdout(output):
            app.run_backtest(1)
        fetcher.collect.assert_called_once_with(unittest.mock.ANY, 7)
        text = output.getvalue()
        self.assertIn("gross", text)
        self.assertIn("standard", text)
        self.assertIn("0.435%", text)
        self.assertIn("stress", text)
        self.assertIn("0.535%", text)
        self.assertIn("訊號批次，不是帳戶報酬", text)
        self.assertIn("保守帳戶模型（研究估算）", text)
        self.assertIn("期末資金 1,010,000", text)
        self.assertIn("淨報酬 1.00%", text)
        self.assertIn("最大同時持倉 3", text)

    def test_diagnostics_option_uses_requested_path_without_opening_ui(self):
        destination = Path("C:/safe/diagnostics.json")
        output = StringIO()
        with patch("app.export_diagnostics", return_value=destination) as exporter, \
             patch("app.app_version", return_value="2026.10.17.6"), redirect_stdout(output):
            self.assertEqual(app.main(["--diagnostics", str(destination)]), 0)
        self.assertEqual(exporter.call_args.args[:3], (app.DATA, "2026.10.17.6", destination))
        self.assertIsNotNone(exporter.call_args.args[3].tzinfo)
        self.assertIn(str(destination), output.getvalue())

    def test_diagnostics_option_without_path_uses_diagnostics_folder(self):
        with patch("app.export_diagnostics", side_effect=lambda data, version, path, now: path) as exporter, \
             patch("app.app_version", return_value="2026.10.17.6"):
            self.assertEqual(app.main(["--diagnostics"]), 0)
        destination = exporter.call_args.args[2]
        self.assertEqual(destination.parent, app.ROOT / "diagnostics")
        self.assertRegex(destination.name, r"^health-\d{8}-\d{6}\.json$")

    def test_backup_option_uses_optional_destination_without_opening_ui(self):
        result = {"path": Path("C:/safe/backup.zip"), "file_count": 3, "completed": True}
        output = StringIO()
        with patch("app.create_backup", return_value=result) as creator, redirect_stdout(output):
            self.assertEqual(app.main(["--backup", "C:/safe/backup.zip"]), 0)
        creator.assert_called_once_with(app.ROOT, Path("C:/safe/backup.zip"))
        self.assertIn("3", output.getvalue())
        self.assertNotIn("secret", output.getvalue())

    def test_backup_option_without_path_uses_timestamped_default(self):
        result = {"path": Path("backups/TaiStockScanner-backup-20261017-120000.zip"),
                  "file_count": 0, "completed": True}
        with patch("app.create_backup", return_value=result) as creator:
            self.assertEqual(app.main(["--backup"]), 0)
        creator.assert_called_once_with(app.ROOT, None)

    def test_restore_option_runs_safe_restore_without_opening_ui(self):
        source = Path("C:/safe/backup.zip")
        result = {"path": source, "file_count": 2, "completed": True}
        output = StringIO()
        with patch("app.restore_backup", return_value=result) as restorer, redirect_stdout(output):
            self.assertEqual(app.main(["--restore", str(source)]), 0)
        restorer.assert_called_once_with(app.ROOT, source)
        self.assertIn("完成", output.getvalue())

    def test_backup_failure_returns_nonzero_without_sensitive_output(self):
        errors = StringIO()
        with patch("app.create_backup", side_effect=ValueError("unsafe archive")), \
             unittest.mock.patch("sys.stderr", errors):
            self.assertEqual(app.main(["--backup"]), 1)
        self.assertIn("備份失敗", errors.getvalue())

    def test_optimize_strategy_option_runs_without_opening_ui(self):
        with patch("app.run_strategy_optimization", return_value={}) as runner:
            self.assertEqual(app.main(["--optimize-strategy"]), 0)
        runner.assert_called_once_with()

    def test_strategy_optimization_output_discloses_all_cost_scenarios(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app, "DATA", Path(directory)):
            (Path(directory) / "backtest_100d.json").write_text(
                json.dumps(fixture_backtest()), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                app.run_strategy_optimization()
            self.assertTrue((Path(directory) / "strategy_optimization_candidate.json").exists())
            self.assertFalse((Path(directory) / "strategy_optimization.json").exists())
        text = output.getvalue()
        self.assertIn("gross", text)
        self.assertIn("standard", text)
        self.assertIn("stress", text)


if __name__ == "__main__":
    unittest.main()
