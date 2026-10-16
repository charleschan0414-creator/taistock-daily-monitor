import json
import queue
import subprocess
import sys
import threading
import tkinter as tk
import socket
from datetime import date, timedelta
from tkinter import ttk, messagebox
from .backtest import OfficialHistoryFetcher
from .global_market import GlobalMarketClient
from .net import HttpClient
from .scanner import Scanner
from .storage import load_optimizer_state, load_result, save_result
from .strategy_optimize import load_strategy_gate
from .realtime import RealtimeQuoteClient
from .mobile import lan_ipv4

BG, PANEL, FG, MUTED, ACCENT = "#0b1220", "#111c2f", "#e7edf7", "#8fa2bd", "#4da3ff"


class BackgroundRefreshGate:
    def __init__(self):
        self._lock = threading.Lock()
        self._running = False

    def try_start(self):
        with self._lock:
            if self._running:
                return False
            self._running = True
            return True

    def finish(self):
        with self._lock:
            self._running = False


def visible_signal_rows(result):
    rows, seen = [], set()
    for row in list(result.get("long_top10", result.get("top10", [])))[:10] + list(result.get("short_top10", []))[:10]:
        code = str(row.get("code", ""))
        if code and code not in seen:
            rows.append(row); seen.add(code)
    return rows


def display_quote_values(quote):
    if not quote:
        return "—", "—", "尚未更新"
    price = "—" if quote.get("price") is None else f"{quote['price']:g}"
    pct = quote.get("change_pct")
    change = "—" if pct is None else f"{pct:+.2f}%"
    moment = quote.get("quote_time") or ""
    clock = moment[11:19] if len(moment) >= 19 else "—"
    return price, change, f"{clock} {quote.get('status', '')}".strip()


def format_global_market(payload):
    scenario = payload.get("scenario", {})
    scenario_text = f"情境：{scenario.get('bias', '中性')}｜{scenario.get('basis', 'TX / UNF / SXF 尚無完整資料')}"
    rows = []
    for item in payload.get("items", []):
        pct = item.get("change_pct")
        rows.append((item.get("contract", item.get("symbol", "—")), item.get("contract_name", "—"),
                     f"{item['price']:,.10g}" if item.get("price") is not None else "—",
                     f"{pct:+.2f}%" if pct is not None else "—",
                     f"{item['volume']:,}" if item.get("volume") is not None else "—"))
    status = (f"系統抓取時間 {payload.get('fetched_at', '—')}｜期交所行情，可能延遲，非投資建議｜"
              "情境僅供參考，不加入現有策略分數")
    if payload.get("status") != "ok":
        status = f"{payload.get('message', 'TAIFEX 行情更新失敗')}｜{status}"
    return scenario_text, rows, status


def format_backtest_summary(result):
    period, metrics = result["period"], result["metrics"]
    lines = [
        f"回測期間  {period['from']} ～ {period['to']}",
        f"整體  訊號 {metrics.get('signals', metrics.get('trades', 0))}｜成交 {metrics['trades']}｜未觸發 {metrics.get('untriggered', 0)}｜未結算 {metrics.get('unresolved', 0)}",
        f"勝率 {metrics['win_rate']:.2%}｜平均 {metrics.get('average_return', metrics.get('average_net_return', 0)):.2%}｜中位數 {metrics.get('median_return', 0):.2%}｜累積 {metrics['cumulative_return']:.2%}｜最大回撤 {metrics['max_drawdown']:.2%}",
    ]
    for key, label in (("long", "偏多"), ("short", "偏空")):
        side = metrics[key]
        lines.append(f"{label}  訊號 {side['signals']}｜成交 {side['trades']}｜勝率 {side['win_rate']:.2%}｜累積 {side['cumulative_return']:.2%}")
    lines.extend(["成本：未計手續費、交易稅、滑價、借券費與融券限制",
                  f"最後完成  {result.get('completed_at', '—')}",
                  "歷史回測不代表未來績效，不保證獲利。"])
    return "\n\n".join(lines)


