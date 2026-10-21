"""T 日收盤後可取得的技術研究指標；不得直接影響評分或交易資格。"""


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


def _missing(as_of_date, reason="requires_6_complete_sessions"):
    return {
        "version": "ta-v1",
        "as_of_date": as_of_date,
        "lookback_sessions": 6,
        "status": "insufficient_history",
        "score_effect": "none",
        "indicators": {key: None for key in INDICATOR_KEYS},
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
    except (KeyError, TypeError, ValueError):
        return _missing(as_of_date, "invalid_ohlc")
    if opens[-1] <= 0 or closes[-1] <= 0 or closes[-2] <= 0 or any(high < low for high, low in zip(highs, lows)):
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
    return {
        "version": "ta-v1",
        "as_of_date": as_of_date,
        "lookback_sessions": 6,
        "status": "available",
        "score_effect": "none",
        "indicators": {key: round(value, 6) for key, value in values.items()},
        "missing_reason": None,
    }


def assess_candlestick_conflict(technical_research, direction):
    """標示日K與方向的衝突；只供研究顯示，不改分或排除。"""
    base = {"version": "candle-v1", "score_effect": "none", "filter_effect": "none"}
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
        return {**base, "status": "direction_conflict",
                "message": f"{side}訊號與{label}方向衝突；研究警示，不直接改分或排除"}
    return {**base, "status": "aligned_or_neutral", "message": "日K未出現預先定義的方向衝突"}