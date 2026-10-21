import json
import queue
import subprocess
import sys
import threading
import tkinter as tk
import socket
from datetime import datetime, timezone
from pathlib import Path
from tkinter import ttk, messagebox, filedialog
from .backup import create_backup, restore_backup
from .health import DAILY_UPDATE_TIME, build_health_report, export_diagnostics
from .global_market import GlobalMarketClient
from .storage import load_result
from .realtime import RealtimeQuoteClient
from .mobile import lan_ipv4

BG, PANEL, FG, MUTED, ACCENT = "#0b1220", "#111c2f", "#e7edf7", "#8fa2bd", "#4da3ff"


def dashboard_structure():
    return {
        "primary_action": "更新全部資料",
        "tool_actions": ("更新現價", "更新夜盤", "執行回測", "開啟手機版",
                         "匯出診斷", "建立備份", "還原備份"),
        "cards": ("資料日", "待驗證偏多", "待驗證偏空", "即時報價", "策略驗證"),
        "tabs": ("待驗證偏多", "待驗證偏空", "所有觀察", "回測摘要", "夜盤脈動", "系統健康"),
    }


def signal_row_tags(direction, change, index):
    zebra = "even" if index % 2 == 0 else "odd"
    movement = "gain" if str(change).startswith("+") else "loss" if str(change).startswith("-") else "flat"
    return zebra, direction if direction in ("long", "short") else "flat", movement


def strategy_card_label(strategy_gate):
    if strategy_gate.get("applied_mode") == "incumbent_protective":
        return "未通過成本驗證"
    return "已啟用" if strategy_gate.get("enabled") and strategy_gate.get("cost_validated") else "基準策略"


def technical_research_text(row, direction):
    research = row.get("technical_research") or {}
    values = research.get("indicators") or {}
    if research.get("status") != "available":
        return "技術研究資料不足｜不計分"
    distance_key = "close_vs_prior5_high_pct" if direction == "long" else "close_vs_prior5_low_pct"
    distance_label = "距前5日高" if direction == "long" else "距前5日低"
    distance = values.get(distance_key)
    distance_text = "—" if distance is None else f"{float(distance):+g}%"
    rsi = "—" if values.get("rsi_5") is None else f"{float(values['rsi_5']):g}"
    atr = "—" if values.get("atr_5_pct") is None else f"{float(values['atr_5_pct']):g}%"
    text = f"研究不計分｜RSI5 {rsi}｜ATR5 {atr}｜{distance_label} {distance_text}"
    body = values.get("candle_body_pct")
    change = values.get("close_change_pct")
    if isinstance(body, (int, float)) and isinstance(change, (int, float)):
        candle = "紅K" if body > 0 else "黑K" if body < 0 else "十字K"
        text += f"｜{candle} {float(body):+g}%｜較前收 {float(change):+g}%"
    conflict = row.get("candlestick_research") or {}
    if conflict.get("status") == "direction_conflict":
        text += f"｜⚠ {conflict.get('message', 'K線方向衝突；不直接改分或排除')}"
    return text


def broker_risk_text(row):
    research = row.get("broker_risk_research") or {}
    if research.get("status") != "available":
        return "分點資料不足｜不計分"
    if not research.get("warning"):
        return "未達警示門檻｜不計分"
    branches = ([f"買超 {item.get('branch', '')}" for item in research.get("watched_buyers", [])] +
                [f"賣超 {item.get('branch', '')}" for item in research.get("watched_sellers", [])])
    level = "高度注意" if research.get("risk_level") == "high" else "注意"
    return f"{level}｜{','.join(filter(None, branches))}｜非官方認定｜不計分"


def run_desktop_update(data_dir):
    """Use the same validated Taipei-boundary pipeline as the scheduled CLI update."""
    root = Path(data_dir).parent
    completed = subprocess.run([sys.executable, str(root / "app.py"), "--update"],
                               cwd=root, text=True, capture_output=True)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "更新失敗").strip())
    result = load_result(Path(data_dir))
    if not result:
        raise RuntimeError("更新完成但找不到有效結果")
    return result


