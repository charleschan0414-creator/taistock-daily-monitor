"""T 日收盤後可取得的技術研究指標；不得直接影響評分或交易資格。"""

import math


INDICATOR_KEYS = (
    "rsi_5",
    "atr_5_pct",
    "close_vs_prior5_high_pct",
    "close_vs_prior5_low_pct",
    "candle_body_pct",
    "close_change_pct",
    "gap_pct",
    "close_location_pct",
    "upper_shadow_pct_of_range",
)


def _valid_ohlc(open_price, high, low, close):
    values = (open_price, high, low, close)
    return (all(math.isfinite(value) and value > 0 for value in values)
            and high >= max(open_price, close)
            and low <= min(open_price, close))


def _missing(as_of_date, reason="requires_6_complete_sessions"):
    return {
        "version": "ta-v1",
        "as_of_date": as_of_date,
        "lookback_sessions": 6,
        "status": "insufficient_history",
        "score_effect": "none",
        "indicators": {key: None for key in INDICATOR_KEYS},
        "recent_candles": [],
        "missing_reason": reason,
    }


def calculate_technical_research(bars, as_of_date):
    """用截至 as_of_date 的最後 6 根完整日 K 計算研究欄位。"""
    eligible = [row for row in bars if str(row.get("date", "")) <= str(as_of_date)]
    eligible.sort(key=lambda row: str(row.get("date", "")))
    if len(eligible) < 6:
        return _missing(as_of_date)
    bars6 = eligible[-6:]
    try:
        opens = [float(row["open"]) for row in bars6]
        closes = [float(row["close"]) for row in bars6]
        highs = [float(row["high"]) for row in bars6]
        lows = [float(row["low"]) for row in bars6]
    except (KeyError, TypeError, ValueError, OverflowError):
        return _missing(as_of_date, "invalid_ohlc")
    if not all(_valid_ohlc(open_price, high, low, close)
               for open_price, high, low, close in zip(opens, highs, lows, closes)):
        return _missing(as_of_date, "invalid_ohlc")

    changes = [closes[index] - closes[index - 1] for index in range(1, 6)]
    avg_gain = sum(max(change, 0.0) for change in changes) / 5
    avg_loss = sum(max(-change, 0.0) for change in changes) / 5
    if avg_gain == 0 and avg_loss == 0:
        rsi = 50.0
    elif avg_loss == 0:
        rsi = 100.0
    else:
        rsi = 100 - 100 / (1 + avg_gain / avg_loss)

    true_ranges = []
    for index in range(1, 6):
        true_ranges.append(max(
            highs[index] - lows[index],
            abs(highs[index] - closes[index - 1]),
            abs(lows[index] - closes[index - 1]),
        ))
    prior_high = max(highs[:5])
    prior_low = min(lows[:5])
    if prior_high <= 0 or prior_low <= 0:
        return _missing(as_of_date, "invalid_ohlc")
    values = {
        "rsi_5": rsi,
        "atr_5_pct": sum(true_ranges) / 5 / closes[-1] * 100,
        "close_vs_prior5_high_pct": (closes[-1] / prior_high - 1) * 100,
        "close_vs_prior5_low_pct": (closes[-1] / prior_low - 1) * 100,
        "candle_body_pct": (closes[-1] / opens[-1] - 1) * 100,
        "close_change_pct": (closes[-1] / closes[-2] - 1) * 100,
        "gap_pct": (opens[-1] / closes[-2] - 1) * 100,
        "close_location_pct": ((closes[-1] - lows[-1]) / (highs[-1] - lows[-1]) * 100
                               if highs[-1] > lows[-1] else 50.0),
        "upper_shadow_pct_of_range": ((highs[-1] - max(opens[-1], closes[-1])) /
                                      (highs[-1] - lows[-1]) * 100
                                      if highs[-1] > lows[-1] else 0.0),
    }
    if not all(math.isfinite(value) for value in values.values()):
        return _missing(as_of_date, "invalid_ohlc")
    recent_candles = [
        {"date": str(bars6[index]["date"]),
         "open": round(opens[index], 6),
         "high": round(highs[index], 6),
         "low": round(lows[index], 6),
         "close": round(closes[index], 6)}
        for index in range(1, 6)
    ]
    if not all(_valid_ohlc(row["open"], row["high"], row["low"], row["close"])
               for row in recent_candles):
        return _missing(as_of_date, "invalid_ohlc")
    return {
        "version": "ta-v1",
        "as_of_date": as_of_date,
        "lookback_sessions": 6,
        "status": "available",
        "score_effect": "none",
        "indicators": {key: round(value, 6) for key, value in values.items()},
        "recent_candles": recent_candles,
        "missing_reason": None,
    }


