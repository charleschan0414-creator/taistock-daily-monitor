"""同一區網唯讀 PWA HTTP server。"""
import argparse
import csv
import io
import json
import mimetypes
import socket
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .global_market import GlobalMarketClient
from .realtime import RealtimeQuoteClient, TAIPEI
from .storage import CSV_FIELDS

DOWNLOADS = {"latest.csv", "backtest_100d.csv"}
STATIC = {"index.html", "manifest.json", "service-worker.js", "icon.svg"}


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _executable_lists(latest):
    if not isinstance(latest, dict):
        return [], []
    if not csv_download_allowed(latest):
        return [], []
    execution = latest.get("execution_eligibility") or {}
    if not isinstance(execution, dict):
        return [], []
    data_date, source_date = latest.get("data_date"), execution.get("source_date")
    if execution.get("enforced") is not True:
        return [], []
    try:
        signal_day, source_day = date.fromisoformat(data_date), date.fromisoformat(source_date)
    except (TypeError, ValueError):
        return [], []
    if signal_day.isoformat() != data_date or source_day.isoformat() != source_date or source_day < signal_day:
        return [], []

    def verified(rows):
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, dict) and
                isinstance(row.get("day_trading_eligibility"), dict) and
                row["day_trading_eligibility"].get("eligible") is True and
                row["day_trading_eligibility"].get("source_date") == source_date][:10]
    return (verified(latest.get("long_top10", latest.get("top10", []))),
            verified(latest.get("short_top10", [])))


def csv_download_allowed(latest):
    if not isinstance(latest, dict):
        return False
    execution = latest.get("execution_eligibility") or {}
    if not isinstance(execution, dict):
        return False
    data_date, source_date = latest.get("data_date"), execution.get("source_date")
    if execution.get("enforced") is not True:
        return False
    try:
        signal_day, source_day = date.fromisoformat(data_date), date.fromisoformat(source_date)
    except (TypeError, ValueError):
        return False
    if signal_day.isoformat() != data_date or source_day.isoformat() != source_date or source_day < signal_day:
        return False
    longs = latest.get("long_top10", latest.get("top10", []))
    shorts = latest.get("short_top10", [])
    if not isinstance(longs, list) or not isinstance(shorts, list):
        return False
    rows = longs + shorts
    return all(isinstance(row, dict) and
               isinstance(row.get("day_trading_eligibility"), dict) and
               row["day_trading_eligibility"].get("eligible") is True and
               row["day_trading_eligibility"].get("source_date") == source_date
               for row in rows)


def verified_latest_csv(latest):
    """Build the download from the same verified JSON used by the dashboard."""
    if not csv_download_allowed(latest):
        return None
    longs, shorts = _executable_lists(latest)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in longs + shorts:
        writer.writerow({"date": latest.get("data_date"), **row})
    return b"\xef\xbb\xbf" + output.getvalue().encode("utf-8")


def build_dashboard(latest, backtest, quote_client=None, global_client=None):
    latest = latest if isinstance(latest, dict) else {}
    backtest = backtest if isinstance(backtest, dict) else {}
    longs, shorts = _executable_lists(latest)
    rows = longs + shorts
    quote_error = None
    try:
        quotes = (quote_client or RealtimeQuoteClient()).fetch(rows) if rows else {}
    except Exception as exc:
        quotes, quote_error = {}, str(exc)
    try:
        global_market = (global_client or GlobalMarketClient()).fetch()
    except Exception as exc:
        global_market = {"status": "error", "message": f"TAIFEX 行情更新失敗：{exc}",
                         "fetched_at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "items": []}
    def merge(items):
        return [dict(row, realtime=quotes.get(str(row.get("code")), {
            "price": None, "change_pct": None, "quote_time": None,
            "status": "報價暫時無法更新", "stale": True})) for row in items]
    metrics = backtest.get("metrics", {}) if isinstance(backtest, dict) else {}
    return {
        "data_date": latest.get("data_date"),
        "source_updated_at": latest.get("updated_at"),
        "generated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"),
        "long_top10": merge(longs), "short_top10": merge(shorts),
        "backtest": {"period": backtest.get("period"), "metrics": metrics} if backtest else None,
        "optimization": latest.get("optimization", {}),
        "strategy_gate": latest.get("strategy_gate", {"enabled": False, "status": "未啟用｜保留基準訊號",
                                                        "directions": {"long": True, "short": True}}),
        "execution_eligibility": latest.get("execution_eligibility", {}),
        "quote_error": quote_error,
        "global_market": global_market,
        "disclaimer": "僅供研究、非投資建議",
    }


