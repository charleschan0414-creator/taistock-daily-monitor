import csv
import gzip
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from taistock.backtest import (
    OfficialHistoryFetcher,
    build_backtest,
    parse_tpex_day,
    parse_tpex_institutions,
    parse_twse_day,
    parse_twse_institutions,
    simulate_trade,
    summarize_trade_scenarios,
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

    def test_open_through_stop_before_same_day_entry_does_not_create_gap_fill(self):
        result = simulate_trade(
            self.long, {"open": 90, "high": 101, "low": 89, "close": 96}
        )
        self.assertTrue(result["triggered"])
        self.assertEqual(result["exit_reason"], "stop")
        self.assertEqual(result["exit"], 95.0)
        self.assertAlmostEqual(result["return"], -0.05)

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
        self.assertEqual(close_exit["exit_reason"], "forced_close")
        self.assertAlmostEqual(close_exit["return"], 0.02)

    def test_only_next_trading_day_is_eligible_and_untriggered_never_carries_forward(self):
        days = [
            {"date": "2026-01-02", "open": 98, "high": 99, "low": 97, "close": 98},
            {"date": "2026-01-05", "open": 100, "high": 103, "low": 96, "close": 102},
        ]
        result = simulate_trade(self.long, days)
        self.assertEqual(result["status"], "untriggered")
        self.assertFalse(result["triggered"])
        self.assertEqual(result["exit_date"], "2026-01-02")

    def test_one_future_day_is_a_complete_horizon_and_closes_same_day(self):
        result = simulate_trade(self.long, [{"date": "2026-01-02", "open": 100,
                                             "high": 103, "low": 97, "close": 102}],
                                require_full_horizon=True)
        self.assertEqual(result["status"], "settled")
        self.assertEqual(result["entry_date"], "2026-01-02")
        self.assertEqual(result["exit_date"], "2026-01-02")
        self.assertEqual(result["exit_reason"], "forced_close")

    def test_gap_through_entry_uses_worse_open_for_long_returns_and_excursions(self):
        result = simulate_trade(
            self.long, {"date": "2026-01-02", "open": 104, "high": 106, "low": 101, "close": 105}
        )
        self.assertEqual(result["entry"], 104.0)
        self.assertAlmostEqual(result["return"], 1 / 104)
        self.assertAlmostEqual(result["mfe"], 2 / 104)
        self.assertAlmostEqual(result["mae"], -3 / 104)

    def test_gap_through_entry_uses_worse_open_for_short_returns_and_excursions(self):
        result = simulate_trade(
            self.short, {"date": "2026-01-02", "open": 96, "high": 99, "low": 94, "close": 95}
        )
        self.assertEqual(result["entry"], 96.0)
        self.assertAlmostEqual(result["return"], 1 / 96)
        self.assertAlmostEqual(result["mfe"], 2 / 96)
        self.assertAlmostEqual(result["mae"], -3 / 96)


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

    def test_cost_scenarios_deduct_round_trip_cost_once_only_from_executed_trades(self):
        trades = [
            {"signal_date": "2026-01-01", "direction": "long", "status": "settled",
             "triggered": True, "return": 0.10},
            {"signal_date": "2026-01-01", "direction": "short", "status": "untriggered",
             "triggered": False, "return": None},
        ]
        scenarios = summarize_trade_scenarios(trades, ["2026-01-01"])
        self.assertEqual(set(scenarios), {"gross", "standard", "stress"})
        self.assertAlmostEqual(scenarios["gross"]["average_return"], 0.10)
        self.assertAlmostEqual(scenarios["standard"]["average_return"], 0.10 - 0.00435)
        self.assertAlmostEqual(scenarios["stress"]["average_return"], 0.10 - 0.00535)
        self.assertEqual(scenarios["standard"]["trades"], 1)
        self.assertEqual(scenarios["standard"]["untriggered"], 1)
        self.assertEqual(scenarios["standard"]["short"]["average_return"], 0.0)

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
        self.assertEqual(result["metrics"], result["metrics_scenarios"]["gross"])
        self.assertIn("portfolio_standard", result)
        self.assertTrue(result["portfolio_standard"]["realized_only_equity"])
        self.assertEqual(result["costs"], {"gross": 0.0, "standard": 0.00435, "stress": 0.00535})
        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(len(result["_candidate_universe"]), 1)
        candidate = result["_candidate_universe"][0]
        self.assertTrue(candidate["eligible"])
        self.assertTrue(candidate["selected_signal"])
        self.assertEqual(candidate["selected_rank"], 1)
        self.assertIn("technical_candle_body_pct", candidate)
        self.assertEqual(candidate["candlestick_status"], "aligned_or_neutral")
        self.assertNotIn("return", candidate)
        self.assertNotIn("high", candidate)
        trade = result["trades"][0]
        self.assertEqual(trade["signal_date"], "2026-01-06")
        self.assertEqual(trade["trade_date"], "2026-01-07")
        self.assertLess(trade["signal_date"], trade["trade_date"])
        self.assertEqual(trade["entry_date"], trade["exit_date"])
        self.assertEqual(trade["exit_reason"], "forced_close")
        self.assertEqual(trade["entry_trigger"], 101.0)
        realized = result["portfolio_standard"]["realized_trades"][0]
        self.assertEqual(realized["notional"], trade["entry"] * realized["shares"])
        with tempfile.TemporaryDirectory() as directory:
            write_backtest_outputs(result, Path(directory), 1)
            self.assertTrue((Path(directory) / "backtest_1d.json").exists())
            self.assertTrue((Path(directory) / "portfolio_1d.json").exists())
            portfolio = json.loads((Path(directory) / "portfolio_1d.json").read_text(encoding="utf-8"))
            self.assertEqual(portfolio, result["portfolio_standard"])
            csv_path = Path(directory) / "backtest_trades.csv"
            csv_text = csv_path.read_text(encoding="utf-8-sig")
            self.assertIn("signal_date,trade_date,code,name,direction", csv_text)
            self.assertNotIn(b"\r\n", csv_path.read_bytes())

    def test_preserves_top_volume_candidate_without_history_and_rejection_reason(self):
        dates = [f"2026-01-{day:02d}" for day in range(1, 8)]
        days = []
        for index, date in enumerate(dates):
            regular = {"code": "2330", "name": "台積電", "market": "上市", "volume": 100,
                       "open": 100, "high": 101, "low": 99, "close": 100}
            quotes = {"2330": regular}
            if index == 5:
                quotes["9999"] = {"code": "9999", "name": "新股", "market": "上市", "volume": 1000,
                                  "open": 10, "high": 11, "low": 9, "close": 10}
            days.append({"date": date, "quotes": quotes, "institutions": {}})

        result = build_backtest(days, signal_days=1)
        self.assertEqual(len(result["_candidate_universe"]), 2)
        new_listing = next(row for row in result["_candidate_universe"] if row["code"] == "9999")
        self.assertEqual(new_listing["volume_rank"], 1)
        self.assertFalse(new_listing["eligible"])
        self.assertIn("insufficient_history", new_listing["rejection_reasons"])
        self.assertFalse(new_listing["selected_signal"])
        self.assertIsNone(new_listing["selected_rank"])

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
    def test_writes_candidate_gzip_summary_and_excludes_private_rows_from_payloads(self):
        candidates = [
            {"signal_date": "2026-01-01", "code": "2330", "name": "台積電", "market": "上市",
             "volume_rank": 1, "close": 100, "volume": 200, "prev_volume": 100,
             "institutional_net": 1000, "ma5": 95, "volume_growth_pct": 100,
             "above_ma5_pct": 5.26, "ma5_distance_pct": 5.26, "institutional_lots": 1,
             "institutional_abs_lots": 1, "volatility_proxy_pct": 2, "eligible": True,
             "signal_direction": "long", "rejection_reasons": [], "score": 90,
             "entry_trigger": 101, "stop_loss": 98, "target1": 105.5, "target2": 107,
             "risk_pct": 2.97, "entry_offset_pct": 1, "selected_signal": True, "selected_rank": 1},
            {"signal_date": "2026-01-01", "code": "9999", "name": "新股", "market": "上市",
             "volume_rank": 2, "close": 10, "volume": 100, "prev_volume": None,
             "institutional_net": 0, "ma5": None, "volume_growth_pct": None,
             "above_ma5_pct": None, "ma5_distance_pct": None, "institutional_lots": 0,
             "institutional_abs_lots": 0, "volatility_proxy_pct": None, "eligible": False,
             "signal_direction": None, "rejection_reasons": ["insufficient_history"], "score": None,
             "entry_trigger": None, "stop_loss": None, "target1": None, "target2": None,
             "risk_pct": None, "entry_offset_pct": None, "selected_signal": False, "selected_rank": None},
        ]
        result = {"period": {"from": "2026-01-01", "to": "2026-01-01"},
                  "signal_dates": ["2026-01-01"], "metrics": {}, "trades": [],
                  "optimization": None, "_candidate_universe": candidates}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_backtest_outputs(result, root, 100)
            artifact = root / "candidate_universe_100d.csv.gz"
            with gzip.open(artifact, "rt", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1]["rejection_reasons"], "insufficient_history")
            payload = json.loads((root / "backtest_100d.json").read_text(encoding="utf-8"))
            self.assertNotIn("_candidate_universe", payload)
            self.assertEqual(payload["candidate_universe_summary"], {
                "signal_dates": 1, "total_rows": 2, "eligible": 1, "selected": 1,
                "rejection_reasons": {"insufficient_history": 1},
                "artifact": "candidate_universe_100d.csv.gz",
            })
            db = sqlite3.connect(root / "scanner.db")
            stored = json.loads(db.execute("SELECT payload FROM backtest_runs").fetchone()[0])
            db.close()
            self.assertNotIn("_candidate_universe", stored)
            self.assertEqual(stored["candidate_universe_summary"], payload["candidate_universe_summary"])

    def test_candidate_gzip_failure_leaves_no_partial_artifact(self):
        result = {"period": {"from": "2026-01-01", "to": "2026-01-01"},
                  "signal_dates": ["2026-01-01"], "metrics": {}, "trades": [],
                  "optimization": None, "_candidate_universe": []}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("taistock.backtest.gzip.open", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    write_backtest_outputs(result, root, 100)
            self.assertFalse((root / "candidate_universe_100d.csv.gz").exists())
            self.assertEqual(list(root.glob("candidate_universe_100d.csv.gz.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
