import tempfile
import unittest
from pathlib import Path

from taistock.backtest import (
    OfficialHistoryFetcher,
    build_backtest,
    parse_tpex_day,
    parse_tpex_institutions,
    parse_twse_day,
    parse_twse_institutions,
    simulate_trade,
    summarize_trades,
    write_backtest_outputs,
)


class OfficialParsingTests(unittest.TestCase):
    def test_parses_four_official_daily_sources_with_share_units(self):
        twse = {"date": "20261015", "tables": [{
            "fields": ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"],
            "data": [["2330", "台積電", "1,234", "1", "2", "100", "105", "98", "103"]],
        }]}
        tpex = {"date": "115/10/15", "tables": [{"data": [
            ["6488", "環球晶", "500", "+1", "490", "510", "480", "x", "2,345"],
        ]}]}
        tw_inst = {"date": "20261015", "data": [["2330", "台積電", "1", "2", "3", "4", "5", "6", "7", "8", "9", "2,000"]]}
        tp_row = ["6488", "環球晶"] + ["0"] * 21 + ["-3,000"]
        tp_inst = {"date": "115/10/15", "tables": [{"data": [tp_row]}]}

        self.assertEqual(parse_twse_day(twse)["2330"]["volume"], 1234)
        self.assertEqual(parse_twse_day(twse)["2330"]["high"], 105.0)
        self.assertEqual(parse_tpex_day(tpex)["6488"]["volume"], 2345)
        self.assertEqual(parse_tpex_day(tpex)["6488"]["open"], 490.0)
        self.assertEqual(parse_twse_institutions(tw_inst), {"2330": 2000})
        self.assertEqual(parse_tpex_institutions(tp_inst), {"6488": -3000})

    def test_filters_non_common_stock_codes(self):
        payload = {"tables": [{"fields": ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"],
                                "data": [["0050", "ETF", "1", "1", "1", "1", "1", "1", "1"],
                                         ["12345", "權證", "1", "1", "1", "1", "1", "1", "1"]]}]}
        self.assertEqual(parse_twse_day(payload), {})


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.long = {"signal_direction": "long", "entry_trigger": 100.0, "stop_loss": 95.0,
                     "target1": 107.5, "target2": 110.0}
        self.short = {"signal_direction": "short", "entry_trigger": 100.0, "stop_loss": 105.0,
                      "target1": 92.5, "target2": 90.0}

    def test_untriggered_signal_has_no_return(self):
        result = simulate_trade(self.long, {"open": 98, "high": 99, "low": 96, "close": 97})
        self.assertFalse(result["triggered"])
        self.assertIsNone(result["return"])

    def test_conservative_stop_wins_when_stop_and_target_touch(self):
        result = simulate_trade(self.long, {"open": 100, "high": 111, "low": 94, "close": 108})
        self.assertEqual(result["exit_reason"], "stop")
        self.assertEqual(result["exit"], 95.0)
        self.assertAlmostEqual(result["return"], -0.05)
        self.assertNotIn("net_return", result)

    def test_same_day_target1_and_target2_conservatively_exits_at_target1(self):
        long = simulate_trade(self.long, {"open": 99, "high": 111, "low": 99, "close": 101})
        self.assertEqual(long["exit_reason"], "target1")
        self.assertEqual(long["exit"], 107.5)
        self.assertAlmostEqual(long["return"], 0.075)
        short = simulate_trade(self.short, {"open": 101, "high": 103, "low": 89, "close": 95})
        self.assertEqual(short["exit_reason"], "target1")
        self.assertEqual(short["exit"], 92.5)
        self.assertAlmostEqual(short["return"], 0.075)

    def test_close_fallback(self):
        close_exit = simulate_trade(self.long, {"open": 99, "high": 103, "low": 97, "close": 102})
        self.assertEqual(close_exit["exit_reason"], "close")
        self.assertAlmostEqual(close_exit["return"], 0.02)

    def test_waits_up_to_five_days_and_reports_mfe_mae_without_costs(self):
        days = [
            {"date": "2026-01-02", "open": 98, "high": 99, "low": 97, "close": 98},
            {"date": "2026-01-05", "open": 100, "high": 103, "low": 96, "close": 102},
            {"date": "2026-01-06", "open": 102, "high": 111, "low": 101, "close": 109},
        ]
        result = simulate_trade(self.long, days)
        self.assertTrue(result["triggered"])
        self.assertEqual(result["entry_date"], "2026-01-05")
        self.assertEqual(result["exit_date"], "2026-01-06")
        self.assertEqual(result["exit_reason"], "target1")
        self.assertAlmostEqual(result["return"], .075)
        self.assertAlmostEqual(result["mfe"], .11)
        self.assertAlmostEqual(result["mae"], -.04)
        self.assertNotIn("net_return", result)

    def test_less_than_five_future_days_is_unresolved(self):
        result = simulate_trade(self.long, [{"date": "2026-01-02", "open": 100,
                                             "high": 120, "low": 90, "close": 100}],
                                require_full_horizon=True)
        self.assertEqual(result["status"], "unresolved")
        self.assertFalse(result["triggered"])


