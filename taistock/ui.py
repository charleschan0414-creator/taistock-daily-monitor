import json
import math
import queue
import subprocess
import sys
import threading
import tkinter as tk
import socket
from datetime import date, datetime, timezone
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
        "cards": ("資料日／更新", "可執行偏多", "可執行偏空"),
        "tabs": ("偏多推薦", "偏空推薦", "研究與系統"),
        "detail_sections": ("回測", "夜盤", "系統與備份"),
        "secondary_menu": "更多功能",
    }


def desktop_visual_spec():
    """Centralize desktop density choices so they remain testable."""
    return {
        "table_row_height": 31,
        "stretch_column": "warning",
        "horizontal_scrollbar": False,
        "primary_button_width": 18,
    }


def signal_empty_state(direction, health_state):
    label = "偏多" if direction == "long" else "偏空"
    other = "偏空" if direction == "long" else "偏多"
    if health_state == "paused":
        return f"今日休市，沒有可執行的{label}訊號。\n可查看沿用觀察標的；今日不可執行。"
    if health_state in ("stale", "error"):
        return f"{label}清單目前不可執行。\n請先更新資料，確認官方資格與資料狀態。"
    return f"目前沒有符合官方資格的{label}推薦。\n可更新資料或查看{other}推薦。"


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


def candlestick_window_text(row):
    window = row.get("candlestick_window") or {}
    if window.get("status") == "insufficient_history" or not window:
        return "5日K線資料不足｜不計分"
    return (f"近3日型態｜{window.get('pattern_name', '未辨識')}｜"
            f"{window.get('explanation', '只供研究。')}｜不計分、不篩除")


def candle_chart_geometry(candles, width, height):
    """將正好5根OHLC轉成畫布座標，供桌面K線圖使用。"""
    rows = list(candles or [])
    if len(rows) != 5:
        return []
    try:
        lows = [float(row["low"]) for row in rows]
        highs = [float(row["high"]) for row in rows]
        opens = [float(row["open"]) for row in rows]
        closes = [float(row["close"]) for row in rows]
    except (KeyError, TypeError, ValueError):
        return []
    if not all(math.isfinite(value) and value > 0
               for values in zip(opens, highs, lows, closes) for value in values):
        return []
    if any(high < max(open_price, close) or low > min(open_price, close)
           for open_price, high, low, close in zip(opens, highs, lows, closes)):
        return []
    low, high = min(lows), max(highs)
    top, bottom = 10.0, max(11.0, float(height) - 24.0)
    span = high - low or 1.0
    y = lambda value: top + (high - value) / span * (bottom - top)
    spacing = max(1.0, (float(width) - 40.0) / len(rows))
    return [{
        "x": 20.0 + spacing * (index + 0.5),
        "high_y": y(highs[index]), "low_y": y(lows[index]),
        "open_y": y(opens[index]), "close_y": y(closes[index]),
        "bullish": closes[index] >= opens[index],
        "date": str(row.get("date", "")),
    } for index, row in enumerate(rows)]


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


def signal_warning_text(row):
    warnings = []
    candle = row.get("candlestick_research") or {}
    if candle.get("status") == "direction_conflict":
        warnings.append("爆量收黑K高風險" if candle.get("risk_level") == "high" else "K線方向衝突")
    broker = row.get("broker_risk_research") or {}
    if broker.get("warning"):
        warnings.append("分點高度注意" if broker.get("risk_level") == "high" else "分點注意")
    return "｜".join(warnings) if warnings else "無重大研究警示"


