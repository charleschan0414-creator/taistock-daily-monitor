import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from build_software_manifest import RELEASE_FILES
from taistock.health import build_health_report, export_diagnostics


TAIPEI = timezone(timedelta(hours=8))


class HealthReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.now = datetime(2026, 10, 17, 7, 45, tzinfo=TAIPEI)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        (self.data / name).write_text(json.dumps(value), encoding="utf-8")

    def write_healthy_files(self):
        self.write("daily_status.json", {
            "status": "success", "completed_at": "2026-10-16T16:10:00+08:00",
            "data_date": "2026-10-16",
        })
        self.write("latest.json", {
            "data_date": "2026-10-16", "top100_count": 100,
            "long_count": 4, "short_count": 3,
            "signals": [{"code": "2330"}], "universe": [{"code": "2330"}],
        })
        self.write("backtest_100d.json", {
            "period": {"from": "2026-05-01", "to": "2026-10-16"},
            "metrics": {"signal_days": 100}, "trades": [{"code": "secret"}],
        })
        self.write("strategy_registry.json", {
            "strategy_version": 3, "activated_on": "2026-10-01",
            "forward_signal_days": 8, "minimum_forward_signal_days": 20,
            "status": "forward_observation", "active_gate": {"secret": 1},
        })
        self.write("market_regime.json", {
            "snapshot_count": 17, "insufficient_history": True,
            "affects_stock_scores": False,
        })

    def test_builds_readable_healthy_report_and_next_schedule(self):
        self.write_healthy_files()
        report = build_health_report(self.data, "2026.10.17.6", self.now)
        self.assertEqual(report["overall"], "ok")
        self.assertEqual(report["latest_data_date"], "2026-10-16")
        self.assertEqual(report["pipeline"]["status"], "success")
        self.assertEqual(report["strategy"], {
            "version": 3, "activated_on": "2026-10-01", "forward_signal_days": 8,
            "minimum_forward_signal_days": 20, "progress": "8/20",
            "status": "forward_observation",
        })
        self.assertEqual(report["universe"], {"total": 100, "selected": 7})
        self.assertEqual(report["night_market"], {"count": 17, "threshold": 60, "remaining": 43})
        self.assertEqual(report["desktop_version"], "2026.10.17.6")
        self.assertEqual(report["next_update"], "2026-10-18T06:30:00+08:00")
        self.assertEqual(set(report["sources"]), {
            "daily_status", "latest", "backtest_100d", "strategy_registry", "market_regime"})
        for source in report["sources"].values():
            self.assertEqual(source["status"], "ok")
            self.assertIn("message", source)
            self.assertIn("date", source)

    def test_uses_list_lengths_when_count_fields_are_absent(self):
        self.write_healthy_files()
        self.write("latest.json", {
            "data_date": "2026-10-16",
            "universe": [{"code": "1"}, {"code": "2"}],
            "signals": [{"code": "1"}],
        })
        report = build_health_report(self.data, "test", self.now)
        self.assertEqual(report["universe"], {"total": 2, "selected": 1})

    def test_missing_and_corrupt_files_warn_without_crashing(self):
        (self.data / "latest.json").write_text("{broken", encoding="utf-8")
        report = build_health_report(self.data, "test", self.now)
        self.assertEqual(report["overall"], "warning")
        self.assertEqual(report["sources"]["latest"]["status"], "warning")
        self.assertEqual(report["sources"]["daily_status"]["status"], "warning")
        self.assertIsNone(report["latest_data_date"])

    def test_pipeline_error_makes_overall_error_and_exposes_recent_error(self):
        self.write_healthy_files()
        self.write("daily_status.json", {
            "status": "error", "completed_at": "2026-10-17T07:00:00+08:00",
            "data_date": "2026-10-16", "error": "RuntimeError: offline",
        })
        report = build_health_report(self.data, "test", self.now)
        self.assertEqual(report["overall"], "error")
        self.assertEqual(report["pipeline"]["status"], "error")
        self.assertEqual(report["recent_error"], "RuntimeError: offline")

    def test_success_older_than_48_hours_warns(self):
        self.write_healthy_files()
        self.write("daily_status.json", {
            "status": "success", "completed_at": "2026-10-15T07:44:59+08:00",
            "data_date": "2026-10-14",
        })
        report = build_health_report(self.data, "test", self.now)
        self.assertEqual(report["overall"], "warning")
        self.assertEqual(report["pipeline"]["freshness"], "stale")

    def test_next_schedule_rolls_to_next_day_at_or_after_0630(self):
        self.write_healthy_files()
        now = datetime(2026, 10, 17, 6, 30, tzinfo=TAIPEI)
        report = build_health_report(self.data, "test", now)
        self.assertEqual(report["next_update"], "2026-10-18T06:30:00+08:00")

    def test_export_is_atomic_anonymous_and_contains_only_safe_summaries(self):
        self.write_healthy_files()
        output = self.data / "diagnostics.json"
        result = export_diagnostics(self.data, "2026.10.17.6", output, self.now)
        self.assertEqual(result, output)
        payload = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(set(payload), {"health", "json_summaries"})
        self.assertNotIn("signals", json.dumps(payload))
        self.assertNotIn("2330", json.dumps(payload))
        self.assertNotIn("secret", json.dumps(payload))
        self.assertNotIn(str(self.data), json.dumps(payload))
        self.assertEqual(payload["json_summaries"]["latest"]["safe_fields"]["top100_count"], 100)
        self.assertFalse(output.with_name(output.name + ".tmp").exists())

    def test_health_module_is_in_release_manifest(self):
        self.assertIn("taistock/health.py", RELEASE_FILES)


if __name__ == "__main__":
    unittest.main()