class OfficialHistoryFetcherTests(unittest.TestCase):
    def test_caches_each_source_and_reuses_successful_complete_days(self):
        class FakeClient:
            def __init__(self): self.urls = []
            def json(self, url):
                self.urls.append(url)
                digits = url.split("date=")[1].split("&")[0].replace("%2F", "/")
                ymd = digits.replace("/", "")
                if "MI_INDEX" in url:
                    return {"date": ymd, "tables": [{"fields": ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"],
                                                           "data": [["2330", "台積電", "100", "1", "1", "100", "101", "99", "100"]]}]}
                if "T86" in url:
                    return {"date": ymd, "data": [["2330", "台積電", "100"]]}
                roc = f"{int(digits[:4])-1911}/{digits[5:7]}/{digits[8:10]}"
                if "dailyQuotes" in url:
                    return {"date": roc, "tables": [{"data": [["6488", "環球晶", "50", "0", "50", "51", "49", "x", "200"]]}]}
                return {"date": roc, "tables": [{"data": [["6488", "環球晶"] + ["0"] * 21 + ["10"]]}]}
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            fetcher = OfficialHistoryFetcher(client, Path(directory), throttle=0)
            first = fetcher.collect("2026-01-02", 2)
            self.assertEqual([row["date"] for row in first], ["2026-01-01", "2026-01-02"])
            self.assertEqual(len(client.urls), 8)
            self.assertEqual(len(list(Path(directory).glob("*.json.gz"))), 8)
            second = fetcher.collect("2026-01-02", 2)
            self.assertEqual(first, second)
            self.assertEqual(len(client.urls), 8)

    def test_mismatched_cached_payload_is_removed_so_morning_retry_can_recover(self):
        class FakeClient:
            def __init__(self): self.calls = 0
            def json(self, url):
                self.calls += 1
                requested = url.split("date=")[1].split("&")[0].replace("%2F", "/")
                requested_compact = requested.replace("/", "")
                compact = "20261015" if self.calls <= 4 else requested_compact
                if "MI_INDEX" in url:
                    return {"date": compact, "tables": [{"fields": ["證券代號"],
                            "data": [["2330", "台積電", "100", "1", "1", "100", "101", "99", "100"]]}]}
                if "T86" in url:
                    return {"date": compact, "data": [["2330", "台積電", "100"]]}
                roc = f"{int(compact[:4])-1911}/{compact[4:6]}/{compact[6:8]}"
                if "dailyQuotes" in url:
                    return {"date": roc, "tables": [{"data": [["6488", "環球晶", "50", "0", "50", "51", "49", "x", "200"]]}]}
                return {"date": roc, "tables": [{"data": [["6488", "環球晶"] + ["0"] * 21 + ["10"]]}]}

        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            fetcher = OfficialHistoryFetcher(FakeClient(), cache, throttle=0)
            from datetime import datetime
            day = datetime(2026, 10, 16)
            self.assertIsNone(fetcher.fetch_day(day))
            self.assertEqual(list(cache.glob("20261016_*.json.gz")), [])
            recovered = fetcher.fetch_day(day)
            self.assertEqual(recovered["date"], "2026-10-16")
            self.assertEqual(fetcher.requests, 8)

    def test_empty_quote_cache_is_removed_so_retry_can_recover(self):
        class FakeClient:
            def __init__(self): self.calls = 0
            def json(self, url):
                self.calls += 1
                requested = url.split("date=")[1].split("&")[0].replace("%2F", "/")
                compact = requested.replace("/", "")
                roc = f"{int(compact[:4])-1911}/{compact[4:6]}/{compact[6:8]}"
                if "MI_INDEX" in url:
                    rows = [] if self.calls <= 4 else [["2330", "台積電", "100", "1", "1", "100", "101", "99", "100"]]
                    return {"date": compact, "tables": [{"fields": ["證券代號"], "data": rows}]}
                if "T86" in url:
                    return {"date": compact, "data": [["2330", "台積電", "100"]]}
                if "dailyQuotes" in url:
                    return {"date": roc, "tables": [{"data": [["6488", "環球晶", "50", "0", "50", "51", "49", "x", "200"]]}]}
                return {"date": roc, "tables": [{"data": [["6488", "環球晶"] + ["0"] * 21 + ["10"]]}]}

        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            client = FakeClient()
            fetcher = OfficialHistoryFetcher(client, cache, throttle=0)
            from datetime import datetime
            day = datetime(2026, 10, 16)
            self.assertIsNone(fetcher.fetch_day(day))
            self.assertFalse((cache / "20261016_twse_quotes.json.gz").exists())
            recovered = fetcher.fetch_day(day)
            self.assertEqual(recovered["date"], "2026-10-16")
            self.assertGreater(client.calls, 4)


