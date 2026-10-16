import unittest
from datetime import datetime, timezone

from taistock.global_market import GlobalMarketClient, market_scenario


class StubHttp:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.urls = []

    def json(self, url):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.payload


class GlobalMarketTests(unittest.TestCase):
    def test_fetches_official_contracts_and_parses_comma_numbers(self):
        payload = [
            {"contract": "TX106", "price": "46,140", "ttlvol": "40,941", "contractName": "臺股期貨", "updown": "-1,144"},
            {"contract": "ZEF106", "price": "2,875.00", "ttlvol": "583", "contractName": "小型電子期貨", "updown": "-88.10"},
            {"contract": "CDF106", "price": "2,384.00", "ttlvol": "2,860", "contractName": "台積電期貨", "updown": "-47.00"},
            {"contract": "UNF126", "price": "29,374.00", "ttlvol": "285", "contractName": "那期達克100期貨", "updown": "-802.00"},
            {"contract": "SXF126", "price": "11,211.50", "ttlvol": "93", "contractName": "費城半導體期貨", "updown": "-465.50"},
            {"contract": "OTHER", "price": "1", "ttlvol": "1", "contractName": "忽略", "updown": "1"},
        ]
        http = StubHttp(payload)
        result = GlobalMarketClient(http=http, clock=lambda: datetime(2026, 10, 17, 1, 2, 3, tzinfo=timezone.utc)).fetch()
        self.assertEqual(result["status"], "ok")
        self.assertEqual([row["symbol"] for row in result["items"]], ["TX", "ZEF", "CDF", "UNF", "SXF"])
        self.assertEqual(result["items"][0]["price"], 46140.0)
        self.assertEqual(result["items"][0]["volume"], 40941)
        expected = (-1144 / (46140 - -1144)) * 100
        self.assertAlmostEqual(result["items"][0]["change_pct"], expected)
        self.assertEqual(result["fetched_at"], "2026-10-17T01:02:03+00:00")
        self.assertEqual(result["scenario"]["bias"], "偏空")
        self.assertIn("taifex.com.tw/cht/quotesApi/getQuotes?objId=12", http.urls[0])

    def test_zero_previous_price_has_no_change_percentage(self):
        result = GlobalMarketClient(http=StubHttp([
            {"contract": "TX106", "price": "100", "ttlvol": "0", "contractName": "臺股期貨", "updown": "100"}
        ] + [
            {"contract": symbol + "106", "price": "100", "ttlvol": "1", "contractName": symbol, "updown": "1"}
            for symbol in ("ZEF", "CDF", "UNF", "SXF")
        ])).fetch()
        self.assertIsNone(result["items"][0]["change_pct"])

    def test_failure_returns_explicit_status_instead_of_raising(self):
        result = GlobalMarketClient(http=StubHttp(error=TimeoutError("slow"))).fetch()
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["items"], [])
        self.assertIn("slow", result["message"])
        self.assertIn("fetched_at", result)

    def test_incomplete_official_payload_is_an_explicit_error(self):
        result = GlobalMarketClient(http=StubHttp([
            {"contract": "TX106", "price": "100", "ttlvol": "1", "contractName": "臺股期貨", "updown": "1"}
        ])).fetch()
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["items"], [])
        self.assertIn("缺少", result["message"])

    def test_scenario_uses_only_tx_unf_sxf_and_explains_breadth(self):
        rows = [
            {"symbol": "TX", "change_pct": 1.0}, {"symbol": "UNF", "change_pct": 0.5},
            {"symbol": "SXF", "change_pct": -0.2}, {"symbol": "CDF", "change_pct": -9.0},
        ]
        scenario = market_scenario(rows)
        self.assertEqual(scenario["bias"], "偏多")
        self.assertIn("TX / UNF / SXF", scenario["basis"])
        self.assertEqual(scenario["positive"], 2)
        self.assertEqual(scenario["negative"], 1)


if __name__ == "__main__":
    unittest.main()