def _desktop_version(root):
    try:
        return str(json.loads((Path(root) / "software_manifest.json").read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return "unknown"


def format_health_report(report):
    overall = {"ok": "正常", "warning": "需注意", "error": "需處理"}.get(report.get("overall"), "未知")
    pipeline = report.get("pipeline", {})
    strategy = report.get("strategy", {})
    universe = report.get("universe", {})
    night = report.get("night_market", {})
    lines = [
        f"整體狀態｜{overall}",
        f"桌面版本｜{report.get('desktop_version', '—')}",
        f"最新資料日｜{report.get('latest_data_date') or '—'}",
        f"每日流程｜{pipeline.get('status', 'unknown')}｜完成時間 {pipeline.get('completed_at') or '—'}",
        f"策略版本｜v{strategy.get('version') or '—'}｜啟用日 {strategy.get('activated_on') or '—'}｜前瞻進度 {strategy.get('progress', '0/0')}",
        f"候選宇宙｜總數 / 入選 {universe.get('total', 0)} / {universe.get('selected', 0)}",
        f"夜盤研究快照｜{night.get('count', 0)}/{night.get('threshold', 60)}｜尚需 {night.get('remaining', 60)} 筆",
        f"下次本機更新｜{report.get('next_update', '—')}（台北 {DAILY_UPDATE_TIME.strftime('%H:%M')}）",
        f"最近錯誤｜{report.get('recent_error') or '無'}",
        "",
        "資料來源",
    ]
    for name, source in report.get("sources", {}).items():
        lines.append(f"{name}｜{source.get('status', 'unknown')}｜{source.get('date') or '—'}｜{source.get('message', '')}")
    return "\n".join(lines)


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


def global_market_layers(payload):
    scenario = payload.get("scenario", {})
    notice = "期交所行情，可能延遲，非投資建議｜情境僅供參考，不加入現有策略分數"
    if payload.get("status") != "ok":
        notice = f"{payload.get('message', 'TAIFEX 行情更新失敗')}｜{notice}"
    return {
        "scenario": scenario.get("bias", "中性"),
        "basis": f"依據｜{scenario.get('basis', 'TX / UNF / SXF 尚無完整資料')}",
        "fetched_at": f"抓取時間｜{payload.get('fetched_at', '—')}",
        "notice": notice,
    }


def format_global_market(payload):
    layers = global_market_layers(payload)
    scenario_text = f"情境：{layers['scenario']}｜{layers['basis'][3:]}"
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
    period = result["period"]
    scenarios = result.get("metrics_scenarios")
    lines = [f"回測期間  {period['from']} ～ {period['to']}",
             "次一交易日限定當沖｜收盤強制沖銷｜嚴禁跨日持有",
             "100日成本後績效｜訊號批次，不是帳戶報酬"]

    def pf_text(value):
        if value is None:
            return "無虧損樣本"
        return f"{value:.2f}" if isinstance(value, (int, float)) else "—"

    def row(label, metrics):
        return (f"{label}  成交 {metrics.get('trades', 0)}｜勝率 {metrics.get('win_rate', 0):.2%}｜"
                f"平均 {metrics.get('average_return', metrics.get('average_net_return', 0)):.2%}｜"
                f"PF {pf_text(metrics.get('profit_factor'))}｜最大回撤 {metrics.get('max_drawdown', 0):.2%}")

    if scenarios:
        costs = {"gross": 0.0, "standard": 0.00435, "stress": 0.00535, **result.get("costs", {})}
        labels = {
            "gross": "gross（未扣成本）",
            "standard": f"standard {costs['standard']:.3%}",
            "stress": f"stress {costs['stress']:.3%}",
        }
        for name in ("gross", "standard", "stress"):
            metrics = scenarios.get(name)
            if not metrics:
                continue
            lines.append(labels[name])
            lines.append(row("整體", metrics))
            lines.append(row("偏多", metrics.get("long", {})))
            lines.append(row("偏空", metrics.get("short", {})))
    else:
        metrics = result["metrics"]
        lines.append("gross（舊報告，未扣成本）")
        lines.append(row("整體", metrics))
        lines.append(f"累積 {metrics.get('cumulative_return', 0):.2%}")
        for key, label in (("long", "偏多"), ("short", "偏空")):
            side = metrics.get(key, {})
            lines.append(row(label, side) + f"｜累積 {side.get('cumulative_return', 0):.2%}")
    lines.extend(["成本限制：standard 每筆成交扣雙邊總成本 0.435%；stress 扣 0.535%；未另計固定滑價、借券費與融券限制",
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
        self._build(); self._load_health(); self.after(120, self._poll)
        cached = load_result(data_dir)
        if cached:
            self._render(cached, "離線顯示上次成功結果"); self.refresh_quotes()
        self.after(60000, self._auto_refresh_quotes)
        self.refresh_global_market(); self.after(60000, self._auto_refresh_global_market)
        self._load_backtest()

    def _build(self):
        layout = dashboard_structure()
        header = tk.Frame(self, bg=BG); header.pack(fill="x", padx=20, pady=(16, 8))
        brand = tk.Frame(header, bg=BG); brand.pack(fill="x")
        title_box = tk.Frame(brand, bg=BG); title_box.pack(side="left", fill="x", expand=True)
        tk.Label(title_box, text="TAISTOCK DASHBOARD", fg=ACCENT, bg=BG,
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")
        tk.Label(title_box, text="台股多空觀察儀表板", fg=FG, bg=BG,
                 font=("Microsoft JhengHei UI", 24, "bold")).pack(anchor="w")
        self.button = tk.Button(brand, text=layout["primary_action"], command=self.update_data,
                                bg=ACCENT, activebackground="#78b9ff", fg="#06121f", relief="flat",
                                padx=24, pady=11, font=("Microsoft JhengHei UI", 12, "bold"))
        self.button.pack(side="right")
        tools = tk.Frame(header, bg=BG); tools.pack(fill="x", pady=(9, 0))
        tool_style = {"bg": "#19283f", "activebackground": "#263b59", "fg": FG, "relief": "flat",
                      "padx": 13, "pady": 6, "font": ("Microsoft JhengHei UI", 9, "bold")}
        self.quote_button = tk.Button(tools, text=layout["tool_actions"][0], command=self.refresh_quotes, **tool_style)
        self.global_market_button = tk.Button(tools, text=layout["tool_actions"][1], command=self.refresh_global_market, **tool_style)
        self.backtest_button = tk.Button(tools, text=layout["tool_actions"][2], command=self.run_backtest, **tool_style)
        self.mobile_button = tk.Button(tools, text=layout["tool_actions"][3], command=self.open_mobile, **tool_style)
        for widget in (self.quote_button, self.global_market_button, self.backtest_button, self.mobile_button):
            widget.pack(side="left", padx=(0, 7))
        tk.Label(tools, text="研究觀察用｜非投資建議｜不保證獲利｜不會自動下單",
                 fg="#fbbf24", bg=BG, font=("Microsoft JhengHei UI", 9)).pack(side="right")

        overview = tk.Frame(self, bg=BG); overview.pack(fill="x", padx=20, pady=(2, 9))
        tk.Label(overview, text="今日總覽", fg=FG, bg=BG,
                 font=("Microsoft JhengHei UI", 13, "bold")).pack(anchor="w", pady=(0, 5))
        cards = tk.Frame(overview, bg=BG); cards.pack(fill="x")
        self.card_vars, self.card_value_labels = [], []
        card_colors = (FG, "#fb7185", "#34d399", ACCENT, "#fbbf24")
        for title, color in zip(layout["cards"], card_colors):
            box = tk.Frame(cards, bg=PANEL, padx=15, pady=11, highlightthickness=1,
                           highlightbackground="#20324d")
            box.pack(side="left", fill="x", expand=True, padx=(0, 8))
            tk.Label(box, text=title, fg=MUTED, bg=PANEL,
                     font=("Microsoft JhengHei UI", 9, "bold")).pack(anchor="w")
            var = tk.StringVar(value="—"); self.card_vars.append(var)
            value_label = tk.Label(box, textvariable=var, fg=color, bg=PANEL, anchor="w",
                                   font=("Microsoft JhengHei UI", 18, "bold"))
            value_label.pack(fill="x", pady=(3, 0)); self.card_value_labels.append(value_label)
        tk.Label(self, text="篩選規則｜成交量前 100 · 量增 ≥ 20% · 偏多：收盤高於 MA5 且法人買超 · 偏空：收盤低於 MA5 且法人賣超",
                 fg=FG, bg="#16243a", padx=14, pady=8, anchor="w",
                 font=("Microsoft JhengHei UI", 9, "bold")).pack(fill="x", padx=20, pady=(0, 7))

        self.tabs = ttk.Notebook(self); self.tabs.pack(fill="both", expand=True, padx=20, pady=3)
        self.trees = []
        for label in layout["tabs"][:3]:
            frame = tk.Frame(self.tabs, bg=PANEL); self.tabs.add(frame, text=label)
            columns = ("rank", "direction", "code", "name", "price", "change", "quote", "entry", "stop", "target1", "target2", "risk", "score", "technical", "broker", "reason")
            tree = ttk.Treeview(frame, columns=columns, show="headings")
            heads = ("#", "方向", "代號", "名稱", "現價", "漲跌%", "報價時間/狀態", "觸發價（待驗證）", "停損/回補", "目標1", "目標2", "風險%", "研究排序", "技術研究（不計分）", "分點風險（不計分）", "原因")
            widths = (35, 55, 60, 80, 70, 70, 145, 115, 80, 75, 75, 60, 80, 300, 300, 390)
            for c, h, w in zip(columns, heads, widths):
                tree.heading(c, text=h); tree.column(c, width=w, minwidth=w, stretch=False,
                                                     anchor="w" if c == "reason" else "center")
            tree.tag_configure("even", background=PANEL); tree.tag_configure("odd", background="#16243a")
            tree.tag_configure("long", foreground="#fb7185"); tree.tag_configure("short", foreground="#34d399")
            tree.tag_configure("gain", foreground="#fb7185"); tree.tag_configure("loss", foreground="#34d399")
            tree.tag_configure("flat", foreground=FG)
            scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview); tree.configure(yscrollcommand=scroll.set)
            xscroll = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview); tree.configure(xscrollcommand=xscroll.set)
            scroll.pack(side="right", fill="y"); xscroll.pack(side="bottom", fill="x")
            tree.pack(fill="both", expand=True); self.trees.append(tree)
        backtest_frame = tk.Frame(self.tabs, bg=PANEL); self.tabs.add(backtest_frame, text=layout["tabs"][3])
        self.backtest_text = tk.Text(backtest_frame, bg=PANEL, fg=FG, insertbackground=FG, relief="flat",
                                     padx=24, pady=20, font=("Microsoft JhengHei UI", 12), state="disabled")
        self.backtest_text.pack(fill="both", expand=True)
        global_frame = tk.Frame(self.tabs, bg=PANEL); self.tabs.add(global_frame, text=layout["tabs"][4])
        tk.Label(global_frame, text="市場情境", fg=MUTED, bg=PANEL, anchor="w",
                 font=("Microsoft JhengHei UI", 9, "bold"), padx=18).pack(fill="x", pady=(12, 0))
        self.global_scenario = tk.StringVar(value="讀取中…")
        tk.Label(global_frame, textvariable=self.global_scenario, fg=ACCENT, bg=PANEL, anchor="w",
                 font=("Microsoft JhengHei UI", 20, "bold"), padx=18).pack(fill="x", pady=(1, 2))
        self.global_basis = tk.StringVar(value="依據｜讀取中…")
        tk.Label(global_frame, textvariable=self.global_basis, fg=FG, bg=PANEL, anchor="w",
                 font=("Microsoft JhengHei UI", 11), padx=18).pack(fill="x", pady=(0, 2))
        self.global_fetched_at = tk.StringVar(value="抓取時間｜—")
        tk.Label(global_frame, textvariable=self.global_fetched_at, fg=MUTED, bg=PANEL, anchor="w",
                 font=("Microsoft JhengHei UI", 9), padx=18).pack(fill="x", pady=(0, 9))
        market_columns = ("contract", "name", "price", "change", "volume")
        self.global_tree = ttk.Treeview(global_frame, columns=market_columns, show="headings", height=7)
        for column, heading, width in zip(market_columns, ("契約", "商品", "價格", "漲跌幅", "成交量"),
                                          (100, 220, 130, 120, 130)):
            self.global_tree.heading(column, text=heading); self.global_tree.column(column, width=width, anchor="center")
        self.global_tree.pack(fill="both", expand=True, padx=18)
        self.global_market_status = tk.StringVar(value="期交所行情讀取中…")
        tk.Label(global_frame, textvariable=self.global_market_status, fg="#fbbf24", bg=PANEL, anchor="w",
                 font=("Microsoft JhengHei UI", 10), padx=18, pady=12).pack(fill="x")

        health_frame = tk.Frame(self.tabs, bg=PANEL); self.tabs.add(health_frame, text=layout["tabs"][5])
        health_actions = tk.Frame(health_frame, bg=PANEL); health_actions.pack(fill="x", padx=18, pady=(14, 4))
        for label, command in ((layout["tool_actions"][4], self.export_diagnostics_action),
                               (layout["tool_actions"][5], self.create_backup_action),
                               (layout["tool_actions"][6], self.restore_backup_action)):
            tk.Button(health_actions, text=label, command=command, **tool_style).pack(side="left", padx=(0, 8))
        self.health_text = tk.Text(health_frame, bg=PANEL, fg=FG, insertbackground=FG, relief="flat",
                                   padx=24, pady=14, font=("Microsoft JhengHei UI", 11), state="disabled")
        self.health_text.pack(fill="both", expand=True)
        bottom = tk.Frame(self, bg=BG); bottom.pack(fill="x", padx=20, pady=(7, 14))
        self.progress = ttk.Progressbar(bottom, maximum=100); self.progress.pack(fill="x")
        self.status = tk.StringVar(value="就緒｜可離線開啟上次結果")
        tk.Label(bottom, textvariable=self.status, fg=MUTED, bg=BG, anchor="w", font=("Microsoft JhengHei UI", 9)).pack(fill="x", pady=(5, 0))

    def _load_health(self):
        root = self.data_dir.parent
        report = build_health_report(self.data_dir, _desktop_version(root), datetime.now(timezone.utc))
        self.health_text.configure(state="normal")
        self.health_text.delete("1.0", "end")
        self.health_text.insert("1.0", format_health_report(report))
        self.health_text.configure(state="disabled")
        return report

    def export_diagnostics_action(self):
        root = self.data_dir.parent
        now = datetime.now(timezone.utc)
        destination = root / "diagnostics" / f"health-{now.astimezone().strftime('%Y%m%d-%H%M%S')}.json"
        try:
            result = export_diagnostics(self.data_dir, _desktop_version(root), destination, now)
        except Exception as exc:
            messagebox.showerror("診斷匯出失敗", str(exc)); return
        messagebox.showinfo("匿名診斷已匯出", f"已建立：\n{result}\n\n不含股票清單、路徑、帳號或憑證。")

    def create_backup_action(self):
        root = self.data_dir.parent
        try:
            result = create_backup(root)
        except Exception as exc:
            messagebox.showerror("備份失敗", str(exc)); return
        messagebox.showinfo("備份完成", f"已建立 {result['file_count']} 個資料檔：\n{result['path']}")

    def restore_backup_action(self):
        selected = filedialog.askopenfilename(title="選擇 TaiStockScanner 備份",
                                              filetypes=(("ZIP 備份", "*.zip"), ("所有檔案", "*.*")))
        if not selected:
            return
        if not messagebox.askyesno("確認保守還原",
                                   "程式會先驗證備份；較新的現有資料不會被覆蓋。\n確定要繼續嗎？"):
            return
        root = self.data_dir.parent
        try:
            result = restore_backup(root, Path(selected))
        except Exception as exc:
            messagebox.showerror("還原失敗", str(exc)); return
        messagebox.showinfo("還原完成", f"已還原 {result['file_count']} 個資料檔。")
        if "health_text" in self.__dict__:
            self._load_health()

    def update_data(self):
        self.button.configure(state="disabled"); self.status.set("更新中…")
        def worker():
            try:
                self.events.put(("done", run_desktop_update(self.data_dir)))
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
        _, rows, status = format_global_market(payload)
        layers = global_market_layers(payload)
        self.global_scenario.set(layers["scenario"])
        self.global_basis.set(layers["basis"])
        self.global_fetched_at.set(layers["fetched_at"])
        self.global_tree.delete(*self.global_tree.get_children())
        for row in rows:
            self.global_tree.insert("", "end", values=row)
        self.global_market_status.set(layers["notice"])

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
                elif event[0] == "done": self._render(event[1], "更新成功"); self._load_health(); self.progress["value"] = 100; self.button.configure(state="normal")
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
        long_count = result.get("long_count", len([x for x in signals if x.get("signal_direction", "long") == "long"]))
        short_count = result.get("short_count", len([x for x in signals if x.get("signal_direction") == "short"]))
        self.card_vars[0].set(result.get("data_date", "—"))
        self.card_vars[1].set(f"{long_count} 檔")
        self.card_vars[2].set(f"{short_count} 檔")
        self.card_vars[3].set(f"{len(self.quotes)} 檔已更新" if self.quotes else "等待更新")
        self.card_vars[4].set(strategy_card_label(strategy_gate))
        for tree, rows in zip(self.trees, (longs, shorts, signals)):
            tree.delete(*tree.get_children())
            for i, x in enumerate(rows, 1):
                direction_key = x.get("signal_direction", "long")
                direction = "偏多" if direction_key == "long" else "偏空"
                price, change, quote = display_quote_values(self.quotes.get(str(x.get("code"))))
                tree.insert("", "end", values=(i, direction, x.get("code"), x.get("name"), price, change, quote, x.get("entry_trigger", "—"),
                            x.get("stop_loss", "—"), x.get("target1", "—"), x.get("target2", "—"),
                            x.get("risk_pct", "—"), x.get("score", "—"),
                            technical_research_text(x, direction_key), broker_risk_text(x), x.get("reason", "")),
                            tags=signal_row_tags(direction_key, change, i - 1))
        gate_status = strategy_gate.get("status", "未啟用｜保留基準訊號")
        learning_status = optimization.get("status", "尚無學習狀態")
        self.status.set(f"{prefix}｜資料 {result.get('data_date')}｜策略：{gate_status}｜學習：{learning_status}｜非投資建議｜不會自動下單")
