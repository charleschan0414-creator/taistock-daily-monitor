"""Broker-branch short-term turnover research.

This is a warning-only overlay.  Branch labels are public transaction
aggregates and never identify one investor or prove intent.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from urllib.parse import quote
from urllib.request import Request, urlopen

SOURCE_URL = "https://fubon-ebrokerdj.fbs.com.tw/z/zc/zco/zco.djhtm?a={code}"
MAX_RESPONSE_BYTES = 1_000_000
WATCHED_SHORT_TERM_BRANCHES = frozenset({
    "凱基-台北", "凱基-城中", "凱基-市政", "凱基-松山", "凱基-信義",
    "國泰-敦南", "永豐金-市政", "富邦-建國", "富邦-南屯", "元大-土城永寧",
})


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "tr":
            self._row = []
        elif tag == "td" and self._row is not None:
            self._cell = []

    def handle_data(self, data):
        self.text.append(data)
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "td" and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
            self._cell = None


def _number(text: str) -> float:
    return float(str(text).replace(",", "").replace("%", "").strip())


def _leader(cells, offset, side):
    branch = cells[offset].strip()
    if not branch or branch in ("買超券商", "賣超券商"):
        return None
    try:
        buy = int(_number(cells[offset + 1]))
        sell = int(_number(cells[offset + 2]))
        displayed = int(_number(cells[offset + 3]))
        share = _number(cells[offset + 4])
    except (ValueError, TypeError, IndexError):
        return None
    net = displayed if side == "buy" else -displayed
    return {"branch": branch, "buy_lots": buy, "sell_lots": sell,
            "net_lots": net, "share_pct": share, "side": side}


def parse_broker_report(html: str, code: str) -> dict:
    identity = re.search(r"<title>\s*主力賣買超-(\d{4,6})\s*</title>", html, re.I)
    if not identity or identity.group(1) != str(code):
        raise ValueError("broker report stock code mismatch")
    parser = _TableParser()
    parser.feed(html)
    plain = " ".join("".join(parser.text).split())
    match = re.search(r"最後更新日[：:]\s*(\d{4})/(\d{2})/(\d{2})", plain)
    if not match:
        raise ValueError("broker report date missing")
    buy_leaders, sell_leaders = [], []
    for cells in parser.rows:
        if len(cells) != 10:
            continue
        buy = _leader(cells, 0, "buy")
        sell = _leader(cells, 5, "sell")
        if buy:
            buy_leaders.append(buy)
        if sell:
            sell_leaders.append(sell)
    if not buy_leaders and not sell_leaders:
        raise ValueError("broker report rows missing")
    return {"code": identity.group(1), "data_date": "-".join(match.groups()),
            "buy_leaders": buy_leaders, "sell_leaders": sell_leaders}


def _annotate(row, total_lots):
    item = dict(row)
    turnover = item["buy_lots"] + item["sell_lots"]
    item["turnover_share_pct"] = round(turnover / total_lots * 100, 2) if total_lots > 0 else None
    return item


def analyze_broker_risk(report: dict, total_volume_shares: int,
                        watched=WATCHED_SHORT_TERM_BRANCHES) -> dict:
    total_lots = max(0.0, float(total_volume_shares or 0) / 1000)
    watched_buyers = [_annotate(row, total_lots) for row in report.get("buy_leaders", [])
                      if row.get("branch") in watched]
    watched_sellers = [_annotate(row, total_lots) for row in report.get("sell_leaders", [])
                       if row.get("branch") in watched]
    material_buyers = [row for row in watched_buyers
                       if row["share_pct"] >= 0.5 or (row["turnover_share_pct"] or 0) >= 3]
    material_sellers = [row for row in watched_sellers
                        if row["share_pct"] >= 0.5 or (row["turnover_share_pct"] or 0) >= 3]
    high = any(row["share_pct"] >= 1.5 or (row["turnover_share_pct"] or 0) >= 8
               for row in material_buyers + material_sellers)
    warning = bool(material_buyers or material_sellers)
    level = "high" if high else "watch" if warning else "none"
    branches = "、".join(dict.fromkeys(
        row["branch"] for row in material_buyers + material_sellers))
    summary = (f"{level.upper()}｜短線觀察分點達門檻：{branches}；隔日勿追價，先看開盤賣壓。"
               if warning else "未見預設短線觀察分點達到大量買賣或高週轉門檻。")
    return {
        "version": "broker-risk-v1",
        "status": "available",
        "data_date": report.get("data_date"),
        "risk_level": level,
        "warning": warning,
        "summary": summary,
        "watched_buyers": material_buyers,
        "watched_sellers": material_sellers,
        "score_effect": "none",
        "filter_effect": "none",
        "source": "MoneyDJ公開彙整頁（原始資訊來自市場買賣日報表）",
        "notice": "分點為多人彙總，短線型標籤是觀察名單、非官方認定；只能提示可能賣壓，不能保證避免倒貨。",
    }


def fetch_broker_html(code: str, timeout=25) -> str:
    url = SOURCE_URL.format(code=quote(str(code), safe=""))
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"})
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("broker report exceeds size limit")
    return raw.decode("big5", errors="replace")


def _unavailable(status, data_date=None):
    return {
        "version": "broker-risk-v1", "status": status, "data_date": data_date,
        "risk_level": "unknown", "warning": False, "watched_buyers": [],
        "watched_sellers": [], "score_effect": "none", "filter_effect": "none",
        "notice": "分點資料不可用或日期不一致，不據此調整排序或名單。",
    }


def enrich_broker_risk(result: dict, fetch_text=fetch_broker_html) -> dict:
    """Attach warnings to selected rows; failures never alter the scan result."""
    expected_date = result.get("data_date")
    rows = list(result.get("signals", []))

    def fetch_one(row):
        try:
            report = parse_broker_report(fetch_text(str(row.get("code", ""))), str(row.get("code", "")))
            if report["data_date"] != expected_date:
                research = _unavailable("stale", report["data_date"])
            else:
                research = analyze_broker_risk(report, row.get("volume", 0))
        except Exception:
            research = _unavailable("unavailable")
        return row, research

    if rows:
        with ThreadPoolExecutor(max_workers=min(4, len(rows))) as executor:
            futures = [executor.submit(fetch_one, row) for row in rows]
            for future in as_completed(futures):
                row, research = future.result()
                row["broker_risk_research"] = research
    result["broker_risk_research"] = {
        "version": "broker-risk-v1", "score_effect": "none", "filter_effect": "none",
        "watched_branches": sorted(WATCHED_SHORT_TERM_BRANCHES),
        "notice": "只對通過策略閘門的觀察名單查詢；警示不代表分點或投資人一定會倒貨。",
    }
    return result
