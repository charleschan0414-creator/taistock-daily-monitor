import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import app


class AppCliTests(unittest.TestCase):
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
    def test_optimize_strategy_option_runs_without_opening_ui(self):
        with patch("app.run_strategy_optimization", return_value={}) as runner:
            self.assertEqual(app.main(["--optimize-strategy"]), 0)
        runner.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
