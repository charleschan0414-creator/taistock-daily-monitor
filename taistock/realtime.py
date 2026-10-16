"""TWSE MIS 即時報價用戶端；只使用 Python 標準函式庫。"""
import json
import time
from datetime import datetime, time as clock_time, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
TAIPEI = timezone(timedelta(hours=8))


def build_channel(code, market):
    prefix = "otc" if str(market) in ("上櫃", "otc", "OTC") else "tse"
    return f"{prefix}_{code}.tw"


def _number(value):
    if value is None or str(value).strip() in ("", "-"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _first_book(value):
    for item in str(value or "").split("_"):
        parsed = _number(item)
        if parsed is not None:
            return parsed
    return None


def _quote_time(raw):
    tlong = raw.get("tlong")
    if tlong and str(tlong).isdigit():
        return datetime.fromtimestamp(int(tlong) / 1000, TAIPEI)
    day, moment = str(raw.get("d", "")), str(raw.get("t", ""))
    try:
        return datetime.strptime(day + " " + moment[:8], "%Y%m%d %H:%M:%S").replace(tzinfo=TAIPEI)
    except ValueError:
        return None


def is_trading_time(moment):
    local = moment.astimezone(TAIPEI)
    return local.weekday() < 5 and clock_time(9, 0) <= local.time().replace(tzinfo=None) <= clock_time(13, 30)


def parse_quote(raw, now=None):
    now = (now or datetime.now(TAIPEI)).astimezone(TAIPEI)
    quote_time = _quote_time(raw)
    last = _number(raw.get("z"))
    fallback = last is None
    if fallback:
        last = (_first_book(raw.get("a")) or _first_book(raw.get("b")) or
                _number(raw.get("o")) or _number(raw.get("h")) or
                _number(raw.get("l")) or _number(raw.get("y")))
    previous = _number(raw.get("y"))
    change = (last - previous) if last is not None and previous is not None else None
    change_pct = (change / previous * 100) if change is not None and previous else None
    live = (not fallback and quote_time is not None and quote_time.date() == now.date()
            and is_trading_time(now))
    status = "即時" if live else ("非即時（成交價缺值回退）" if fallback else "收盤/非即時")
    return {
        "code": str(raw.get("c") or raw.get("ch", "").split("_")[-1].split(".")[0]),
        "name": raw.get("n") or "",
        "price": last,
        "previous_close": previous,
        "open": _number(raw.get("o")), "high": _number(raw.get("h")), "low": _number(raw.get("l")),
        "volume": _number(raw.get("v")), "change": change, "change_pct": change_pct,
        "quote_time": quote_time.isoformat(timespec="seconds") if quote_time else None,
        "status": status, "stale": not live,
    }


class RealtimeQuoteClient:
    def __init__(self, timeout=8, retries=2, batch_size=50, opener=urlopen, sleep=time.sleep):
        self.timeout, self.retries, self.batch_size = timeout, retries, batch_size
        self.opener, self.sleep = opener, sleep

    def _request(self, rows):
        channels = "|".join(build_channel(row["code"], row.get("market", "上市")) for row in rows)
        url = MIS_URL + "?" + urlencode({"ex_ch": channels, "json": "1", "delay": "0"})
        request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        last_error = None
        for attempt in range(self.retries + 1):
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8-sig"))
                if not isinstance(payload.get("msgArray"), list):
                    raise ValueError("MIS 回應缺少 msgArray")
                return payload["msgArray"]
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < self.retries:
                    self.sleep(min(4, 0.5 * (2 ** attempt)))
        raise RuntimeError(f"MIS 即時報價失敗：{last_error}") from last_error

    def fetch(self, rows):
        unique = {}
        for row in rows:
            code = str(row.get("code", ""))
            if code and code not in unique:
                unique[code] = row
        values = list(unique.values())
        result = {}
        now = datetime.now(TAIPEI)
        for offset in range(0, len(values), self.batch_size):
            for raw in self._request(values[offset:offset + self.batch_size]):
                quote = parse_quote(raw, now)
                if quote["code"]:
                    result[quote["code"]] = quote
        return result
