import unittest

from taistock.day_trading import apply_execution_eligibility, parse_tpex_eligibility, parse_twse_eligibility


class DayTradingEligibilityTests(unittest.TestCase):
    def test_parses_official_lists_and_suspension_marker(self):
        twse = {"stat": "OK", "date": "20261021", "tables": [
            {}, {"fields": ["證券代號", "證券名稱", "暫停現股賣出後現款買進當沖註記"],
                 "data": [["2492", "華新科", ""], ["1111", "甲", "Y"]]},
        ]}
        tpex = [
            {"資料日期": "1151021", "證券代號": "2222", "證券名稱": "乙",
             "暫停現股賣出後現款買進當沖註記": ""},
            {"資料日期": "1151021", "證券代號": "7777", "證券名稱": "丙",
             "暫停現股賣出後現款買進當沖註記": "＊"},
        ]
        self.assertEqual(parse_twse_eligibility(twse)[0], "2026-10-21")
        self.assertFalse(parse_twse_eligibility(twse)[1]["2492"])
        self.assertTrue(parse_twse_eligibility(twse)[1]["1111"])
        self.assertEqual(parse_tpex_eligibility(tpex)[0], "2026-10-21")
        self.assertIn("2222", parse_tpex_eligibility(tpex)[1])
        self.assertTrue(parse_tpex_eligibility(tpex)[1]["7777"])

    def test_rejects_impossible_dates_and_missing_suspension_schema(self):
        bad_date = {"stat": "OK", "date": "20260230", "tables": []}
        with self.assertRaisesRegex(ValueError, "有效資料日期"):
            parse_twse_eligibility(bad_date)
        with self.assertRaisesRegex(ValueError, "欄位"):
            parse_tpex_eligibility([{"資料日期": "1151021", "證券代號": "3354"}])

    def test_twse_parser_uses_named_fields_when_columns_are_reordered(self):
        payload = {"stat": "OK", "date": "20261021", "tables": [{
            "fields": ["暫停現股賣出後現款買進當沖註記", "證券名稱", "證券代號"],
            "data": [["Y", "測試", "1111"]],
        }]}
        self.assertEqual(parse_twse_eligibility(payload), ("2026-10-21", {"1111": True}))

    def test_filters_noneligible_and_sell_first_suspended_from_executable_signals(self):
        result = {
            "data_date": "2026-10-20",
            "signals": [
                {"code": "3354", "name": "律勝", "market": "上櫃", "signal_direction": "long"},
                {"code": "2492", "name": "華新科", "market": "上市", "signal_direction": "short"},
                {"code": "1111", "name": "甲", "market": "上市", "signal_direction": "short"},
                {"code": "2222", "name": "乙", "market": "上櫃", "signal_direction": "long"},
            ],
            "long_top10": [], "short_top10": [], "qualified": [], "top10": [],
        }
        twse = {"stat": "OK", "date": "20261021", "tables": [
            {}, {"fields": ["證券代號", "證券名稱", "暫停現股賣出後現款買進當沖註記"],
                 "data": [["2492", "華新科", ""], ["1111", "甲", "Y"]]},
        ]}
        tpex = [{"資料日期": "1151021", "證券代號": "2222", "證券名稱": "乙",
                 "暫停現股賣出後現款買進當沖註記": ""}]
        filtered = apply_execution_eligibility(result, twse, tpex, "2026-10-21")
        self.assertEqual([row["code"] for row in filtered["signals"]], ["2492", "2222"])
        reasons = {row["code"]: row["reason"] for row in filtered["execution_eligibility"]["filtered_out"]}
        self.assertEqual(reasons["3354"], "not_day_trade_eligible")
        self.assertEqual(reasons["1111"], "sell_first_suspended")
        self.assertEqual(filtered["execution_eligibility"]["source_date"], "2026-10-21")
        self.assertEqual(filtered["execution_eligibility"]["execution_for_signal_date"], "next_session_verified")

    def test_unknown_market_direction_and_non_list_signals_fail_closed(self):
        result = {"data_date": "2026-10-20", "signals": [
            {"code": "1111", "name": "未知市場", "market": "其他", "signal_direction": "long"},
            {"code": "2492", "name": "未知方向", "market": "上市", "signal_direction": "sideways"},
        ]}
        twse = {"stat": "OK", "date": "20261021", "tables": [{
            "fields": ["證券代號", "暫停現股賣出後現款買進當沖註記"],
            "data": [["2492", ""]],
        }]}
        tpex = [{"資料日期": "1151021", "證券代號": "2222",
                 "暫停現股賣出後現款買進當沖註記": ""}]
        updated = apply_execution_eligibility(result, twse, tpex, "2026-10-21")
        self.assertEqual(updated["signals"], [])
        self.assertEqual([row["reason"] for row in updated["execution_eligibility"]["filtered_out"]],
                         ["unknown_market", "unknown_direction"])
        with self.assertRaisesRegex(ValueError, "signals"):
            apply_execution_eligibility({"data_date": "2026-10-20", "signals": {}},
                                        twse, tpex, "2026-10-21")

    def test_rejects_mismatched_or_stale_official_dates(self):
        twse = {"stat": "OK", "date": "20261020", "tables": [{}, {"data": []}]}
        tpex = [{"資料日期": "1151021", "證券代號": "2222",
                 "暫停現股賣出後現款買進當沖註記": ""}]
        with self.assertRaises(ValueError):
            apply_execution_eligibility({"data_date": "2026-10-20", "signals": []},
                                        twse, tpex, "2026-10-21")


if __name__ == "__main__":
    unittest.main()
