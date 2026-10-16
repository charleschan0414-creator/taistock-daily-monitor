"""TAIFEX official global-linked and Taiwan night-session futures."""
from datetime import datetime

from .net import HttpClient

TAIFEX_QUOTES_URL = "https://www.taifex.com.tw/cht/quotesApi/getQuotes?objId=12"
SYMBOLS = ("TX", "ZEF", "CDF", "UNF", "SXF")
SCENARIO_SYMBOLS = ("TX", "UNF", "SXF")


def market_scenario(items):
    changes = [row.get("change_pct") for row in items if row.get("symbol") in SCENARIO_SYMBOLS]
    positive = sum(value is not None and value > 0 for value in changes)
    negative = sum(value is not None and value < 0 for value in changes)
    bias = "偏多" if positive > negative else "偏空" if negative > positive else "中性"
    return {
        "bias": bias,
        "positive": positive,
        "negative": negative,
        "basis": f"TX / UNF / SXF 上漲 {positive}、下跌 {negative}；僅供情境參考，不加入策略分數",
    }


def _number(value):
    return float(str(value).replace(",", ""))


class GlobalMarketClient:
    def __init__(self, http=None, clock=None):
        self.http = http or HttpClient()
        self.clock = clock or (lambda: datetime.now().astimezone())

    def fetch(self):
        fetched_at = self.clock().isoformat(timespec="seconds")
        try:
            payload = self.http.json(TAIFEX_QUOTES_URL)
            by_symbol = {}
            for raw in payload:
                contract = str(raw.get("contract", ""))
                symbol = next((item for item in SYMBOLS if contract.startswith(item)), None)
                if symbol and symbol not in by_symbol:
                    price = _number(raw["price"])
                    updown = _number(raw["updown"])
                    previous = price - updown
                    by_symbol[symbol] = {
                        "symbol": symbol,
                        "contract": contract,
                        "contract_name": raw.get("contractName", symbol),
                        "price": price,
                        "volume": int(_number(raw["ttlvol"])),
                        "updown": updown,
                        "change_pct": None if previous == 0 else updown / previous * 100,
                    }
            missing = [symbol for symbol in SYMBOLS if symbol not in by_symbol]
            if missing:
                raise ValueError("官方回傳缺少契約：" + ", ".join(missing))
            items = [by_symbol[symbol] for symbol in SYMBOLS]
            return {
                "status": "ok",
                "message": "TAIFEX 官方行情",
                "fetched_at": fetched_at,
                "items": items,
                "scenario": market_scenario(items),
            }
        except Exception as exc:
            return {
                "status": "error",
                "message": f"TAIFEX 行情更新失敗：{exc}",
                "fetched_at": fetched_at,
                "items": [],
            }