class BacktestAggregationTests(unittest.TestCase):
    def test_metrics_use_equal_weight_daily_compounding_and_trade_profit_factor(self):
        trades = [
            {"signal_date": "2026-01-01", "direction": "long", "triggered": True, "net_return": 0.10},
            {"signal_date": "2026-01-01", "direction": "long", "triggered": True, "net_return": -0.10},
            {"signal_date": "2026-01-02", "direction": "short", "triggered": True, "net_return": -0.20},
            {"signal_date": "2026-01-02", "direction": "short", "triggered": False, "net_return": None},
        ]
        metrics = summarize_trades(trades, ["2026-01-01", "2026-01-02"])
        self.assertEqual(metrics["signal_days"], 2)
        self.assertEqual(metrics["signals"], 4)
        self.assertEqual(metrics["trades"], 3)
        self.assertEqual(metrics["untriggered"], 1)
        self.assertAlmostEqual(metrics["win_rate"], 1 / 3)
        self.assertAlmostEqual(metrics["average_return"], -0.2 / 3)
        self.assertNotIn("average_net_return", metrics)
        self.assertAlmostEqual(metrics["cumulative_return"], -0.2)
        self.assertAlmostEqual(metrics["max_drawdown"], -0.2)
        self.assertAlmostEqual(metrics["profit_factor"], 1 / 3)
        self.assertEqual(metrics["long"]["trades"], 2)
        self.assertEqual(metrics["short"]["trades"], 1)

    def test_builds_one_signal_day_without_lookahead_and_exports_artifacts(self):
        dates = [f"2026-01-{day:02d}" for day in range(1, 8)]
        days = []
        closes = [90, 91, 92, 93, 94, 100, 102]
        volumes = [100, 100, 100, 100, 100, 130, 120]
        for i, date in enumerate(dates):
            quote = {"code": "2330", "name": "台積電", "market": "上市", "volume": volumes[i],
                     "open": closes[i], "high": closes[i] + 1, "low": closes[i] - 1, "close": closes[i]}
            days.append({"date": date, "quotes": {"2330": quote}, "institutions": {"2330": 1000}})
        result = build_backtest(days, signal_days=1)
        self.assertEqual(result["period"], {"from": "2026-01-06", "to": "2026-01-06"})
        self.assertEqual(result["metrics"]["signal_days"], 1)
        self.assertEqual(len(result["trades"]), 1)
        trade = result["trades"][0]
        self.assertEqual(trade["signal_date"], "2026-01-06")
        self.assertEqual(trade["trade_date"], "2026-01-07")
        self.assertLess(trade["signal_date"], trade["trade_date"])
        with tempfile.TemporaryDirectory() as directory:
            write_backtest_outputs(result, Path(directory), 1)
            self.assertTrue((Path(directory) / "backtest_1d.json").exists())
            csv_text = (Path(directory) / "backtest_trades.csv").read_text(encoding="utf-8-sig")
            self.assertIn("signal_date,trade_date,code,name,direction", csv_text)

    def test_walk_forward_uses_only_prior_resolved_samples(self):
        from taistock.backtest import walk_forward_weights
        samples = [{"signal_date": "2026-01-01", "exit_date": "2026-01-03",
                    "direction": "long", "triggered": True, "return": .01,
                    "volume_growth_pct": 1, "ma5_distance_pct": 1,
                    "institutional_abs_lots": 1, "volume_rank": 1} for _ in range(30)]
        before = walk_forward_weights(samples, "2026-01-03")
        after = walk_forward_weights(samples, "2026-01-04")
        self.assertEqual(before["sample_count"], 0)
        self.assertEqual(after["sample_count"], 30)

    def test_writes_required_named_csv_and_sqlite_tables(self):
        import sqlite3
        result = {"period": {"from": "2026-01-01", "to": "2026-01-01"},
                  "signal_dates": ["2026-01-01"], "metrics": {},
                  "trades": [{"signal_date": "2026-01-01", "code": "2330",
                              "direction": "long", "status": "untriggered", "triggered": False}],
                  "optimization": {"source": "walk_forward_backtest_100d"}}
        with tempfile.TemporaryDirectory() as directory:
            write_backtest_outputs(result, Path(directory), 100)
            self.assertTrue((Path(directory) / "backtest_100d.csv").exists())
            db = sqlite3.connect(Path(directory) / "scanner.db")
            names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            db.close()
            self.assertTrue({"backtest_runs", "backtest_trades"} <= names)


if __name__ == "__main__":
    unittest.main()
