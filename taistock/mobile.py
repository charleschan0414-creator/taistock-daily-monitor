"""同一區網唯讀 PWA HTTP server。"""
import argparse
import json
import mimetypes
import socket
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .realtime import RealtimeQuoteClient, TAIPEI

DOWNLOADS = {"latest.csv", "backtest_100d.csv"}
STATIC = {"index.html", "manifest.json", "service-worker.js", "icon.svg"}


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def build_dashboard(latest, backtest, quote_client=None):
    longs = list(latest.get("long_top10", latest.get("top10", [])))[:10]
    shorts = list(latest.get("short_top10", []))[:10]
    rows = longs + shorts
    quote_error = None
    try:
        quotes = (quote_client or RealtimeQuoteClient()).fetch(rows) if rows else {}
    except Exception as exc:
        quotes, quote_error = {}, str(exc)
    def merge(items):
        return [dict(row, realtime=quotes.get(str(row.get("code")), {
            "price": None, "change_pct": None, "quote_time": None,
            "status": "報價暫時無法更新", "stale": True})) for row in items]
    metrics = backtest.get("metrics", {}) if isinstance(backtest, dict) else {}
    return {
        "data_date": latest.get("data_date"), "generated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"),
        "long_top10": merge(longs), "short_top10": merge(shorts),
        "backtest": {"period": backtest.get("period"), "metrics": metrics} if backtest else None,
        "optimization": latest.get("optimization", {}),
        "strategy_gate": latest.get("strategy_gate", {"enabled": False, "status": "未啟用｜保留基準訊號",
                                                        "directions": {"long": True, "short": True}}),
        "quote_error": quote_error,
        "disclaimer": "僅供研究、非投資建議",
    }


def _handler(root, quote_client):
    root, data, mobile = Path(root), Path(root) / "data", Path(root) / "mobile"
    class Handler(BaseHTTPRequestHandler):
        server_version = "TaiStockMobile/1.0"
        def log_message(self, fmt, *args):
            print(f"{self.client_address[0]} - {fmt % args}")
        def _send(self, status, body, content_type, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store" if self.path.startswith("/api/") else "public, max-age=300")
            self.send_header("X-Content-Type-Options", "nosniff")
            if extra:
                for key, value in extra.items(): self.send_header(key, value)
            self.end_headers(); self.wfile.write(body)
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
                result = build_dashboard(latest, load_json(data / "backtest_100d.json", {}), quote_client)
                return self._send(200, json.dumps(result, ensure_ascii=False).encode(), "application/json; charset=utf-8")
            if raw_path.startswith("/download/"):
                name = raw_path[len("/download/"):]
                if name not in DOWNLOADS or not (data / name).is_file():
                    return self._json_error(404, "not found")
                return self._send(200, (data / name).read_bytes(), "text/csv; charset=utf-8",
                                  {"Content-Disposition": f'attachment; filename="{name}"'})
            name = "index.html" if raw_path == "/" else raw_path.lstrip("/")
            if name not in STATIC or not (mobile / name).is_file():
                return self._json_error(404, "not found")
            content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type in ("application/json", "image/svg+xml"):
                content_type += "; charset=utf-8"
            return self._send(200, (mobile / name).read_bytes(), content_type)
    return Handler


def create_server(host, port, root, quote_client=None):
    return ThreadingHTTPServer((host, port), _handler(root, quote_client or RealtimeQuoteClient()))


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
