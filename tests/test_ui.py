import unittest
from taistock.ui import format_backtest_summary, display_quote_values, visible_signal_rows


class BacktestUiTests(unittest.TestCase):
    def test_formats_period_completion_and_long_short_details(self):
        result = {"period": {"from": "2026-05-22", "to": "2026-10-14"},
                  "completed_at": "2026-10-16T10:00:00+08:00",
                  "metrics": {"trades": 10, "win_rate": .5, "cumulative_return": .1, "max_drawdown": -.05,
                              "long": {"signals": 8, "trades": 6, "win_rate": .5, "cumulative_return": .06},
                              "short": {"signals": 5, "trades": 4, "win_rate": .5, "cumulative_return": .04}}}
        text = format_backtest_summary(result)
        self.assertIn("2026-05-22 ～ 2026-10-14", text)
        self.assertIn("成交 10", text)
        self.assertIn("累積 10.00%", text)
        self.assertIn("最大回撤 -5.00%", text)
        self.assertIn("偏多", text)
        self.assertIn("偏空", text)
        self.assertIn("2026-10-16T10:00:00+08:00", text)

    def test_visible_rows_are_top_ten_each_and_deduplicated_for_fetch(self):
        payload = {"long_top10": [{"code": str(i)} for i in range(12)],
                   "short_top10": [{"code": "0"}] + [{"code": str(i + 20)} for i in range(12)]}
        rows = visible_signal_rows(payload)
        self.assertEqual(len(rows), 19)
        self.assertLessEqual(len(rows), 20)
        self.assertEqual(len({x["code"] for x in rows}), 19)

    def test_quote_display_includes_price_change_and_status(self):
        values = display_quote_values({"price": 101.5, "change_pct": 1.25,
                                       "quote_time": "2026-10-16T10:00:00+08:00", "status": "即時"})
        self.assertEqual(values, ("101.5", "+1.25%", "10:00:00 即時"))


if __name__ == "__main__": unittest.main()