def analyze_candlestick_window(technical_research, direction):
    """以最近 5 根日K作圖、最後 3 根辨識型態；只供研究顯示。"""
    base = {
        "version": "candle-window-v1",
        "chart_sessions": 5,
        "pattern_sessions": 3,
        "score_effect": "none",
        "filter_effect": "none",
    }
    candles = technical_research.get("recent_candles") or []
    if technical_research.get("status") != "available" or len(candles) != 5:
        return {**base, "status": "insufficient_history", "pattern_name": "資料不足",
                "bias": "neutral", "direction_relation": "unknown",
                "explanation": "需要最近5個完整交易日；不以缺漏資料推定型態。"}
    try:
        valid = all(_valid_ohlc(float(row["open"]), float(row["high"]),
                                float(row["low"]), float(row["close"]))
                    for row in candles)
    except (KeyError, TypeError, ValueError, OverflowError):
        valid = False
    if not valid:
        return {**base, "status": "invalid_ohlc", "pattern_name": "K線資料無效",
                "bias": "neutral", "direction_relation": "unknown",
                "explanation": "最近5日K線含無效價格；不推定型態、不計分。"}
    last3 = candles[-3:]
    first, second, third = last3

    def price(row, key):
        return float(row[key])

    def is_bull(row):
        return price(row, "close") > price(row, "open")

    def is_bear(row):
        return price(row, "close") < price(row, "open")

    def body(row):
        return abs(price(row, "close") - price(row, "open"))

    def body_bounds(row):
        return sorted((price(row, "open"), price(row, "close")))

    all_bull = all(is_bull(row) for row in last3)
    all_bear = all(is_bear(row) for row in last3)
    rising = price(first, "close") < price(second, "close") < price(third, "close")
    falling = price(first, "close") > price(second, "close") > price(third, "close")
    opens_in_prior_body = all(
        body_bounds(last3[index - 1])[0] <= price(last3[index], "open") <= body_bounds(last3[index - 1])[1]
        for index in range(1, 3)
    )
    first_low, first_high = body_bounds(first)
    second_low, second_high = body_bounds(second)
    second_inside_first = first_low <= second_low and second_high <= first_high
    second_engulfs_first = second_low <= first_low and second_high >= first_high
    small_middle = body(second) <= body(first) * 0.35
    midpoint = (price(first, "open") + price(first, "close")) / 2

    pattern = None
    if all_bull and rising and opens_in_prior_body:
        pattern = ("三白兵", "bullish", "最近3日為連續三根紅K且收盤逐日墊高，屬偏多型態")
    elif all_bear and falling and opens_in_prior_body:
        pattern = ("三烏鴉", "bearish", "最近3日為連續三根黑K且收盤逐日下移，屬偏空型態")
    elif is_bear(first) and small_middle and is_bull(third) and price(third, "close") >= midpoint:
        pattern = ("晨星", "bullish", "前段黑K、中段小實體、第三日紅K收復首日實體一半以上，屬偏多反轉觀察")
    elif is_bull(first) and small_middle and is_bear(third) and price(third, "close") <= midpoint:
        pattern = ("夜星", "bearish", "前段紅K、中段小實體、第三日黑K跌回首日實體一半以下，屬偏空反轉觀察")
    elif is_bear(first) and is_bull(second) and second_inside_first and is_bull(third) and price(third, "close") > price(first, "open"):
        pattern = ("內困三日翻紅", "bullish", "第二日實體位於首日黑K內，第三日紅K向上突破，屬偏多確認觀察")
    elif is_bull(first) and is_bear(second) and second_inside_first and is_bear(third) and price(third, "close") < price(first, "open"):
        pattern = ("內困三日翻黑", "bearish", "第二日實體位於首日紅K內，第三日黑K向下突破，屬偏空確認觀察")
    elif is_bear(first) and is_bull(second) and second_engulfs_first and is_bull(third) and price(third, "close") > price(second, "close"):
        pattern = ("外側三日上升", "bullish", "第二日紅K吞噬首日黑K，第三日續漲確認，屬偏多型態")
    elif is_bull(first) and is_bear(second) and second_engulfs_first and is_bear(third) and price(third, "close") < price(second, "close"):
        pattern = ("外側三日下跌", "bearish", "第二日黑K吞噬首日紅K，第三日續跌確認，屬偏空型態")

    if pattern:
        name, bias, explanation = pattern
        aligned = (bias == "bullish" and direction == "long") or (bias == "bearish" and direction == "short")
        return {**base, "status": "pattern_found", "pattern_name": name,
                "bias": bias, "direction_relation": "aligned" if aligned else "conflict",
                "explanation": f"{explanation}；仍須配合位置、量價與隔日資格，不直接改分或排除。"}
    change = (float(candles[-1]["close"]) / float(candles[0]["close"]) - 1) * 100
    trend = "走高" if change > 0 else "走低" if change < 0 else "持平"
    return {**base, "status": "no_clear_pattern", "pattern_name": "未見明確3日型態",
            "bias": "neutral", "direction_relation": "neutral",
            "explanation": f"最近3日未形成保守定義的型態；5日收盤{trend} {change:+.2f}%。只供研究，不計分。"}


