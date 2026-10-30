import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from taistock.mobile import build_dashboard, create_server, csv_download_allowed


class StubQuotes:
    def fetch(self, rows):
        return {str(row["code"]): {"price": 123.0, "change_pct": 1.25, "quote_time": "2026-10-16T10:00:00+08:00", "status": "即時", "stale": False} for row in rows}


class StubGlobalMarket:
    def fetch(self):
        return {"status": "ok", "fetched_at": "2026-10-17T10:00:00+08:00",
                "items": [{"symbol": "TX", "contract_name": "臺股期貨", "price": 23000.0,
                           "volume": 100, "updown": 100.0, "change_pct": 0.44}],
                "scenario": {"bias": "偏多", "basis": "TX / UNF / SXF 上漲 2、下跌 1"}}


def verified_latest(longs=None, shorts=None, data_date="2026-10-15"):
    def verified(row):
        return {**row, "day_trading_eligibility": {"eligible": True, "source_date": data_date}}
    return {"data_date": data_date, "updated_at": f"{data_date}T09:00:00+08:00",
            "long_top10": [verified(row) for row in (longs or [])],
            "short_top10": [verified(row) for row in (shorts or [])],
            "execution_eligibility": {"enforced": True, "source_date": data_date}}


class MobileTests(unittest.TestCase):
    def test_dashboard_limits_sides_and_adds_quotes(self):
        payload = verified_latest([{"code":str(i), "market":"上市"} for i in range(12)],
                                  [{"code":str(i + 20), "market":"上櫃"} for i in range(12)])
        payload.update({"optimization":{"status":"學習中"},
                        "strategy_gate":{"enabled":True,"status":"已啟用｜偏空停用",
                                         "directions":{"long":True,"short":False}}})
        dashboard = build_dashboard(payload, {}, StubQuotes(), StubGlobalMarket(),
                                    {"status": "success", "errors": [], "data_date": payload["data_date"]})
        self.assertEqual(len(dashboard["long_top10"]), 10)
        self.assertEqual(len(dashboard["short_top10"]), 10)
        self.assertEqual(dashboard["long_top10"][0]["realtime"]["price"], 123.0)
        self.assertFalse(dashboard["strategy_gate"]["directions"]["short"])
        self.assertIn("generated_at", dashboard)
        self.assertEqual(dashboard["source_updated_at"], "2026-10-15T09:00:00+08:00")

    def test_dashboard_includes_global_market_without_affecting_stock_rows(self):
        latest = verified_latest([{"code": "2330"}])
        dashboard = build_dashboard(latest, {}, StubQuotes(), StubGlobalMarket(),
                                    {"status": "success", "errors": [], "data_date": latest["data_date"]})
        self.assertEqual(dashboard["global_market"]["items"][0]["symbol"], "TX")
        self.assertEqual(dashboard["global_market"]["scenario"]["bias"], "偏多")
        self.assertEqual(dashboard["long_top10"][0]["code"], "2330")

    def test_quote_failure_keeps_rows_and_reports_error(self):
        class Failed:
            def fetch(self, rows): raise RuntimeError("offline")
        latest = verified_latest([{"code":"2330"}])
        dashboard = build_dashboard(latest, {}, Failed(), StubGlobalMarket(),
                                    {"status": "success", "errors": [], "data_date": latest["data_date"]})
        self.assertEqual(dashboard["long_top10"][0]["code"], "2330")
        self.assertIn("offline", dashboard["quote_error"])

    def test_unverified_or_malformed_eligibility_hides_rows_and_blocks_csv(self):
        malformed_row = verified_latest([{"code": "2330"}])
        malformed_row["long_top10"][0]["day_trading_eligibility"] = "invalid"
        mixed_rows = verified_latest([{"code": "2330"}, {"code": "2317"}])
        mixed_rows["long_top10"][1]["day_trading_eligibility"] = "invalid"
        malformed_direction = verified_latest([], [{"code": "2308"}])
        malformed_direction["long_top10"] = "invalid"
        for payload in (
            [],
            "invalid",
            {"data_date": "2026-10-15", "long_top10": [{"code": "2330"}]},
            {**verified_latest([{"code": "2330"}]), "data_date": "2026-10-15garbage"},
            {**verified_latest(), "long_top10": None, "short_top10": None},
            {**verified_latest(), "execution_eligibility": []},
            malformed_row,
            mixed_rows,
            malformed_direction,
        ):
            dashboard = build_dashboard(payload, {}, StubQuotes(), StubGlobalMarket())
            self.assertEqual(dashboard["long_top10"], [])
            self.assertEqual(dashboard["short_top10"], [])
            self.assertFalse(csv_download_allowed(payload))

    def test_local_dashboard_ready_requires_canonical_health_and_eligibility(self):
        latest = verified_latest([{"code": "2330"}])
        healthy = {"status": "success", "errors": [], "data_date": latest["data_date"]}
        dashboard = build_dashboard(latest, {}, StubQuotes(), StubGlobalMarket(), healthy)
        self.assertTrue(dashboard["execution_status"]["ready"])
        self.assertEqual(len(dashboard["long_top10"]), 1)

        missing = build_dashboard(latest, {}, StubQuotes(), StubGlobalMarket())
        self.assertFalse(missing["execution_status"]["ready"])
        self.assertEqual(missing["long_top10"], [])

        failed = build_dashboard(latest, {}, StubQuotes(), StubGlobalMarket(),
                                 {"status": "success", "errors": ["source failed"],
                                  "data_date": latest["data_date"]})
        self.assertFalse(failed["execution_status"]["ready"])
        self.assertEqual(failed["long_top10"], [])

        malformed = verified_latest([{"code": "2330"}])
        malformed["long_top10"][0]["day_trading_eligibility"] = "invalid"
        rejected = build_dashboard(malformed, {}, StubQuotes(), StubGlobalMarket(), healthy)
        self.assertFalse(rejected["execution_status"]["ready"])
        self.assertEqual(rejected["long_top10"], [])

        paused_latest = verified_latest([])
        paused_latest["market_session"] = {"status": "paused_no_new_official_session"}
        paused_failed = build_dashboard(paused_latest, {}, StubQuotes(), StubGlobalMarket(), {
            "status": "failed", "errors": ["boom"], "data_date": paused_latest["data_date"],
        })
        self.assertEqual(paused_failed["execution_status"]["state"], "blocked")

    def test_malformed_backtest_container_is_ignored(self):
        latest = verified_latest([{"code": "2330"}])
        for malformed in (["invalid"], "invalid"):
            dashboard = build_dashboard(latest, malformed, StubQuotes(), StubGlobalMarket())
            self.assertIsNone(dashboard["backtest"])

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

    def test_mobile_primary_stock_list_precedes_collapsed_research_details(self):
        html = (Path(__file__).resolve().parent.parent / "mobile" / "index.html").read_text(encoding="utf-8")
        self.assertLess(html.index('id="stock-heading"'), html.index('id="night-pulse"'))
        self.assertIn('<details id="advanced-hub"', html)
        self.assertIn("進階研究與系統", html)
        self.assertIn('<span>可執行偏多</span>', html)
        self.assertIn('<span>可執行偏空</span>', html)
        self.assertRegex(html, r'id="list"[^>]*>[\s\S]*?正在載入個股觀察')
        self.assertIn("freshness.classList.add('failed')", html)
        self.assertIn("資料暫時無法載入，請稍後再試。", html)
        self.assertNotIn("meta.textContent='更新失敗：'+error.message", html)
        self.assertIn('aria-pressed="true"', html)
        self.assertIn("el('details')", html)
        self.assertIn("data?.source_updated_at", html)

    def test_local_mobile_has_fail_closed_preflight_status_center(self):
        html = (Path(__file__).resolve().parent.parent / "mobile" / "index.html").read_text(encoding="utf-8")
        self.assertRegex(html, r'id="preflight-center"[\s\S]*?id="preflight-title"[\s\S]*?id="preflight-detail"')
        self.assertIn("function preflightStatus()", html)
        self.assertIn("官方資格或資料健康未完成｜不可執行", html)
        self.assertIn("休市觀察｜不可執行", html)
        self.assertIn("更新失敗｜不可執行", html)
        self.assertIn("preflightTitle.textContent='更新中'", html)
        self.assertIn("data?.execution_status?.ready===true", html)
        self.assertIn("execution_status:{ready:false,state:'blocked'", html)

    def test_mobile_page_renders_compatible_100_day_cost_performance(self):
        html = (Path(__file__).resolve().parent.parent / "mobile" / "index.html").read_text(encoding="utf-8")
        for marker in ('id="backtest-performance"', 'id="backtest-period"',
                       'id="backtest-standard"', 'id="backtest-gross"', 'id="backtest-stress"'):
            self.assertIn(marker, html)
        self.assertIn("100日成本後績效", html)
        self.assertIn("訊號批次，不是帳戶報酬", html)
        self.assertIn("standard 0.435%", html)
        self.assertIn("stress 0.535%", html)
        self.assertIn("data?.backtest", html)
        self.assertIn("metrics_scenarios", html)
        self.assertIn("舊報告未提供", html)

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

    def test_mobile_page_clears_cached_executable_rows_after_refresh_failure(self):
        html = (Path(__file__).resolve().parent.parent / "mobile" / "index.html").read_text(encoding="utf-8")
        catch_block = html.split("}catch(error){", 1)[1].split("}finally{", 1)[0]
        self.assertIn("data=failClosedRows(data);render();", catch_block)
        self.assertLess(catch_block.index("data=failClosedRows(data);render();"), catch_block.index("localStorage"))
        self.assertIn("try{saved=localStorage.getItem('dashboard')}catch(storageError){}", catch_block)
        self.assertIn("data=failClosedRows(cached);render()", catch_block)
        self.assertNotIn("data=JSON.parse(saved);render()", html)

    def test_mobile_service_worker_only_caches_shell_and_never_downloads(self):
        script = (Path(__file__).resolve().parent.parent / "mobile" / "service-worker.js").read_text(encoding="utf-8")
        self.assertIn("taistock-shell-v2", script)
        self.assertIn("startsWith('/download/')", script)
        self.assertIn("SHELL.includes(path)", script)

    def test_http_static_api_download_whitelist_and_traversal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); data = root / "data"; mobile = root / "mobile"
            data.mkdir(); mobile.mkdir()
            (data / "latest.json").write_text(json.dumps(verified_latest([{"code":"2330","market":"上市"}])), encoding="utf-8")
            (data / "daily_status.json").write_text(json.dumps({
                "status": "success", "errors": [], "data_date": "2026-10-15",
            }), encoding="utf-8")
            (data / "latest.csv").write_text("UNRELATED-STALE", encoding="utf-8")
            (data / "backtest_100d.csv").write_text("code\n", encoding="utf-8")
            (mobile / "index.html").write_text("<h1>Monitor</h1>", encoding="utf-8")
            (mobile / "manifest.json").write_text("{}", encoding="utf-8")
            server = create_server("127.0.0.1", 0, root, quote_client=StubQuotes(), global_client=StubGlobalMarket())
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            try:
                home = urlopen(base + "/")
                self.assertIn(b"Monitor", home.read())
                self.assertEqual(home.headers["X-Frame-Options"], "DENY")
                self.assertIn("frame-ancestors 'none'", home.headers["Content-Security-Policy"])
                self.assertEqual(home.headers["Referrer-Policy"], "no-referrer")
                self.assertIn("geolocation=()", home.headers["Permissions-Policy"])
                head = urlopen(Request(base + "/", method="HEAD"))
                self.assertEqual(head.status, 200)
                self.assertEqual(head.read(), b"")
                self.assertIn("frame-ancestors 'none'", head.headers["Content-Security-Policy"])
                self.assertEqual(head.headers["X-Content-Type-Options"], "nosniff")
                with self.assertRaises(HTTPError) as unsupported:
                    urlopen(Request(base + "/", data=b"", method="POST"))
                self.assertEqual(unsupported.exception.code, 405)
                self.assertIn("frame-ancestors 'none'", unsupported.exception.headers["Content-Security-Policy"])
                self.assertEqual(urlopen(base + "/manifest.json").status, 200)
                api = json.load(urlopen(base + "/api/dashboard"))
                self.assertIn("realtime", api["long_top10"][0])
                self.assertEqual(api["global_market"]["status"], "ok")
                response = urlopen(base + "/download/latest.csv")
                latest_csv = response.read().decode("utf-8-sig")
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertIn("date,code,name,market", latest_csv)
                self.assertIn("2026-10-15,2330", latest_csv)
                self.assertNotIn("UNRELATED-STALE", latest_csv)
                (data / "latest.csv").unlink()
                regenerated = urlopen(base + "/download/latest.csv")
                self.assertEqual(regenerated.status, 200)
                self.assertIn("2026-10-15,2330", regenerated.read().decode("utf-8-sig"))
                (data / "daily_status.json").write_text(json.dumps({
                    "status": "failed", "errors": ["boom"], "data_date": "2026-10-15",
                }), encoding="utf-8")
                with self.assertRaises(HTTPError) as unhealthy_csv:
                    urlopen(base + "/download/latest.csv")
                self.assertEqual(unhealthy_csv.exception.code, 503)
                (data / "latest.json").write_text("[]", encoding="utf-8")
                with self.assertRaises(HTTPError) as rejected:
                    urlopen(base + "/download/latest.csv")
                self.assertEqual(rejected.exception.code, 503)
                self.assertEqual(rejected.exception.headers["Cache-Control"], "no-store")
                self.assertEqual(urlopen(base + "/download/backtest_100d.csv").status, 200)
                for path in ("/download/scanner.db", "/../app.py", "/mobile/../app.py"):
                    with self.assertRaises(HTTPError) as caught:
                        urlopen(base + path)
                    self.assertEqual(caught.exception.code, 404)
            finally:
                server.shutdown(); server.server_close(); thread.join(2)


if __name__ == "__main__": unittest.main()
