"""Official live day-trading execution eligibility.

This is an execution constraint, not a strategy score. A T+1 intraday
signal cannot be presented as executable when the official market list
does not permit the required same-day round trip.
"""
from datetime import datetime
from urllib.parse import urlencode

TWSE_DAY_TRADE = "https://www.twse.com.tw/rwd/zh/dayTrading/TWTB4U"
TPEX_DAY_TRADE = "https://www.tpex.org.tw/openapi/v1/tpex_securities"


def _iso_date(value):
    text = str(value or "").strip().replace("/", "").replace("-", "")
    if len(text) == 7 and text.isdigit():
        text = str(int(text[:3]) + 1911) + text[3:]
    if len(text) != 8 or not text.isdigit():
        raise ValueError("當沖標的來源缺少有效資料日期")
    try:
        return datetime.strptime(text, "%Y%m%d").date().isoformat()
    except ValueError as error:
        raise ValueError("當沖標的來源缺少有效資料日期") from error


def parse_twse_eligibility(payload):
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        raise ValueError("TWSE當沖標的來源狀態異常")
    source_date = _iso_date(payload.get("date"))
    tables = payload.get("tables")
    if not isinstance(tables, list):
        raise ValueError("TWSE當沖標的缺少資料表")
    code_field = "證券代號"
    suspension_field = "暫停現股賣出後現款買進當沖註記"
    mapping = {}
    matched = False
    for table in tables:
        if not isinstance(table, dict) or not isinstance(table.get("fields"), list):
            continue
        fields = table["fields"]
        normalized_fields = ["".join(str(field).split()) for field in fields]
        if normalized_fields.count(code_field) == 0:
            continue
        if normalized_fields.count(code_field) != 1 or normalized_fields.count(suspension_field) != 1:
            raise ValueError("TWSE當沖標的必要欄位重複或缺漏")
        matched = True
        code_index = normalized_fields.index(code_field)
        suspension_index = normalized_fields.index(suspension_field)
        if not isinstance(table.get("data"), list):
            raise ValueError("TWSE當沖標的資料列格式異常")
        for row in table["data"]:
            if not isinstance(row, list) or max(code_index, suspension_index) >= len(row):
                raise ValueError("TWSE當沖標的資料列格式異常")
            code = str(row[code_index]).strip()
            marker = str(row[suspension_index]).strip().upper()
            if marker not in {"", "Y"}:
                raise ValueError(f"TWSE當沖標的暫停註記異常：{marker}")
            if code:
                mapping[code] = marker == "Y"
    if not matched:
        raise ValueError("TWSE當沖標的缺少必要欄位")
    return source_date, mapping


def parse_tpex_eligibility(payload):
    if not isinstance(payload, list) or not payload:
        raise ValueError("TPEx當沖標的來源為空")
    required = {"資料日期", "證券代號", "暫停現股賣出後現款買進當沖註記"}
    if any(not isinstance(row, dict) or not required.issubset(row) for row in payload):
        raise ValueError("TPEx當沖標的缺少必要欄位")
    dates = {_iso_date(row["資料日期"]) for row in payload}
    if len(dates) != 1:
        raise ValueError(f"TPEx當沖標的含多個資料日期：{sorted(dates)}")
    mapping = {}
    for row in payload:
        code = str(row["證券代號"]).strip()
        marker = str(row["暫停現股賣出後現款買進當沖註記"]).strip().upper()
        if marker not in {"", "＊"}:
            raise ValueError(f"TPEx當沖標的暫停註記異常：{marker}")
        if code:
            mapping[code] = marker == "＊"
    return dates.pop(), mapping