def execution_eligibility_text(execution):
    if not execution or not execution.get("enforced"):
        return "⚠ 官方當沖資格尚未驗證｜清單不可視為可執行"
    if execution.get("execution_for_signal_date") == "paused_no_new_official_session":
        return (f"⏸ {execution.get('requested_date', '今日')} 官方尚無新交易日資料｜"
                f"沿用 {execution.get('source_date', '—')} 觀察資料｜所有訊號暫停執行")
    reasons = {
        "sell_first_suspended": "暫停先賣後買",
        "not_day_trade_eligible": "非當沖標的",
    }
    excluded = []
    for row in execution.get("filtered_out", []):
        label = reasons.get(row.get("reason"), row.get("reason") or "資格不符")
        excluded.append(f"{row.get('code', '')}{row.get('name', '')}：{label}")
    count = execution.get("filtered_count", len(excluded))
    suffix = f"｜已排除 {count} 檔" + (f"：{'、'.join(excluded)}" if excluded else "")
    return f"✓ 官方資格已驗證 {execution.get('source_date', '—')}{suffix}"


def signal_table_columns():
    columns = ("rank", "direction", "code", "name", "quote", "entry", "stop", "target", "warning")
    headings = ("#", "方向", "代號", "名稱", "現價／狀態", "觸發價", "停損／回補", "目標1", "重要警示")
    widths = (36, 58, 62, 94, 145, 84, 84, 84, 200)
    return columns, headings, widths


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


class BusyOperationState:
    def __init__(self):
        self._operations = set()

    def set_busy(self, operation, busy):
        if busy:
            self._operations.add(operation)
        else:
            self._operations.discard(operation)

    @property
    def is_busy(self):
        return bool(self._operations)


def visible_signal_rows(result):
    rows, seen = [], set()
    for row in list(result.get("long_top10", result.get("top10", [])))[:10] + list(result.get("short_top10", []))[:10]:
        code = str(row.get("code", ""))
        if code and code not in seen:
            rows.append(row); seen.add(code)
    return rows


def desktop_execution_health(report, result):
    """Only canonical fresh, successful data may remain executable."""
    pipeline = (report or {}).get("pipeline") or {}
    if pipeline.get("status") != "success":
        return "error"
    if (pipeline.get("freshness") == "paused" and
            (result.get("market_session") or {}).get("status") == "paused_no_new_official_session"):
        return "paused"
    if pipeline.get("freshness") != "fresh":
        return "stale"
    if pipeline.get("data_date") != result.get("data_date"):
        return "stale"
    return "success"


def executable_signal_lists(result, health_state="success"):
    if health_state != "success":
        return [], []
    execution = result.get("execution_eligibility") or {}
    data_date = result.get("data_date")
    source_date = execution.get("source_date")
    if not execution.get("enforced"):
        return [], []
    try:
        source_day = date.fromisoformat(source_date)
        signal_day = date.fromisoformat(data_date)
    except (TypeError, ValueError):
        return [], []
    if source_date != source_day.isoformat() or data_date != signal_day.isoformat() or source_day < signal_day:
        return [], []

    def verified(rows):
        return [row for row in rows if
                (row.get("day_trading_eligibility") or {}).get("eligible") is True and
                (row.get("day_trading_eligibility") or {}).get("source_date") == source_date]

    longs = result.get("long_top10", result.get("top10", []))
    shorts = result.get("short_top10", [])
    return verified(longs), verified(shorts)


def _is_verified_carried_row(row, result):
    eligibility = row.get("day_trading_eligibility") or {}
    data_date = result.get("data_date")
    try:
        data_day = date.fromisoformat(data_date)
    except (TypeError, ValueError):
        return False
    return (data_date == data_day.isoformat()
            and eligibility.get("eligible") is False
            and eligibility.get("source_date") == data_date
            and eligibility.get("reason") in {"market_closed", "no_new_official_session"})


def display_signal_lists(result, health_state="success"):
    """Show only verified prior-session targets during closure, never executable rows."""
    if health_state == "success":
        return executable_signal_lists(result, health_state)
    execution = result.get("execution_eligibility") or {}
    session = result.get("market_session") or {}
    if (health_state == "paused" and execution.get("enforced") is True
            and execution.get("execution_for_signal_date") == "paused_no_new_official_session"
            and session.get("status") == "paused_no_new_official_session"
            and execution.get("source_date") == result.get("data_date")):
        longs = result.get("long_top10", result.get("top10", []))
        shorts = result.get("short_top10", [])
        return ([row for row in longs if _is_verified_carried_row(row, result)][:10],
                [row for row in shorts if _is_verified_carried_row(row, result)][:10])
    return [], []