def _handler(root, quote_client, global_client):
    root, data, mobile = Path(root), Path(root) / "data", Path(root) / "mobile"
    class Handler(BaseHTTPRequestHandler):
        server_version = "TaiStockMobile/1.0"
        def log_message(self, fmt, *args):
            print(f"{self.client_address[0]} - {fmt % args}")
        def send_error(self, code, message=None, explain=None):
            self._json_error(code, message or self.responses.get(code, ("error",))[0])
        def _send(self, status, body, content_type, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            path = urlsplit(self.path).path
            no_store = path.startswith("/api/") or path == "/download/latest.csv"
            self.send_header("Cache-Control", "no-store" if no_store else "public, max-age=300")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Permissions-Policy", "geolocation=(), camera=(), microphone=()")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                             "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
                             "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            if extra:
                for key, value in extra.items(): self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        def _json_error(self, status, message):
            self._send(status, json.dumps({"error": message}, ensure_ascii=False).encode(), "application/json; charset=utf-8")
        def do_GET(self):
            raw_path = unquote(urlsplit(self.path).path)
            if ".." in raw_path.split("/"):
                return self._json_error(404, "not found")
            if raw_path == "/api/dashboard":
                latest = load_json(data / "latest.json", {})
                if not latest:
                    return self._json_error(503, "尚無 latest.json 資料")
                result = build_dashboard(latest, load_json(data / "backtest_100d.json", {}), quote_client, global_client)
                return self._send(200, json.dumps(result, ensure_ascii=False).encode(), "application/json; charset=utf-8")
            if raw_path.startswith("/download/"):
                name = raw_path[len("/download/"):]
                if name not in DOWNLOADS:
                    return self._json_error(404, "not found")
                if name == "latest.csv":
                    body = verified_latest_csv(load_json(data / "latest.json", {}))
                    if body is None:
                        return self._json_error(503, "當日官方可當沖資格尚未驗證")
                else:
                    if not (data / name).is_file():
                        return self._json_error(404, "not found")
                    body = (data / name).read_bytes()
                return self._send(200, body, "text/csv; charset=utf-8",
                                  {"Content-Disposition": f'attachment; filename="{name}"'})
            name = "index.html" if raw_path == "/" else raw_path.lstrip("/")
            if name not in STATIC or not (mobile / name).is_file():
                return self._json_error(404, "not found")
            content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type in ("application/json", "image/svg+xml"):
                content_type += "; charset=utf-8"
            return self._send(200, (mobile / name).read_bytes(), content_type)
        def do_HEAD(self):
            return self.do_GET()
        def do_POST(self):
            return self._json_error(405, "method not allowed")
    return Handler


def create_server(host, port, root, quote_client=None, global_client=None):
    return ThreadingHTTPServer((host, port), _handler(root, quote_client or RealtimeQuoteClient(),
                                                     global_client or GlobalMarketClient()))


def lan_ipv4():
    candidates = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80)); candidates.append(sock.getsockname()[0])
    except OSError:
        pass
    try:
        candidates.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    return next((ip for ip in candidates if not ip.startswith(("127.", "169.254."))), None)


def main(argv=None):
    parser = argparse.ArgumentParser(description="TaiStockScanner 同 Wi-Fi 手機 PWA 伺服器")
    parser.add_argument("--host", default="0.0.0.0"); parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args(argv)
    server = create_server(args.host, args.port, args.root)
    urls = [f"http://localhost:{server.server_port}/"]
    ip = lan_ipv4()
    if ip: urls.append(f"http://{ip}:{server.server_port}/")
    (args.root / "data").mkdir(parents=True, exist_ok=True)
    (args.root / "data" / "mobile_url.txt").write_text("\n".join(urls) + "\n", encoding="utf-8")
    print("手機版已啟動：" + " ｜ ".join(urls), flush=True)
    print("手機須與電腦同 Wi-Fi；電腦關機即無法連線。若無法連線，請檢查 Windows 防火牆提示。", flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
    return 0
