"""Official Taiwan market-session checks for planned and disaster closures."""
from __future__ import annotations

import html
import re
from datetime import date
from urllib.parse import urlencode

from .data import parse_tpex_quotes, parse_twse_quotes

TWSE_QUOTES = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_QUOTES = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"

TWSE_HOLIDAY = "https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule"
DGPA_CLOSURE = "https://www.dgpa.gov.tw/typh/daily/nds.html"


def latest_official_quote_date(client):
    """Return the latest common TWSE/TPEx quote date, or fail closed."""
    twse_dates = {row.date for row in parse_twse_quotes(client.json(TWSE_QUOTES))}
    tpex_dates = {row.date for row in parse_tpex_quotes(client.json(TPEX_QUOTES))}
    if len(twse_dates) != 1 or len(tpex_dates) != 1 or twse_dates != tpex_dates:
        raise ValueError(
            f"上市與上櫃最新官方行情日期不一致：TWSE={sorted(twse_dates)} TPEx={sorted(tpex_dates)}")
    return twse_dates.pop()


def _date(value):
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("市場開休市日期無效") from exc


def parse_twse_holiday(payload, target_date):
    """Return True only for a matching TWSE row that explicitly has no trading."""
    target = _date(target_date)
    if not isinstance(payload, dict) or str(payload.get("stat", "")).lower() != "ok":
        raise ValueError("TWSE市場開休市來源狀態異常")
    fields = payload.get("fields")
    rows = payload.get("data")
    if fields != ["日期", "名稱", "說明"] or not isinstance(rows, list):
        raise ValueError("TWSE市場開休市來源欄位異常")
    expected_years = {str(target.year - 1911), str(target.year)}
    if str(payload.get("queryYear")) not in expected_years:
        raise ValueError("TWSE市場開休市來源年份不一致")
    matched = []
    for row in rows:
        if not isinstance(row, list) or len(row) != 3:
            raise ValueError("TWSE市場開休市來源資料列異常")
        if row[0] == target.isoformat():
            matched.append(row)
    if len(matched) > 1:
        raise ValueError("TWSE市場開休市來源日期重複")
    if not matched:
        return False
    text = " ".join(str(value) for value in matched[0][1:])
    if "市場無交易" in text or "放假" in text or "補假" in text:
        return True
    if "開始交易" in text or "最後交易" in text:
        return False
    raise ValueError("TWSE市場開休市來源無法判定是否交易")


def _plain_text(fragment):
    without_tags = re.sub(r"<[^>]+>", " ", fragment)
    return "".join(html.unescape(without_tags).split())


def parse_dgpa_taipei_closure(page, target_date):
    """Return whether DGPA says Taipei City stops work for the full day."""
    target = _date(target_date)
    if not isinstance(page, str) or not page.strip():
        raise ValueError("DGPA天然災害來源為空")
    plain = _plain_text(page)
    roc = target.year - 1911
    if f"{roc}年{target.month}月{target.day}日天然災害停止上班及上課情形" not in plain:
        raise ValueError("DGPA天然災害來源日期不一致")
    rows = re.findall(r"<tr\b[^>]*>[\s\S]*?</tr>", page, flags=re.IGNORECASE)
    taipei = [_plain_text(row) for row in rows if "臺北市" in _plain_text(row)]
    if len(taipei) != 1:
        raise ValueError("DGPA天然災害來源缺少唯一臺北市資料")
    text = taipei[0]
    if "下午停止上班" in text and "上午停止上班" not in text:
        return False
    if (("今天停止上班" in text or "上午停止上班" in text)
            and "照常上班" not in text):
        return True
    if "照常上班" in text or "尚未宣布" in text or "未達停止上班" in text:
        return False
    raise ValueError("DGPA臺北市停止上班狀態無法判定")


def market_session_status(client, target_date):
    """Determine closure from weekends, TWSE schedule, then current DGPA notice."""
    target = _date(target_date)
    if target.weekday() >= 5:
        return {"status": "closed", "date": target.isoformat(),
                "reason": "weekend", "source": "calendar"}
    query = urlencode({"response": "json", "queryYear": str(target.year - 1911)})
    holiday_url = f"{TWSE_HOLIDAY}?{query}"
    holiday = client.json(holiday_url)
    if parse_twse_holiday(holiday, target.isoformat()):
        return {"status": "closed", "date": target.isoformat(),
                "reason": "twse_holiday", "source": TWSE_HOLIDAY}
    page = client.text(DGPA_CLOSURE)
    if parse_dgpa_taipei_closure(page, target.isoformat()):
        return {"status": "closed", "date": target.isoformat(),
                "reason": "taipei_disaster_stop_work", "source": DGPA_CLOSURE}
    return {"status": "open", "date": target.isoformat(),
            "reason": "official_sources_open", "sources": [TWSE_HOLIDAY, DGPA_CLOSURE]}
