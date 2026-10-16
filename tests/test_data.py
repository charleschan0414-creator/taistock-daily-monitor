import unittest
from taistock.data import parse_twse_quotes, parse_tpex_quotes, ensure_same_date


class DataParsingTests(unittest.TestCase):
    def test_parses_both_markets_into_normalized_quotes(self):
        twse = parse_twse_quotes([{"Code":"2330","Name":"台積電","ClosingPrice":"1000.00","TradeVolume":"12,345","Date":"20261015"}])
        tpex = parse_tpex_quotes([{"SecuritiesCompanyCode":"6488","CompanyName":"環球晶","Close":"500.5","TradingShares":"8,000","Date":"1151015"}])
        self.assertEqual((twse[0].code, twse[0].volume, twse[0].date), ("2330", 12345, "2026-10-15"))
        self.assertEqual((tpex[0].code, tpex[0].volume, tpex[0].date), ("6488", 8000, "2026-10-15"))

    def test_refuses_mismatched_market_or_institution_dates(self):
        with self.assertRaisesRegex(ValueError, "日期不一致"):
            ensure_same_date("2026-10-15", "2026-10-14", "上市")


if __name__ == "__main__":
    unittest.main()