def assess_candlestick_conflict(technical_research, direction, volume_growth_pct=None):
    """標示日K與方向的衝突；只供研究顯示，不改分或排除。"""
    base = {"version": "candle-v1", "score_effect": "none", "filter_effect": "none",
            "risk_level": "normal", "shadow_candidate": None}
    if technical_research.get("status") != "available":
        return {**base, "status": "unknown", "message": "K線資料不足，無法判定方向衝突"}
    indicators = technical_research.get("indicators", {})
    body = indicators.get("candle_body_pct")
    change = indicators.get("close_change_pct")
    if not isinstance(body, (int, float)) or not isinstance(change, (int, float)):
        return {**base, "status": "unknown", "message": "K線欄位不完整，無法判定方向衝突"}
    conflict = ((direction == "short" and body > 0 and change > 0) or
                (direction == "long" and body < 0 and change < 0))
    if conflict:
        label = "紅K且收漲" if direction == "short" else "黑K且收跌"
        side = "空方" if direction == "short" else "多方"
        valid_volume = (type(volume_growth_pct) is int or
                        (type(volume_growth_pct) is float and math.isfinite(volume_growth_pct)))
        high_volume_long = direction == "long" and valid_volume and volume_growth_pct >= 100
        if high_volume_long:
            return {**base, "status": "direction_conflict", "risk_level": "high",
                    "shadow_candidate": "long_black_volume_veto_v1",
                    "message": "多方訊號遇爆量收黑K，列為高風險影子候選；正式分數不變，"
                               "不得追價，僅於突破觸發價後觀察"}
        return {**base, "status": "direction_conflict",
                "message": f"{side}訊號與{label}方向衝突；研究警示，不直接改分或排除"}
    return {**base, "status": "aligned_or_neutral", "message": "日K未出現預先定義的方向衝突"}