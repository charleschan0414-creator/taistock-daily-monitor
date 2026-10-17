import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from taistock.mobile import build_dashboard, create_server


class StubQuotes:
    def fetch(self, rows):
        return {str(row["code"]): {"price": 123.0, "change_pct": 1.25, "quote_time": "2026-10-16T10:00:00+08:00", "status": "即時", "stale": False} for row in rows}


class StubGlobalMarket:
    def fetch(self):
        return {"status": "ok", "fetched_at": "2026-10-17T10:00:00+08:00",
                "items": [{"symbol": "TX", "contract_name": "臺股期貨", "price": 23000.0,
                           "volume": 100, "updown": 100.0, "change_pct": 0.44}],
                "scenario": {"bias": "偏多", "basis": "TX / UNF / SXF 上漲 2、下跌 1"}}


class MobileTests(unittest.TestCase):
    def test_dashboard_limits_sides_and_adds_quotes(self):
        payload = {"data_date":"2026-10-15", "long_top10":[{"code":str(i), "market":"上市"} for i in range(12)],
                   "short_top10":[{"code":str(i + 20), "market":"上櫃"} for i in range(12)],
                   "optimization":{"status":"學習中"},
                   "strategy_gate":{"enabled":True,"status":"已啟用｜偏空停用",
                                    "directions":{"long":True,"short":False}}}
        dashboard = build_dashboard(payload, {}, StubQuotes(), StubGlobalMarket())
        self.assertEqual(len(dashboard["long_top10"]), 10)
        self.assertEqual(len(dashboard["short_top10"]), 10)
        self.assertEqual(dashboard["long_top10"][0]["realtime"]["price"], 123.0)
        self.assertFalse(dashboard["strategy_gate"]["directions"]["short"])
        self.assertIn("generated_at", dashboard)

    def test_dashboard_includes_global_market_without_affecting_stock_rows(self):
        latest = {"long_top10": [{"code": "2330"}], "short_top10": []}
        dashboard = build_dashboard(latest, {}, StubQuotes(), StubGlobalMarket())
        self.assertEqual(dashboard["global_market"]["items"][0]["symbol"], "TX")
        self.assertEqual(dashboard["global_market"]["scenario"]["bias"], "偏多")
        self.assertEqual(dashboard["long_top10"][0]["code"], "2330")

    def test_quote_failure_keeps_rows_and_reports_error(self):
        class Failed:
            def fetch(self, rows): raise RuntimeError("offline")
        dashboard = build_dashboard({"long_top10":[{"code":"2330"}]}, {}, Failed(), StubGlobalMarket())
        self.assertEqual(dashboard["long_top10"][0]["code"], "2330")
        self.assertIn("offline", dashboard["quote_error"])

    def test_mobile_page_renders_global_market_with_required_disclaimer(self):
        html = (Path(__file__).resolve().parent.parent / "mobile" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="global-market"', html)
        self.assertIn("系統抓取時間", html)
        self.assertIn("期交所行情，可能延遲，非投資建議", html)
        self.assertIn("global_market", html)
        self.assertIn("setInterval(load,60000)", html)
        self.assertIn("if(loading)return", html)

    def test_mobile_page_has_at_a_glance_summary_and_clear_sections(self):
        html = (Path(__file__).resolve().parent.parent / "mobile" / "index.html").read_text(encoding="utf-8")
        for marker in ('id="freshness"', 'id="today-summary"', 'id="summary-date"',
                       'id="summary-long"', 'id="summary-short"', 'id="summary-night"',
                       'id="night-pulse"', 'id="stock-heading"'):
            self.assertIn(marker, html)
        self.assertIn("今日重點", html)
        self.assertIn("夜盤脈動", html)
        self.assertIn('aria-pressed="true"', html)
        self.assertIn("el('details')", html)

    def test_mobile_page_builds_api_content_without_innerhtml(self):
        html = (Path(__file__).resolve().parent.parent / "mobile" / "index.html").read_text(encoding="utf-8")
        script = html.split("<script>", 1)[1].split("</script>", 1)[0]
        self.assertNotIn("innerHTML", script)
        self.assertIn("document.createElement", script)
        self.assertIn("textContent", script)
        self.assertIn("replaceChildren", script)

    def test_mobile_page_supports_small_touch_screens_and_keyboard_focus(self):
        html = (Path(__file__).resolve().parent.parent / "mobile" / "index.html").read_text(encoding="utf-8")
        self.assertIn("min-height:44px", html)
        self.assertIn(":focus-visible", html)
        self.assertIn("@media(max-width:360px)", html)
        self.assertIn("同一 Wi‑Fi", html)
        self.assertIn("目前離線", html)

    def test_http_static_api_download_whitelist_and_traversal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); data = root / "data"; mobile = root / "mobile"
            data.mkdir(); mobile.mkdir()
            (data / "latest.json").write_text(json.dumps({"long_top10":[{"code":"2330","market":"上市"}], "short_top10":[]}), encoding="utf-8")
            (data / "latest.csv").write_text("code\n2330\n", encoding="utf-8")
            (data / "backtest_100d.csv").write_text("code\n", encoding="utf-8")
            (mobile / "index.html").write_text("<h1>Monitor</h1>", encoding="utf-8")
            (mobile / "manifest.json").write_text("{}", encoding="utf-8")
            server = create_server("127.0.0.1", 0, root, quote_client=StubQuotes(), global_client=StubGlobalMarket())
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                self.assertIn(b"Monitor", urlopen(base + "/").read())
                self.assertEqual(urlopen(base + "/manifest.json").status, 200)
                api = json.load(urlopen(base + "/api/dashboard"))
                self.assertIn("realtime", api["long_top10"][0])
                self.assertEqual(api["global_market"]["status"], "ok")
                self.assertEqual(urlopen(base + "/download/latest.csv").status, 200)
                self.assertEqual(urlopen(base + "/download/backtest_100d.csv").status, 200)
                for path in ("/download/scanner.db", "/../app.py", "/mobile/../app.py"):
                    with self.assertRaises(HTTPError) as caught:
                        urlopen(base + path)
                    self.assertEqual(caught.exception.code, 404)
            finally:
                server.shutdown(); server.server_close(); thread.join(2)


if __name__ == "__main__": unittest.main()