def signal_count_presentation(health_state, executable_long, executable_short,
                              display_long, display_short):
    if health_state == "paused":
        return (("當沖偏多標的（沿用）", f"{display_long} 檔｜休市不可執行"),
                ("當沖偏空標的（沿用）", f"{display_short} 檔｜休市不可執行"))
    return (("可執行偏多", f"{executable_long} 檔"),
            ("可執行偏空", f"{executable_short} 檔"))


def row_eligibility_text(row, result):
    eligibility = row.get("day_trading_eligibility") or {}
    session = result.get("market_session") or {}
    execution = result.get("execution_eligibility") or {}
    data_date = result.get("data_date")
    if (session.get("status") == "paused_no_new_official_session"
            and execution.get("enforced") is True
            and execution.get("execution_for_signal_date") == "paused_no_new_official_session"
            and execution.get("source_date") == data_date
            and _is_verified_carried_row(row, result)):
        return f"{data_date} 當沖資格已通過並沿用｜今日休市不可執行"
    status = "已通過" if eligibility.get("eligible") else "未通過／不明"
    return f"{status}｜來源日 {eligibility.get('source_date', '—')}"


def data_update_label(prefix, market_session=None):
    if (market_session or {}).get("status") == "paused_no_new_official_session":
        return "休市／暫停沿用"
    if prefix == "更新成功":
        return "成功"
    if prefix == "現價更新成功":
        return "資料未變"
    return "上次結果"


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
        self.tool_busy = BusyOperationState()
        self.title("TaiStock Monitor｜台股多空規則式觀察"); self.geometry("1180x780"); self.minsize(960, 640); self.configure(bg=BG)
        visual = desktop_visual_spec()
        style = ttk.Style(self); style.theme_use("clam")
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG,
                        rowheight=visual["table_row_height"], borderwidth=0,
                        font=("Microsoft JhengHei UI", 10))
        style.configure("Treeview.Heading", background="#19283f", foreground=FG, font=("Microsoft JhengHei UI", 10, "bold"))
        style.map("Treeview", background=[("selected", "#234a72")])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background="#16243a", foreground=MUTED,
                        padding=(16, 9), font=("Microsoft JhengHei UI", 10, "bold"))
        style.map("TNotebook.Tab", background=[("selected", PANEL)], foreground=[("selected", FG)])
        style.configure("Vertical.TScrollbar", background="#263b59", troughcolor="#0f1929",
                        bordercolor="#0f1929", arrowcolor=MUTED)
        self._build(); self._load_health(); self.after(120, self._poll)
        cached = load_result(data_dir)
        if cached:
            self._render(cached, "離線顯示上次成功結果"); self.refresh_quotes()
        self.after(60000, self._auto_refresh_quotes)
        self.refresh_global_market(); self.after(60000, self._auto_refresh_global_market)
        self._load_backtest()

    def _build(self):
        layout = dashboard_structure()
        visual = desktop_visual_spec()
        header = tk.Frame(self, bg=BG); header.pack(fill="x", padx=20, pady=(16, 8))
        brand = tk.Frame(header, bg=BG); brand.pack(fill="x")
        title_box = tk.Frame(brand, bg=BG); title_box.pack(side="left", fill="x", expand=True)
        tk.Label(title_box, text="台股每日觀察", fg=FG, bg=BG,
                 font=("Microsoft JhengHei UI", 21, "bold")).pack(anchor="w")
        tk.Label(title_box, text="先看可執行推薦與重要風險；研究細節可再展開", fg=MUTED, bg=BG,
                 font=("Microsoft JhengHei UI", 9)).pack(anchor="w")
        self.button = tk.Button(brand, text=layout["primary_action"], command=self.update_data,
                                bg=ACCENT, activebackground="#78b9ff", fg="#06121f", relief="flat",
                                width=visual["primary_button_width"], padx=10, pady=8,
                                font=("Microsoft JhengHei UI", 11, "bold"))
        self.button.pack(side="right")
        tools = tk.Frame(header, bg=BG); tools.pack(fill="x", pady=(9, 0))
        tool_style = {"bg": "#19283f", "activebackground": "#263b59", "fg": FG, "relief": "flat",
                      "padx": 13, "pady": 6, "font": ("Microsoft JhengHei UI", 9, "bold")}
        self.more_button = tk.Menubutton(tools, text=f"{layout['secondary_menu']} ▾", **tool_style)
        tool_menu = tk.Menu(self.more_button, tearoff=False)
        for label, command in ((layout["tool_actions"][0], self.refresh_quotes),
                               (layout["tool_actions"][1], self.refresh_global_market),
                               (layout["tool_actions"][2], self.run_backtest),
                               (layout["tool_actions"][3], self.open_mobile),
                               (layout["tool_actions"][4], self.export_diagnostics_action),
                               (layout["tool_actions"][5], self.create_backup_action),
                               (layout["tool_actions"][6], self.restore_backup_action)):
            tool_menu.add_command(label=label, command=command)
        self.more_button.configure(menu=tool_menu); self.more_button.pack(side="left")
        tk.Label(tools, text="研究觀察用｜非投資建議｜不保證獲利｜不會自動下單",
                 fg="#fbbf24", bg=BG, font=("Microsoft JhengHei UI", 9)).pack(side="right")

        overview = tk.Frame(self, bg=BG); overview.pack(fill="x", padx=20, pady=(2, 9))
        tk.Label(overview, text="今日總覽", fg=FG, bg=BG,
                 font=("Microsoft JhengHei UI", 13, "bold")).pack(anchor="w", pady=(0, 5))
        cards = tk.Frame(overview, bg=BG); cards.pack(fill="x")
        self.card_vars, self.card_title_vars, self.card_value_labels = [], [], []
        card_colors = (FG, "#fb7185", "#34d399")
        for title, color in zip(layout["cards"], card_colors):
            box = tk.Frame(cards, bg=PANEL, padx=15, pady=11, highlightthickness=1,
                           highlightbackground="#20324d")
            box.pack(side="left", fill="x", expand=True, padx=(0, 8))
            title_var = tk.StringVar(value=title); self.card_title_vars.append(title_var)
            tk.Label(box, textvariable=title_var, fg=MUTED, bg=PANEL,
                     font=("Microsoft JhengHei UI", 9, "bold")).pack(anchor="w")
            var = tk.StringVar(value="—"); self.card_vars.append(var)
            value_label = tk.Label(box, textvariable=var, fg=color, bg=PANEL, anchor="w",
                                   font=("Microsoft JhengHei UI", 18, "bold"))
            value_label.pack(fill="x", pady=(3, 0)); self.card_value_labels.append(value_label)
        self.eligibility_var = tk.StringVar(value="官方當沖資格載入中…")
        self.eligibility_label = tk.Label(self, textvariable=self.eligibility_var,
                 fg="#fbbf24", bg="#16243a", padx=14, pady=8, anchor="w",
                 font=("Microsoft JhengHei UI", 9, "bold"))
        self.eligibility_label.pack(fill="x", padx=20, pady=(0, 7))

        self.tabs = ttk.Notebook(self); self.tabs.pack(fill="both", expand=True, padx=20, pady=3)
        self.trees, self.tree_rows = [], []
        self.candle_canvases, self.candle_detail_vars = [], []
        self.signal_detail_frames, self.signal_empty_labels = [], []
        for direction_key, label in zip(("long", "short"), layout["tabs"][:2]):
            frame = tk.Frame(self.tabs, bg=PANEL); self.tabs.add(frame, text=label)
            columns, heads, widths = signal_table_columns()
            tree = ttk.Treeview(frame, columns=columns, show="headings")
            for c, h, w in zip(columns, heads, widths):
                tree.heading(c, text=h); tree.column(c, width=w, minwidth=w,
                                                     stretch=c == visual["stretch_column"],
                                                     anchor="w" if c in ("quote", "warning") else "center")
            tree.tag_configure("even", background=PANEL); tree.tag_configure("odd", background="#16243a")
            tree.tag_configure("long", foreground="#fb7185"); tree.tag_configure("short", foreground="#34d399")
            tree.tag_configure("gain", foreground="#fb7185"); tree.tag_configure("loss", foreground="#34d399")
            tree.tag_configure("flat", foreground=FG)
            scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview); tree.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            tree_index = len(self.trees)
            tree.bind("<Double-1>", lambda _event, current=tree: self._show_signal_details(current))
            tree.bind("<<TreeviewSelect>>", lambda _event, current=tree: self._render_selected_signal(current))
            detail = tk.Frame(frame, bg="#0f1929", highlightthickness=1, highlightbackground="#263956")
            detail.pack(fill="x", side="bottom", padx=8, pady=(4, 7))
            canvas = tk.Canvas(detail, width=300, height=150, bg="#0f1929", highlightthickness=0)
            canvas.pack(side="left", padx=(10, 4), pady=7)
            detail_var = tk.StringVar(value="選取股票後顯示最近5日K線與3日型態說明。")
            tk.Label(detail, textvariable=detail_var, fg=FG, bg="#0f1929", justify="left",
                     anchor="nw", wraplength=720, padx=12, pady=12,
                     font=("Microsoft JhengHei UI", 10)).pack(side="left", fill="both", expand=True)
            tree.pack(fill="both", expand=True)
            empty_label = tk.Label(frame, text=signal_empty_state(direction_key, "success"),
                                   fg=FG, bg=PANEL, justify="center",
                                   font=("Microsoft JhengHei UI", 12, "bold"))
            self.trees.append(tree); self.tree_rows.append({})
            self.candle_canvases.append(canvas); self.candle_detail_vars.append(detail_var)
            self.signal_detail_frames.append(detail); self.signal_empty_labels.append(empty_label)
            tk.Label(frame, text="雙擊任一股票可查看入選原因、K線研究、分點資料與完整價位。",
                     fg=MUTED, bg=PANEL, anchor="w", padx=12, pady=7,
                     font=("Microsoft JhengHei UI", 9)).pack(fill="x", side="bottom")
        advanced_frame = tk.Frame(self.tabs, bg=PANEL); self.tabs.add(advanced_frame, text=layout["tabs"][2])
        self.detail_tabs = ttk.Notebook(advanced_frame); self.detail_tabs.pack(fill="both", expand=True)
        backtest_frame = tk.Frame(self.detail_tabs, bg=PANEL)
        self.detail_tabs.add(backtest_frame, text=layout["detail_sections"][0])
        self.backtest_text = tk.Text(backtest_frame, bg=PANEL, fg=FG, insertbackground=FG, relief="flat",
                                     padx=24, pady=20, font=("Microsoft JhengHei UI", 12), state="disabled")
        self.backtest_text.pack(fill="both", expand=True)
        global_frame = tk.Frame(self.detail_tabs, bg=PANEL)
        self.detail_tabs.add(global_frame, text=layout["detail_sections"][1])
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

        health_frame = tk.Frame(self.detail_tabs, bg=PANEL)
        self.detail_tabs.add(health_frame, text=layout["detail_sections"][2])
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

    def _show_signal_details(self, tree):
        selected = tree.selection()
        if not selected:
            return
        index = self.trees.index(tree)
        row = self.tree_rows[index].get(selected[0])
        if not row:
            return
        direction = row.get("signal_direction", "long")
        title = f"{row.get('code', '')} {row.get('name', '')}｜{'偏多' if direction == 'long' else '偏空'}"
        details = [
            f"官方當沖資格｜{row_eligibility_text(row, self.current_result or {})}",
            f"觸發價 {row.get('entry_trigger', '—')}｜停損／回補 {row.get('stop_loss', '—')}｜目標1 {row.get('target1', '—')}｜目標2 {row.get('target2', '—')}",
            f"研究排序｜{row.get('score', '—')}",
            f"入選原因｜{row.get('reason', '—')}",
            "",
            f"K線與技術｜{technical_research_text(row, direction)}",
            f"3日／5日K線｜{candlestick_window_text(row)}",
            f"分點風險｜{broker_risk_text(row)}",
            "",
            "規則式研究觀察，非投資建議；不保證獲利，不會自動下單。",
        ]
        messagebox.showinfo(title, "\n".join(details))

    def _render_selected_signal(self, tree):
        selected = tree.selection()
        if not selected:
            return
        index = self.trees.index(tree)
        row = self.tree_rows[index].get(selected[0])
        if not row:
            return
        canvas = self.candle_canvases[index]
        canvas.delete("all")
        candles = (row.get("technical_research") or {}).get("recent_candles") or []
        geometry = candle_chart_geometry(candles, 300, 150)
        if not geometry:
            canvas.create_text(150, 72, text="5日K線資料不足", fill=MUTED,
                               font=("Microsoft JhengHei UI", 11))
        for item in geometry:
            color = "#fb7185" if item["bullish"] else "#34d399"
            x = item["x"]
            canvas.create_line(x, item["high_y"], x, item["low_y"], fill=color, width=2)
            upper, lower = sorted((item["open_y"], item["close_y"]))
            if lower - upper < 2:
                lower = upper + 2
            canvas.create_rectangle(x - 10, upper, x + 10, lower, outline=color,
                                    fill=color if item["bullish"] else "#0f1929", width=2)
            canvas.create_text(x, 139, text=item["date"][-5:], fill=MUTED,
                               font=("Microsoft JhengHei UI", 7))
        self.candle_detail_vars[index].set(
            f"{row.get('code', '')} {row.get('name', '')}\n{candlestick_window_text(row)}\n"
            "圖為最近5個完整交易日；型態以最後3日優先。參考播放清單48種K棒架構，僅供研究。")

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
        self._set_tool_busy("backtest", True)
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
        self._set_tool_busy("quote", True); self.status.set("現價更新中…")
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
        self._set_tool_busy("market", True)
        def worker():
            self.events.put(("global_market_done", GlobalMarketClient().fetch()))
        threading.Thread(target=worker, daemon=True).start()

    def _auto_refresh_global_market(self):
        self.refresh_global_market(); self.after(60000, self._auto_refresh_global_market)

    def _set_tool_busy(self, operation, busy):
        self.tool_busy.set_busy(operation, busy)
        self.more_button.configure(state="disabled" if self.tool_busy.is_busy else "normal")

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
                    self._load_backtest(); self._set_tool_busy("backtest", False); self.status.set("100日回測完成")
                elif event[0] == "backtest_error":
                    self.status.set("回測失敗｜" + event[1]); self._set_tool_busy("backtest", False); messagebox.showerror("回測失敗", event[1])
                elif event[0] == "quotes_done":
                    self.quotes.update(event[1]); self._render(self.current_result, "現價更新成功"); self._set_tool_busy("quote", False)
                elif event[0] == "quotes_error":
                    self.status.set("現價更新失敗，保留最後報價｜" + event[1]); self._set_tool_busy("quote", False)
                elif event[0] == "global_market_done":
                    self._render_global_market(event[1]); self.global_market_gate.finish(); self._set_tool_busy("market", False)
                else: self._handle_data_update_error(event[1])
        except queue.Empty:
            pass
        self.after(120, self._poll)

    def _handle_data_update_error(self, message):
        if self.current_result:
            self._render(self.current_result, "更新失敗", health_state_override="error")
        self.status.set("更新失敗｜" + message)
        self.button.configure(state="normal")
        messagebox.showerror("資料更新失敗", message)

    def _render(self, result, prefix, health_state_override=None):
        self.current_result = result
        if health_state_override is not None:
            health_state = health_state_override
        else:
            try:
                health_state = desktop_execution_health(self._load_health(), result)
            except (OSError, ValueError, TypeError):
                health_state = "error"
        longs, shorts = executable_signal_lists(result, health_state=health_state)
        display_longs, display_shorts = display_signal_lists(result, health_state=health_state)
        signals = result.get("signals", result.get("qualified", []))
        optimization = result.get("optimization", {})
        strategy_gate = result.get("strategy_gate", {})
        long_count = result.get("long_count", len([x for x in signals if x.get("signal_direction", "long") == "long"]))
        short_count = result.get("short_count", len([x for x in signals if x.get("signal_direction") == "short"]))
        update_label = data_update_label(prefix, result.get("market_session"))
        self.card_vars[0].set(f"{result.get('data_date', '—')}｜{update_label}")
        count_cards = signal_count_presentation(
            health_state, len(longs), len(shorts), len(display_longs), len(display_shorts))
        for index, (title, value) in enumerate(count_cards, 1):
            self.card_title_vars[index].set(title)
            self.card_vars[index].set(value)
        execution = result.get("execution_eligibility") or {}
        self.eligibility_var.set(execution_eligibility_text(execution))
        paused = execution.get("execution_for_signal_date") == "paused_no_new_official_session"
        self.eligibility_label.configure(fg="#fbbf24" if paused else
                                         "#34d399" if execution.get("enforced") else "#fb7185")
        for tree_index, (tree, rows) in enumerate(zip(self.trees, (display_longs, display_shorts))):
            tree.delete(*tree.get_children())
            self.tree_rows[tree_index].clear()
            for i, x in enumerate(rows, 1):
                direction_key = x.get("signal_direction", "long")
                direction = "偏多" if direction_key == "long" else "偏空"
                price, change, quote = display_quote_values(self.quotes.get(str(x.get("code"))))
                quote_text = f"{price} {change}｜{quote}" if price != "—" else quote
                iid = str(i - 1)
                warning = signal_warning_text(x)
                if health_state == "paused":
                    warning = "休市沿用｜不可執行｜" + warning
                tree.insert("", "end", iid=iid,
                            values=(i, direction, x.get("code"), x.get("name"), quote_text,
                                    x.get("entry_trigger", "—"), x.get("stop_loss", "—"),
                                    x.get("target1", "—"), warning),
                            tags=signal_row_tags(direction_key, change, i - 1))
                self.tree_rows[tree_index][iid] = x
            children = tree.get_children()
            if children:
                self.signal_empty_labels[tree_index].place_forget()
                if not self.signal_detail_frames[tree_index].winfo_manager():
                    self.signal_detail_frames[tree_index].pack(fill="x", side="bottom", padx=8, pady=(4, 7))
                tree.selection_set(children[0]); tree.focus(children[0]); self._render_selected_signal(tree)
            else:
                self.signal_detail_frames[tree_index].pack_forget()
                direction_key = "long" if tree_index == 0 else "short"
                empty_text = signal_empty_state(direction_key, health_state)
                self.signal_empty_labels[tree_index].configure(text=empty_text)
                self.signal_empty_labels[tree_index].place(relx=.5, rely=.48, anchor="center")
                self.candle_canvases[tree_index].delete("all")
                self.candle_detail_vars[tree_index].set(empty_text)
        gate_status = strategy_gate.get("status", "未啟用｜保留基準訊號")
        learning_status = optimization.get("status", "尚無學習狀態")
        self.status.set(f"{prefix}｜資料 {result.get('data_date')}｜策略：{gate_status}｜學習：{learning_status}｜非投資建議｜不會自動下單")
