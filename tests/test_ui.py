import unittest
from taistock.ui import (format_backtest_summary, display_quote_values, visible_signal_rows,
                         format_global_market, BackgroundRefreshGate, dashboard_structure,
                         signal_row_tags, global_market_layers, strategy_card_label)


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

    def test_formats_global_market_rows_scenario_and_system_fetch_time(self):
        payload = {"status": "ok", "fetched_at": "2026-10-17T22:01:02+08:00",
                   "scenario": {"bias": "偏多", "basis": "TX / UNF / SXF 上漲 2、下跌 1"},
                   "items": [{"symbol": "TX", "contract": "TX106", "contract_name": "臺股期貨",
                              "price": 23123.0, "change_pct": 0.45, "volume": 12345}]}
        scenario, rows, status = format_global_market(payload)
        self.assertIn("偏多", scenario)
        self.assertIn("TX / UNF / SXF", scenario)
        self.assertEqual(rows[0], ("TX106", "臺股期貨", "23,123", "+0.45%", "12,345"))
        self.assertIn("系統抓取時間 2026-10-17T22:01:02+08:00", status)
        self.assertIn("可能延遲，非投資建議", status)

    def test_background_refresh_gate_prevents_overlap_until_finished(self):
        gate = BackgroundRefreshGate()
        self.assertTrue(gate.try_start())
        self.assertFalse(gate.try_start())
        gate.finish()
        self.assertTrue(gate.try_start())
    def test_dashboard_structure_has_one_primary_action_and_beginner_labels(self):
        structure = dashboard_structure()
        self.assertEqual(structure["primary_action"], "更新全部資料")
        self.assertEqual(structure["tool_actions"], ("更新現價", "更新夜盤", "執行回測", "開啟手機版"))
        self.assertEqual(structure["cards"], ("資料日", "今日偏多", "今日偏空", "即時報價", "策略狀態"))
        self.assertEqual(structure["tabs"], ("今日偏多", "今日偏空", "所有訊號", "回測摘要", "夜盤脈動"))
    def test_strategy_card_distinguishes_protective_incumbent_from_cost_validated_candidate(self):
        self.assertEqual(strategy_card_label({"applied_mode": "incumbent_protective",
                                              "enabled": False}), "保護性篩選")
        self.assertEqual(strategy_card_label({"applied_mode": "candidate", "enabled": True,
                                              "cost_validated": True}), "已啟用")
    def test_signal_rows_get_direction_change_and_zebra_tags(self):
        self.assertEqual(signal_row_tags("long", "+1.25%", 0), ("even", "long", "gain"))
        self.assertEqual(signal_row_tags("short", "-0.80%", 1), ("odd", "short", "loss"))
        self.assertEqual(signal_row_tags("long", "—", 2), ("even", "long", "flat"))
    def test_global_market_layers_separate_scenario_basis_and_fetch_time(self):
        payload = {"status": "ok", "fetched_at": "2026-10-17T22:01:02+08:00",
                   "scenario": {"bias": "偏多", "basis": "TX 上漲"}}
        layers = global_market_layers(payload)
        self.assertEqual(layers["scenario"], "偏多")
        self.assertEqual(layers["basis"], "依據｜TX 上漲")
        self.assertEqual(layers["fetched_at"], "抓取時間｜2026-10-17T22:01:02+08:00")
        self.assertIn("非投資建議", layers["notice"])


if __name__ == "__main__": unittest.main()
