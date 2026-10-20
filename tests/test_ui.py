import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from taistock.ui import (format_backtest_summary, display_quote_values, visible_signal_rows,
                         format_global_market, BackgroundRefreshGate, dashboard_structure,
                         signal_row_tags, global_market_layers, strategy_card_label,
                         technical_research_text, broker_risk_text, format_health_report,
                         run_desktop_update, Dashboard)


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
        self.assertIn("gross（舊報告，未扣成本）", text)

    def test_prefers_gross_standard_stress_cost_scenarios_for_each_direction(self):
        def metric(trades, win_rate, average, pf, drawdown):
            return {"trades": trades, "win_rate": win_rate, "average_return": average,
                    "profit_factor": pf, "max_drawdown": drawdown,
                    "long": {"trades": trades - 2, "win_rate": win_rate + .01,
                             "average_return": average + .001, "profit_factor": pf + .1,
                             "max_drawdown": drawdown + .01},
                    "short": {"trades": 2, "win_rate": win_rate - .02,
                              "average_return": average - .001, "profit_factor": pf - .1,
                              "max_drawdown": drawdown - .01}}
        result = {
            "period": {"from": "2026-05-22", "to": "2026-10-14"},
            "metrics": metric(10, .55, .01, 1.4, -.08),
            "metrics_scenarios": {
                "gross": metric(10, .55, .01, 1.4, -.08),
                "standard": metric(10, .50, .00565, 1.2, -.10),
                "stress": metric(10, .45, .00465, 1.1, -.12),
            },
            "costs": {"gross": 0, "standard": .00435, "stress": .00535},
        }
        text = format_backtest_summary(result)
        for label in ("gross（未扣成本）", "standard 0.435%", "stress 0.535%"):
            self.assertIn(label, text)
        for label in ("整體", "偏多", "偏空"):
            self.assertGreaterEqual(text.count(label), 3)
        self.assertIn("成交 10｜勝率 50.00%｜平均 0.56%｜PF 1.20｜最大回撤 -10.00%", text)
        self.assertIn("訊號批次，不是帳戶報酬", text)
        self.assertIn("借券費與融券限制", text)

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
        self.assertEqual(structure["tool_actions"], ("更新現價", "更新夜盤", "執行回測", "開啟手機版",
                                                     "匯出診斷", "建立備份", "還原備份"))
        self.assertEqual(structure["cards"], ("資料日", "待驗證偏多", "待驗證偏空", "即時報價", "策略驗證"))
        self.assertEqual(structure["tabs"], ("待驗證偏多", "待驗證偏空", "所有觀察", "回測摘要", "夜盤脈動", "系統健康"))
    def test_strategy_card_distinguishes_protective_incumbent_from_cost_validated_candidate(self):
        self.assertEqual(strategy_card_label({"applied_mode": "incumbent_protective",
                                              "enabled": False}), "未通過成本驗證")
        self.assertEqual(strategy_card_label({"applied_mode": "candidate", "enabled": True,
                                              "cost_validated": True}), "已啟用")
    def test_technical_research_is_explicitly_non_scoring(self):
        row = {"technical_research": {"status": "available", "score_effect": "none", "indicators": {
            "rsi_5": 68.4, "atr_5_pct": 4.3,
            "close_vs_prior5_high_pct": 1.2, "close_vs_prior5_low_pct": 9.8}}}
        self.assertEqual(technical_research_text(row, "long"),
                         "研究不計分｜RSI5 68.4｜ATR5 4.3%｜距前5日高 +1.2%")
    def test_broker_risk_warning_is_explicitly_non_scoring_and_not_official(self):
        row = {"broker_risk_research": {"status": "available", "warning": True,
                "risk_level": "high", "watched_buyers": [{"branch": "國泰-敦南", "net_lots": 1005}],
                "watched_sellers": []}}
        text = broker_risk_text(row)
        self.assertIn("高度注意", text)
        self.assertIn("國泰-敦南", text)
        self.assertIn("買超", text)
        self.assertIn("不計分", text)
        self.assertIn("非官方認定", text)

    def test_desktop_full_update_delegates_to_the_validated_cli_pipeline(self):
        completed = Mock(returncode=0, stdout="ok", stderr="")
        with patch("taistock.ui.subprocess.run", return_value=completed) as runner, \
             patch("taistock.ui.load_result", return_value={"data_date": "2026-10-20"}):
            result = run_desktop_update(Path("C:/app/data"))
        self.assertEqual(result["data_date"], "2026-10-20")
        self.assertEqual(runner.call_args.args[0][-1], "--update")
        self.assertEqual(runner.call_args.kwargs["cwd"], Path("C:/app"))
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
    def test_health_text_contains_status_progress_schedule_and_error(self):
        text = format_health_report({
            "overall": "error", "latest_data_date": "2026-10-16",
            "desktop_version": "2026.10.17.6", "next_update": "2026-10-18T06:30:00+08:00",
            "pipeline": {"status": "error", "completed_at": "2026-10-17T07:00:00+08:00"},
            "strategy": {"version": 3, "activated_on": "2026-10-01", "progress": "8/20"},
            "universe": {"total": 100, "selected": 7},
            "night_market": {"count": 17, "threshold": 60, "remaining": 43},
            "recent_error": "RuntimeError: offline",
            "sources": {"latest": {"status": "ok", "message": "可正常讀取", "date": "2026-10-16"}},
        })
        for expected in ("需處理", "2026.10.17.6", "8/20", "17/60", "06:30", "RuntimeError: offline", "100 / 7"):
            self.assertIn(expected, text)

    def test_restore_button_cancels_without_selected_file_or_confirmation(self):
        dashboard = object.__new__(Dashboard)
        dashboard.data_dir = Path("C:/app/data")
        with patch("taistock.ui.filedialog.askopenfilename", return_value=""), \
             patch("taistock.ui.restore_backup") as restorer:
            dashboard.restore_backup_action()
        restorer.assert_not_called()
        with patch("taistock.ui.filedialog.askopenfilename", return_value="C:/safe/backup.zip"), \
             patch("taistock.ui.messagebox.askyesno", return_value=False), \
             patch("taistock.ui.restore_backup") as restorer:
            dashboard.restore_backup_action()
        restorer.assert_not_called()

    def test_backup_and_confirmed_restore_show_clear_results(self):
        dashboard = object.__new__(Dashboard)
        dashboard.data_dir = Path("C:/app/data")
        with patch("taistock.ui.create_backup", return_value={"path": Path("backup.zip"), "file_count": 3}), \
             patch("taistock.ui.messagebox.showinfo") as info:
            dashboard.create_backup_action()
        self.assertIn("3", info.call_args.args[1])
        with patch("taistock.ui.filedialog.askopenfilename", return_value="C:/safe/backup.zip"), \
             patch("taistock.ui.messagebox.askyesno", return_value=True), \
             patch("taistock.ui.restore_backup", return_value={"path": Path("backup.zip"), "file_count": 2}) as restorer, \
             patch("taistock.ui.messagebox.showinfo") as info:
            dashboard.restore_backup_action()
        restorer.assert_called_once_with(Path("C:/app"), Path("C:/safe/backup.zip"))
        self.assertIn("2", info.call_args.args[1])


if __name__ == "__main__": unittest.main()
