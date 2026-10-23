import unittest

from build_software_manifest import RELEASE_FILES
from taistock.market_calendar import parse_dgpa_taipei_closure, parse_twse_holiday


class MarketCalendarTests(unittest.TestCase):
    def test_market_calendar_is_in_release_manifest(self):
        self.assertIn("taistock/market_calendar.py", RELEASE_FILES)

    def test_twse_schedule_distinguishes_holiday_from_trading_day_labels(self):
        payload = {
            "stat": "ok", "queryYear": "2026",
            "fields": ["日期", "名稱", "說明"],
            "data": [
                ["2026-01-02", "國曆新年開始交易日", "國曆新年開始交易。"],
                ["2026-02-11", "農曆春節前最後交易日", "農曆春節前最後交易。"],
                ["2026-02-12", "市場無交易，僅辦理結算交割作業", "市場無交易"],
                ["2026-10-26", "光復節補假", "依規定放假1日。"],
            ],
        }
        self.assertFalse(parse_twse_holiday(payload, "2026-01-02"))
        self.assertFalse(parse_twse_holiday(payload, "2026-02-11"))
        self.assertTrue(parse_twse_holiday(payload, "2026-02-12"))
        self.assertTrue(parse_twse_holiday(payload, "2026-10-26"))

    def test_dgpa_requires_matching_date_and_taipei_full_day_stop_work(self):
        closed = """
        <h2>115年 10月 23日 天然災害停止上班及上課情形</h2>
        <tr><td headers="city_Name Taipei City"><font>臺北市</font></td>
        <td><font>今天停止上班、停止上課。</font></td></tr>
        """
        open_day = closed.replace("今天停止上班、停止上課。", "今天照常上班、照常上課。")
        afternoon_only = closed.replace("今天停止上班、停止上課。", "今天下午停止上班、停止上課。")
        morning = closed.replace("今天停止上班、停止上課。", "今天上午停止上班、停止上課。")
        self.assertTrue(parse_dgpa_taipei_closure(closed, "2026-10-23"))
        self.assertTrue(parse_dgpa_taipei_closure(morning, "2026-10-23"))
        self.assertFalse(parse_dgpa_taipei_closure(open_day, "2026-10-23"))
        self.assertFalse(parse_dgpa_taipei_closure(afternoon_only, "2026-10-23"))
        with self.assertRaisesRegex(ValueError, "日期"):
            parse_dgpa_taipei_closure(closed, "2026-10-24")


if __name__ == "__main__":
    unittest.main()