def apply_execution_eligibility(result, twse_payload, tpex_payload, expected_source_date):
    expected_source_date = _iso_date(expected_source_date)
    twse_date, twse = parse_twse_eligibility(twse_payload)
    tpex_date, tpex = parse_tpex_eligibility(tpex_payload)
    if twse_date != expected_source_date or tpex_date != expected_source_date:
        raise ValueError(
            f"官方當沖標的日期不一致：預期{expected_source_date}、TWSE {twse_date}、TPEx {tpex_date}"
        )
    signal_date = _iso_date(result.get("data_date"))
    if expected_source_date < signal_date:
        raise ValueError("官方當沖標的早於訊號資料日")

    signals = result.get("signals")
    if not isinstance(signals, list):
        raise ValueError("每日掃描 signals 必須是list")
    kept, removed = [], []
    for source in signals:
        if not isinstance(source, dict):
            raise ValueError("每日掃描 signals 列格式異常")
        row = dict(source)
        code = str(row.get("code", ""))
        market = row.get("market")
        direction = row.get("signal_direction")
        if market not in {"上市", "上櫃"}:
            reason = "unknown_market"
            market_map = None
        elif direction not in {"long", "short"}:
            reason = "unknown_direction"
            market_map = None
        else:
            reason = None
            market_map = twse if market == "上市" else tpex
        suspended = None if market_map is None else market_map.get(code)
        if reason is None:
            if suspended is None:
                reason = "not_day_trade_eligible"
            elif direction == "short" and suspended:
                reason = "sell_first_suspended"
        eligibility = {
            "source_date": expected_source_date,
            "eligible": reason is None,
            "reason": reason or "eligible",
            "source": "TWSE" if market == "上市" else "TPEx" if market == "上櫃" else "unknown",
        }
        row["day_trading_eligibility"] = eligibility
        if reason:
            removed.append({"code": code, "name": row.get("name", ""),
                            "direction": row.get("signal_direction"), "reason": reason,
                            "market": row.get("market"), "source_date": expected_source_date})
        else:
            kept.append(row)

    long_rows = [row for row in kept if row.get("signal_direction") == "long"]
    short_rows = [row for row in kept if row.get("signal_direction") == "short"]
    status = "next_session_verified" if expected_source_date > signal_date else "signal_date_only_recheck_required"
    updated = dict(result)
    updated.update({
        "signals": kept,
        "qualified": long_rows,
        "top10": long_rows[:10],
        "long_top10": long_rows[:10],
        "short_top10": short_rows[:10],
        "qualified_count": len(long_rows),
        "long_count": len(long_rows),
        "short_count": len(short_rows),
        "execution_eligibility": {
            "version": "official-day-trade-v1",
            "source_date": expected_source_date,
            "execution_for_signal_date": status,
            "enforced": True,
            "score_effect": "none",
            "eligible_count": len(kept),
            "filtered_count": len(removed),
            "filtered_out": removed,
            "sources": [TWSE_DAY_TRADE, TPEX_DAY_TRADE],
            "notice": "官方當沖資格屬執行限制，不改變策略分數；空方另排除暫停先賣後買標的。",
        },
    })
    updated["sources"] = list(dict.fromkeys(list(result.get("sources", [])) + [TWSE_DAY_TRADE, TPEX_DAY_TRADE]))
    return updated


def fetch_and_apply_execution_eligibility(client, result, source_date):
    compact = source_date.replace("-", "")
    twse_url = TWSE_DAY_TRADE + "?" + urlencode({"date": compact, "response": "json"})
    twse_payload = client.json(twse_url)
    tpex_payload = client.json(TPEX_DAY_TRADE)
    twse_date, twse = parse_twse_eligibility(twse_payload)
    tpex_date, tpex = parse_tpex_eligibility(tpex_payload)
    if len(twse) < 100 or len(tpex) < 100:
        raise ValueError(f"官方當沖標的數量異常：TWSE {len(twse)}、TPEx {len(tpex)}")
    if twse_date != source_date or tpex_date != source_date:
        raise ValueError(f"官方當沖標的尚未更新至 {source_date}")
    return apply_execution_eligibility(result, twse_payload, tpex_payload, source_date)