class Dashboard(tk.Tk):
    def __init__(self, data_dir):
        super().__init__(); self.data_dir = data_dir; self.events = queue.Queue(); self.current_result = None; self.quotes = {}
        self.global_market_gate = BackgroundRefreshGate()
        self.title("TaiStock Monitor｜台股多空規則式觀察"); self.geometry("1420x820"); self.minsize(1080, 680); self.configure(bg=BG)
        style = ttk.Style(self); style.theme_use("clam")
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG, rowheight=27, borderwidth=0)
        style.configure("Treeview.Heading", background="#19283f", foreground=FG, font=("Microsoft JhengHei UI", 10, "bold"))
        style.map("Treeview", background=[("selected", "#234a72")])
        self._build(); self.after(120, self._poll)
        cached = load_result(data_dir)
        if cached:
            self._render(cached, "離線顯示上次成功結果"); self.refresh_quotes()
        self.after(60000, self._auto_refresh_quotes)
        self.refresh_global_market(); self.after(60000, self._auto_refresh_global_market)
        self._load_backtest()

    def _build(self):
        header = tk.Frame(self, bg=BG); header.pack(fill="x", padx=18, pady=(16, 8))
        tk.Label(header, text="TAISTOCK / DAILY MONITOR", fg=ACCENT, bg=BG, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        row = tk.Frame(header, bg=BG); row.pack(fill="x")
        tk.Label(row, text="台股多空規則式觀察", fg=FG, bg=BG, font=("Microsoft JhengHei UI", 24, "bold")).pack(side="left")
        self.button = tk.Button(row, text="立即更新", command=self.update_data, bg=ACCENT, fg="#06121f", relief="flat", padx=20, pady=8,
                                font=("Microsoft JhengHei UI", 11, "bold")); self.button.pack(side="right")
        self.backtest_button = tk.Button(row, text="100日回測", command=self.run_backtest, bg="#22c55e", fg="#06121f",
                                         relief="flat", padx=20, pady=8,
                                         font=("Microsoft JhengHei UI", 11, "bold")); self.backtest_button.pack(side="right", padx=(0, 8))
        self.quote_button = tk.Button(row, text="更新現價", command=self.refresh_quotes, bg="#f59e0b", fg="#06121f",
                                      relief="flat", padx=18, pady=8, font=("Microsoft JhengHei UI", 11, "bold"))
        self.quote_button.pack(side="right", padx=(0, 8))
        self.global_market_button = tk.Button(row, text="更新期貨", command=self.refresh_global_market, bg="#2dd4bf", fg="#06121f",
                                              relief="flat", padx=18, pady=8, font=("Microsoft JhengHei UI", 11, "bold"))
        self.global_market_button.pack(side="right", padx=(0, 8))
        self.mobile_button = tk.Button(row, text="手機版", command=self.open_mobile, bg="#a78bfa", fg="#06121f",
                                       relief="flat", padx=18, pady=8, font=("Microsoft JhengHei UI", 11, "bold"))
        self.mobile_button.pack(side="right", padx=(0, 8))
        tk.Label(header, text="僅供規則式研究觀察，非投資建議，不保證獲利；不會自動下單。每天 08:30 使用最近完整交易日資料。",
                 fg="#fbbf24", bg=BG, font=("Microsoft JhengHei UI", 10)).pack(anchor="w", pady=(5, 0))
        cards = tk.Frame(self, bg=BG); cards.pack(fill="x", padx=18, pady=8)
        self.card_vars = []
        for title in ("資料日期", "成交量池", "偏多數量", "偏空數量", "自我優化", "策略閘門"):
            box = tk.Frame(cards, bg=PANEL, padx=14, pady=10); box.pack(side="left", fill="x", expand=True, padx=(0, 8))
            tk.Label(box, text=title, fg=MUTED, bg=PANEL, font=("Microsoft JhengHei UI", 9)).pack(anchor="w")
            var = tk.StringVar(value="—"); self.card_vars.append(var)
            tk.Label(box, textvariable=var, fg=FG, bg=PANEL, wraplength=250, justify="left",
                     font=("Microsoft JhengHei UI", 13, "bold")).pack(anchor="w")
        tk.Label(self, text="門檻｜4碼普通股 · 成交量前100 · 量增≥20% · 偏多：收盤>MA5且法人買超 · 偏空：收盤<MA5且法人賣超",
                 fg=FG, bg="#16243a", padx=14, pady=9, anchor="w", font=("Microsoft JhengHei UI", 10, "bold")).pack(fill="x", padx=18, pady=4)
        tk.Label(self, text="風險幅度=5日收盤日報酬絕對值平均（限1.5%–5%）；觸發偏移=波動代理÷3（限0.3%–1%）；目標為1.5R/2R，價格依台股跳動單位四捨五入。",
                 fg=MUTED, bg=BG, anchor="w", font=("Microsoft JhengHei UI", 9)).pack(fill="x", padx=20, pady=(2, 8))
        tabs = ttk.Notebook(self); tabs.pack(fill="both", expand=True, padx=18, pady=4)
        self.trees = []
        for label in ("偏多前10", "偏空前10", "全部訊號"):
            frame = tk.Frame(tabs, bg=PANEL); tabs.add(frame, text=label)
            columns = ("rank", "direction", "code", "name", "price", "change", "quote", "entry", "stop", "target1", "target2", "risk", "score", "reason")
            tree = ttk.Treeview(frame, columns=columns, show="headings")
            heads = ("#", "方向", "代號", "名稱", "現價", "漲跌%", "報價時間/狀態", "進場觸發", "停損/回補", "目標1", "目標2", "風險%", "評分", "原因")
            widths = (35, 55, 60, 80, 70, 70, 145, 80, 80, 75, 75, 60, 60, 390)
            for c, h, w in zip(columns, heads, widths):
                tree.heading(c, text=h); tree.column(c, width=w, anchor="w" if c == "reason" else "center")
            scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview); tree.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y"); tree.pack(fill="both", expand=True); self.trees.append(tree)
        backtest_frame = tk.Frame(tabs, bg=PANEL); tabs.add(backtest_frame, text="100日回測")
        self.backtest_text = tk.Text(backtest_frame, bg=PANEL, fg=FG, insertbackground=FG, relief="flat",
                                     padx=24, pady=20, font=("Microsoft JhengHei UI", 12), state="disabled")
        self.backtest_text.pack(fill="both", expand=True)
        global_frame = tk.Frame(tabs, bg=PANEL); tabs.add(global_frame, text="美股連動與台股夜盤")
        self.global_scenario = tk.StringVar(value="情境：讀取中…")
        tk.Label(global_frame, textvariable=self.global_scenario, fg=FG, bg=PANEL, anchor="w",
                 font=("Microsoft JhengHei UI", 13, "bold"), padx=18, pady=12).pack(fill="x")
        market_columns = ("contract", "name", "price", "change", "volume")
        self.global_tree = ttk.Treeview(global_frame, columns=market_columns, show="headings", height=7)
        for column, heading, width in zip(market_columns, ("契約", "商品", "價格", "漲跌幅", "成交量"),
                                          (100, 220, 130, 120, 130)):
            self.global_tree.heading(column, text=heading); self.global_tree.column(column, width=width, anchor="center")
        self.global_tree.pack(fill="both", expand=True, padx=18)
        self.global_market_status = tk.StringVar(value="期交所行情讀取中…")
        tk.Label(global_frame, textvariable=self.global_market_status, fg="#fbbf24", bg=PANEL, anchor="w",
                 font=("Microsoft JhengHei UI", 10), padx=18, pady=12).pack(fill="x")
        bottom = tk.Frame(self, bg=BG); bottom.pack(fill="x", padx=18, pady=(7, 14))
        self.progress = ttk.Progressbar(bottom, maximum=100); self.progress.pack(fill="x")
        self.status = tk.StringVar(value="就緒｜可離線開啟上次結果")
        tk.Label(bottom, textvariable=self.status, fg=MUTED, bg=BG, anchor="w", font=("Microsoft JhengHei UI", 9)).pack(fill="x", pady=(5, 0))

    def update_data(self):
        self.button.configure(state="disabled"); self.status.set("更新中…")
        def worker():
            try:
                state = load_optimizer_state(self.data_dir)
                strategy_gate = load_strategy_gate(self.data_dir)
                fetcher = OfficialHistoryFetcher(
                    HttpClient(timeout=35, retries=6), self.data_dir / "backtest_cache", throttle=0.35,
                    progress=lambda text, ratio: self.events.put(("progress", text, int(ratio * 75))))
                histories = fetcher.collect((date.today() - timedelta(days=1)).isoformat(), 6)
                scanner = Scanner(HttpClient(), progress=lambda text, pct: self.events.put(("progress", text, pct)),
                                  weights=state["weights"], optimization=state, strategy_gate=strategy_gate)
                result = scanner.run_from_days(histories)
                result["data_collection"] = {"complete_trading_days": len(histories),
                                             "network_requests": fetcher.requests,
                                             "cache_hits": fetcher.cache_hits}
                result = save_result(result, self.data_dir)
                self.events.put(("done", result))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def _load_backtest(self):
        path = self.data_dir / "backtest_100d.json"
        text = "尚無100日回測結果。按右上方「100日回測」執行；下載在背景進行，可續跑。"
        if path.exists():
            try:
                text = format_backtest_summary(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, KeyError):
                text = "回測結果檔無法讀取，請重新執行。"
        self.backtest_text.configure(state="normal")
        self.backtest_text.delete("1.0", "end")
        self.backtest_text.insert("1.0", text)
        self.backtest_text.configure(state="disabled")

    def run_backtest(self):
        self.backtest_button.configure(state="disabled")
        self.status.set("100日回測背景執行中；正在抓取／重用官方快取…")
        def worker():
            root = self.data_dir.parent
            completed = subprocess.run([sys.executable, str(root / "app.py"), "--backtest", "100"],
                                       cwd=root, text=True, capture_output=True)
            if completed.returncode == 0:
                self.events.put(("backtest_done",))
            else:
                self.events.put(("backtest_error", (completed.stderr or completed.stdout).strip()))
        threading.Thread(target=worker, daemon=True).start()

    def refresh_quotes(self):
        if not self.current_result:
            self.status.set("尚無清單可更新現價"); return
        self.quote_button.configure(state="disabled"); self.status.set("現價更新中…")
        rows = visible_signal_rows(self.current_result)
        def worker():
            try:
                self.events.put(("quotes_done", RealtimeQuoteClient().fetch(rows)))
            except Exception as exc:
                self.events.put(("quotes_error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def _auto_refresh_quotes(self):
        self.refresh_quotes(); self.after(60000, self._auto_refresh_quotes)

    def refresh_global_market(self):
        if not self.global_market_gate.try_start():
            return
        self.global_market_button.configure(state="disabled")
        def worker():
            self.events.put(("global_market_done", GlobalMarketClient().fetch()))
        threading.Thread(target=worker, daemon=True).start()

    def _auto_refresh_global_market(self):
        self.refresh_global_market(); self.after(60000, self._auto_refresh_global_market)

    def _render_global_market(self, payload):
        scenario, rows, status = format_global_market(payload)
        self.global_scenario.set(scenario)
        self.global_tree.delete(*self.global_tree.get_children())
        for row in rows:
            self.global_tree.insert("", "end", values=row)
        self.global_market_status.set(status)

    def open_mobile(self):
        root = self.data_dir.parent
        running = False
        try:
            with socket.create_connection(("127.0.0.1", 8765), timeout=.3): running = True
        except OSError:
            pass
        if not running:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen([sys.executable, str(root / "mobile_server.py")], cwd=root, creationflags=flags)
        ip = lan_ipv4(); url = f"http://{ip}:8765/" if ip else "http://localhost:8765/"
        win = tk.Toplevel(self); win.title("手機版 PWA"); win.geometry("510x300"); win.configure(bg=BG)
        tk.Label(win, text="同 Wi-Fi 手機版", fg=FG, bg=BG, font=("Microsoft JhengHei UI", 18, "bold")).pack(pady=(20, 8))
        value = tk.StringVar(value=url)
        tk.Entry(win, textvariable=value, width=46, font=("Segoe UI", 11)).pack(padx=20, pady=8)
        tk.Button(win, text="複製網址", command=lambda: (win.clipboard_clear(), win.clipboard_append(url)), bg=ACCENT, relief="flat", padx=16, pady=7).pack()
        text = ("Android：用 Chrome 開啟網址 → 選單 → 加入主畫面\n"
                "iPhone：用 Safari 開啟網址 → 分享 → 加入主畫面\n\n"
                "手機與電腦必須在同一 Wi-Fi，且電腦需保持開機。\n"
                "若無法連線，請允許 Windows 防火牆的私人網路存取；程式不會自行修改防火牆。")
        tk.Label(win, text=text, justify="left", fg=MUTED, bg=BG, wraplength=460, font=("Microsoft JhengHei UI", 10)).pack(padx=20, pady=16)

    def _poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "progress": self.status.set(event[1]); self.progress["value"] = event[2]
                elif event[0] == "done": self._render(event[1], "更新成功"); self.progress["value"] = 100; self.button.configure(state="normal")
                elif event[0] == "backtest_done":
                    self._load_backtest(); self.backtest_button.configure(state="normal"); self.status.set("100日回測完成")
                elif event[0] == "backtest_error":
                    self.status.set("回測失敗｜" + event[1]); self.backtest_button.configure(state="normal"); messagebox.showerror("回測失敗", event[1])
                elif event[0] == "quotes_done":
                    self.quotes.update(event[1]); self._render(self.current_result, "現價更新成功"); self.quote_button.configure(state="normal")
                elif event[0] == "quotes_error":
                    self.status.set("現價更新失敗，保留最後報價｜" + event[1]); self.quote_button.configure(state="normal")
                elif event[0] == "global_market_done":
                    self._render_global_market(event[1]); self.global_market_gate.finish(); self.global_market_button.configure(state="normal")
                else: self.status.set("更新失敗｜" + event[1]); self.button.configure(state="normal"); messagebox.showerror("資料更新失敗", event[1])
        except queue.Empty:
            pass
        self.after(120, self._poll)

    def _render(self, result, prefix):
        self.current_result = result
        longs = result.get("long_top10", result.get("top10", []))
        shorts = result.get("short_top10", [])
        signals = result.get("signals", result.get("qualified", []))
        optimization = result.get("optimization", {})
        strategy_gate = result.get("strategy_gate", {})
        self.card_vars[0].set(result.get("data_date", "—")); self.card_vars[1].set(f"{result.get('top100_count', 0)} 檔")
        self.card_vars[2].set(f"{result.get('long_count', len([x for x in signals if x.get('signal_direction', 'long') == 'long']))} 檔")
        self.card_vars[3].set(f"{result.get('short_count', len([x for x in signals if x.get('signal_direction') == 'short']))} 檔")
        self.card_vars[4].set(optimization.get("status", "尚無學習狀態"))
        self.card_vars[5].set(strategy_gate.get("status", "未啟用｜保留基準訊號"))
        for tree, rows in zip(self.trees, (longs, shorts, signals)):
            tree.delete(*tree.get_children())
            for i, x in enumerate(rows, 1):
                direction = "偏多" if x.get("signal_direction", "long") == "long" else "偏空"
                price, change, quote = display_quote_values(self.quotes.get(str(x.get("code"))))
                tree.insert("", "end", values=(i, direction, x.get("code"), x.get("name"), price, change, quote, x.get("entry_trigger", "—"),
                            x.get("stop_loss", "—"), x.get("target1", "—"), x.get("target2", "—"),
                            x.get("risk_pct", "—"), x.get("score", "—"), x.get("reason", "")))
        self.status.set(f"{prefix}｜資料 {result.get('data_date')}｜TWSE / TPEx 官方公開資料｜非投資建議")
