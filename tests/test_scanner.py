import copy
import unittest
from taistock.scanner import Scanner, attach_candlestick_research
from taistock.models import Quote


class FakeClient:
    def json(self, url):
        if "STOCK_DAY_ALL" in url:
            return [{"Date":"1151015","Code":"1111","Name":"甲","ClosingPrice":"12","TradeVolume":"2000"}]
        if "daily_close_quotes" in url:
            return [{"Date":"1151015","SecuritiesCompanyCode":"2222","CompanyName":"乙","Close":"20","TradingShares":"3000"}]
        if "fund/T86" in url:
            return {"date":"20261015","data":[["1111","甲"] + ["0"]*16 + ["3000"]]}
        if "3insti" in url:
            return [{"Date":"1151015","SecuritiesCompanyCode":"2222","TotalDifference":"-100"}]
        if "stockNo=1111" in url:
            return {"data":[[f"115/10/{d:02d}",str(v),"0","0","0","0",str(c)] for d,v,c in [(9,1000,8),(12,1000,9),(13,1000,10),(14,1000,11),(15,2000,12)]]}
        if "code=2222" in url:
            return {"tables":[{"data":[[f"115/10/{d:02d}",str(v//1000),"0","0","0","0",str(c)] for d,v,c in [(9,1000,18),(12,1000,19),(13,1000,20),(14,1000,20),(15,3000,20)]]}]}
        raise AssertionError(url)


class ScannerTests(unittest.TestCase):
    def test_candlestick_enrichment_cannot_change_execution_fields(self):
        candles = [{"date": str(i), "open": 10, "high": 11, "low": 9, "close": 10.5}
                   for i in range(5)]
        rows = [{
            "code": "1111", "score": 77.5, "signal_direction": "long",
            "volume_rank": 3, "entry_trigger": 10.6, "stop_loss": 10.0,
            "target1": 11.2, "eligible": True, "eligibility_source_date": "2026-10-21",
            "technical_research": {"status": "available", "recent_candles": candles,
                                     "indicators": {"candle_body_pct": 5.0,
                                                    "close_change_pct": 2.0}},
        }]
        before = copy.deepcopy(rows)
        result = attach_candlestick_research(rows)
        protected = ("code", "score", "signal_direction", "volume_rank", "entry_trigger",
                     "stop_loss", "target1", "eligible", "eligibility_source_date")
        self.assertEqual([{key: row.get(key) for key in protected} for row in result],
                         [{key: row.get(key) for key in protected} for row in before])
        self.assertEqual(result[0]["candlestick_window"]["score_effect"], "none")
        self.assertEqual(result[0]["candlestick_window"]["filter_effect"], "none")

    def test_scans_combined_volume_leaders_and_returns_only_eligible(self):
        result = Scanner(FakeClient(), workers=1).run(limit=100)
        self.assertEqual(result["data_date"], "2026-10-15")
        self.assertEqual(result["top100_count"], 2)
        self.assertEqual([x["code"] for x in result["qualified"]], ["1111"])
        self.assertEqual(result["qualified"][0]["volume_rank"], 2)

    def test_one_unavailable_history_is_reported_without_losing_entire_run(self):
        class PartialClient(FakeClient):
            def json(self, url):
                if "code=2222" in url:
                    raise RuntimeError("temporary")
                return super().json(url)
        result = Scanner(PartialClient(), workers=1).run(limit=100)
        self.assertEqual(result["top100_count"], 2)
        self.assertEqual(result["history_success_count"], 1)
        self.assertIn("2222", result["errors"][0])

    def test_returns_backward_compatible_long_top10_and_separate_short_signals(self):
        class BothClient(FakeClient):
            def json(self, url):
                if "daily_close_quotes" in url:
                    return [{"Date":"1151015","SecuritiesCompanyCode":"2222","CompanyName":"乙","Close":"18","TradingShares":"3000"}]
                if "code=2222" in url:
                    return {"tables":[{"data":[[f"115/10/{d:02d}",str(v//1000),"0","0","0","0",str(c)] for d,v,c in [(9,1000,22),(12,1000,21),(13,1000,20),(14,1000,19),(15,3000,18)]]}]}
                return super().json(url)
        result = Scanner(BothClient(), workers=1).run(limit=100)
        self.assertEqual(result["top10"], result["long_top10"])
        self.assertEqual([x["code"] for x in result["long_top10"]], ["1111"])
        self.assertEqual([x["code"] for x in result["short_top10"]], ["2222"])
        self.assertEqual(len(result["signals"]), 2)
        self.assertEqual(len(result["universe"]), 2)
        self.assertIn("optimization", result)

    def test_scans_bulk_history_days_without_per_stock_requests(self):
        days = []
        for index, date_text in enumerate(("2026-10-08", "2026-10-09", "2026-10-12",
                                           "2026-10-13", "2026-10-14", "2026-10-15")):
            days.append({
                "date": date_text,
                "quotes": {
                    "1111": {"code": "1111", "name": "甲", "market": "上市", "close": 10 + index,
                             "volume": 1000 if index < 5 else 3000, "open": 10, "high": 16, "low": 9},
                    "2222": {"code": "2222", "name": "乙", "market": "上櫃", "close": 20 - index,
                             "volume": 1000 if index < 5 else 3000, "open": 20, "high": 21, "low": 14},
                },
                "institutions": {"1111": 5000, "2222": -5000},
            })
        result = Scanner(FakeClient(), workers=1).run_from_days(days, limit=100)
        self.assertEqual(result["history_success_count"], 2)
        self.assertEqual([row["code"] for row in result["long_top10"]], ["1111"])
        self.assertEqual([row["code"] for row in result["short_top10"]], ["2222"])
        self.assertEqual(result["errors"], [])
        self.assertIn("官方逐日批次資料", result["sources"])
        research = result["long_top10"][0]["technical_research"]
        self.assertEqual(research["status"], "available")
        self.assertEqual(research["score_effect"], "none")
        self.assertIn("rsi_5", research["indicators"])
        self.assertEqual(result["short_top10"][0]["candlestick_research"]["status"],
                         "aligned_or_neutral")
        self.assertEqual(result["short_top10"][0]["candlestick_research"]["filter_effect"], "none")
        window = result["long_top10"][0]["candlestick_window"]
        self.assertEqual(window["chart_sessions"], 5)
        self.assertEqual(window["pattern_sessions"], 3)
        self.assertEqual(window["score_effect"], "none")

    def test_bulk_scan_backfills_new_listing_without_six_sessions(self):
        dates = ("2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15")
        days = []
        for index, date_text in enumerate(dates):
            quotes = {
                "1111": {"code": "1111", "name": "甲", "market": "上市", "close": 10 + index,
                         "volume": 9000, "open": 10, "high": 16, "low": 9},
                "2222": {"code": "2222", "name": "乙", "market": "上市", "close": 20 + index,
                         "volume": 8000, "open": 20, "high": 26, "low": 19},
                "3333": {"code": "3333", "name": "丙", "market": "上市", "close": 30 + index,
                         "volume": 7000, "open": 30, "high": 36, "low": 29},
            }
            if index < 5:
                quotes.pop("1111")
            days.append({"date": date_text, "quotes": quotes,
                         "institutions": {"1111": 1000, "2222": 1000, "3333": 1000}})
        result = Scanner(FakeClient(), workers=1).run_from_days(days, limit=2)
        self.assertEqual(result["top100_count"], 2)
        self.assertEqual(result["history_success_count"], 2)
        self.assertEqual([row["code"] for row in result["history_skipped"]], ["1111"])
        self.assertNotIn("1111", [row["code"] for row in result["signals"]])
        self.assertEqual(result["errors"], [])

    def test_strategy_gate_runs_after_scoring_and_filters_daily_lists(self):
        days = []
        for index, date_text in enumerate(("2026-10-08", "2026-10-09", "2026-10-12",
                                           "2026-10-13", "2026-10-14", "2026-10-15")):
            days.append({"date": date_text, "quotes": {
                "1111": {"code": "1111", "name": "甲", "market": "上市", "close": 10 + index,
                         "volume": 1000 if index < 5 else 3000, "open": 10, "high": 16, "low": 9},
                "2222": {"code": "2222", "name": "乙", "market": "上櫃", "close": 20 - index,
                         "volume": 1000 if index < 5 else 3000, "open": 20, "high": 21, "low": 14},
            }, "institutions": {"1111": 5000, "2222": -5000}})
        gate = {"enabled": True, "status": "已啟用測試", "directions": {"long": True, "short": False},
                "applied_gate": {"long_score_min": 0, "short_score_min": 0,
                                 "short_enabled": False, "volume_growth_min": 20}}
        result = Scanner(FakeClient(), workers=1, strategy_gate=gate).run_from_days(days)
        self.assertEqual([row["code"] for row in result["signals"]], ["1111"])
        self.assertEqual(result["short_top10"], [])
        self.assertEqual(result["pre_gate_counts"], {"long": 1, "short": 1})
        self.assertEqual(result["filtered_out_counts"], {"long": 0, "short": 1})
        self.assertEqual(result["strategy_gate"], gate)

    def test_universe_keeps_non_leaders_for_next_update_settlement(self):
        result = Scanner(FakeClient(), workers=1).run(limit=1)
        self.assertEqual(result["top100_count"], 1)
        self.assertEqual([row["code"] for row in result["universe"]], ["1111", "2222"])

    def test_history_crosses_month_boundary_to_obtain_five_trading_days(self):
        class MonthClient:
            def json(self, url):
                if "date=20261002" in url:
                    return {"data":[["115/10/01","100","0","0","0","0","10"],["115/10/02","120","0","0","0","0","11"]]}
                if "date=20260901" in url:
                    return {"data":[[f"115/09/{d}","90","0","0","0","0",str(c)] for d,c in [(27,7),(28,8),(30,9)]]}
                raise AssertionError(url)
        history = Scanner(MonthClient(), workers=1)._history(Quote("1111","甲",11,120,"2026-10-02","上市"), "2026-10-02")
        self.assertEqual([x[2] for x in history[-5:]], [7,8,9,10,11])


if __name__ == "__main__": unittest.main()
