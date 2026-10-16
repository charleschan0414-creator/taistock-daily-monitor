import json
import unittest
from datetime import datetime, timezone, timedelta
from urllib.parse import parse_qs, urlparse

from taistock.realtime import RealtimeQuoteClient, build_channel, parse_quote


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
    def read(self):
        return json.dumps(self.payload).encode("utf-8")
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


class RealtimeTests(unittest.TestCase):
    def test_builds_twse_and_tpex_channels(self):
        self.assertEqual(build_channel("2330", "上市"), "tse_2330.tw")
        self.assertEqual(build_channel("5347", "上櫃"), "otc_5347.tw")

    def test_parses_live_quote_and_change(self):
        now = datetime(2026, 10, 16, 10, 0, tzinfo=timezone(timedelta(hours=8)))
        row = parse_quote({"c":"2330", "n":"台積電", "z":"1010", "y":"1000", "o":"1005", "h":"1020", "l":"995", "v":"123", "d":"20261016", "t":"10:00:00"}, now)
        self.assertEqual(row["price"], 1010.0)
        self.assertEqual(row["change"], 10.0)
        self.assertEqual(row["change_pct"], 1.0)
        self.assertFalse(row["stale"])
        self.assertEqual(row["status"], "即時")

    def test_missing_last_falls_back_and_marks_stale(self):
        now = datetime(2026, 10, 16, 10, 0, tzinfo=timezone(timedelta(hours=8)))
        row = parse_quote({"c":"2330", "z":"-", "a":"1008_1010_", "b":"1005_", "y":"1000", "d":"20261016", "t":"10:00:00"}, now)
        self.assertEqual(row["price"], 1008.0)
        self.assertTrue(row["stale"])
        self.assertIn("非即時", row["status"])

    def test_closed_market_is_not_live(self):
        now = datetime(2026, 10, 16, 14, 0, tzinfo=timezone(timedelta(hours=8)))
        row = parse_quote({"c":"2330", "z":"1010", "y":"1000", "d":"20261016", "t":"13:30:00"}, now)
        self.assertTrue(row["stale"])
        self.assertEqual(row["status"], "收盤/非即時")

    def test_batches_requests_and_retries_error(self):
        calls = []
        attempts = {"n": 0}
        def opener(request, timeout):
            calls.append(request.full_url)
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise OSError("temporary")
            channels = parse_qs(urlparse(request.full_url).query)["ex_ch"][0].split("|")
            return FakeResponse({"msgArray":[{"c": ch.split("_")[1].split(".")[0], "z":"10", "y":"9", "d":"20261016", "t":"10:00:00"} for ch in channels]})
        rows = RealtimeQuoteClient(batch_size=2, retries=1, opener=opener, sleep=lambda _: None).fetch([
            {"code":"1111", "market":"上市"}, {"code":"2222", "market":"上櫃"}, {"code":"3333", "market":"上市"}])
        self.assertEqual(set(rows), {"1111", "2222", "3333"})
        self.assertEqual(len(calls), 3)
        self.assertIn("tse_1111.tw%7Cotc_2222.tw", calls[0])

    def test_permanent_error_is_reported(self):
        def opener(request, timeout):
            raise OSError("down")
        with self.assertRaisesRegex(RuntimeError, "down"):
            RealtimeQuoteClient(retries=1, opener=opener, sleep=lambda _: None).fetch([{"code":"2330", "market":"上市"}])


if __name__ == "__main__":
    unittest.main()
