from decimal import Decimal, ROUND_HALF_UP


BASELINE_WEIGHTS = {
    "volume_growth_pct": 0.35,
    "ma5_distance_pct": 0.30,
    "institutional_lots": 0.25,
    "volume_rank": 0.10,
}


def is_common_stock(code: object, name: str = "") -> bool:
    text = str(code)
    return len(text) == 4 and text.isdigit() and not text.startswith("00")


def round_tw_price(value: float) -> float:
    """依輸入價格所在級距，以 Decimal ROUND_HALF_UP 對齊台股普通股跳動單位。"""
    price = Decimal(str(value))
    if price < Decimal("10"):
        tick = Decimal("0.01")
    elif price < Decimal("50"):
        tick = Decimal("0.05")
    elif price < Decimal("100"):
        tick = Decimal("0.1")
    elif price < Decimal("500"):
        tick = Decimal("0.5")
    elif price < Decimal("1000"):
        tick = Decimal("1")
    else:
        tick = Decimal("5")
    return float((price / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick)


def _clamp(value, low, high):
    return max(low, min(high, value))


def _trade_levels(item: dict, direction: str) -> dict:
    closes = item["closes"][-5:]
    returns = [abs(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes)) if closes[i - 1] > 0]
    volatility_pct = (sum(returns) / len(returns) * 100) if returns else 0.0
    risk_pct = round(_clamp(volatility_pct, 1.5, 5.0), 2)
    entry_offset_pct = round(_clamp(volatility_pct / 3, 0.3, 1.0), 2)
    close = item["close"]
    if direction == "long":
        entry = round_tw_price(close * (1 + entry_offset_pct / 100))
        stop = round_tw_price(entry * (1 - risk_pct / 100))
        unit_risk = entry - stop
        target1 = round_tw_price(entry + 1.5 * unit_risk)
        target2 = round_tw_price(entry + 2 * unit_risk)
        action = "偏多規則式研究觀察：突破觸發價才觀察；非投資建議或獲利保證"
    else:
        entry = round_tw_price(close * (1 - entry_offset_pct / 100))
        stop = round_tw_price(entry * (1 + risk_pct / 100))
        unit_risk = stop - entry
        target1 = round_tw_price(entry - 1.5 * unit_risk)
        target2 = round_tw_price(entry - 2 * unit_risk)
        action = "偏空規則式研究觀察：跌破觸發價才觀察；非投資建議或獲利保證"
    return {
        "signal_direction": direction,
        "action_text": action,
        "entry_trigger": entry,
        "stop_loss": stop,
        "target1": target1,
        "target2": target2,
        "risk_pct": risk_pct,
        "entry_offset_pct": entry_offset_pct,
        "volatility_proxy_pct": round(volatility_pct, 2),
    }


def evaluate_candidates(rows: list[dict], weights: dict | None = None) -> list[dict]:
    """對稱篩選多空訊號；各方向獨立百分位排名，回傳偏多後接偏空。"""
    weights = dict(BASELINE_WEIGHTS if weights is None else weights)
    by_direction = {"long": [], "short": []}
    for source in rows:
        closes = source.get("closes", [])
        previous = source.get("prev_volume", 0)
        if len(closes) < 5 or previous <= 0:
            continue
        ma5 = sum(closes[-5:]) / 5
        growth = (source["volume"] / previous - 1) * 100
        institutional = source["institutional_net"]
        if growth < 20:
            continue
        if source["close"] > ma5 and institutional > 0:
            direction = "long"
        elif source["close"] < ma5 and institutional < 0:
            direction = "short"
        else:
            continue
        distance = abs((source["close"] / ma5 - 1) * 100)
        item = dict(source)
        item.update(
            ma5=round(ma5, 2),
            volume_growth_pct=round(growth, 2),
            above_ma5_pct=round(distance if direction == "long" else -distance, 2),
            ma5_distance_pct=round(distance, 2),
            institutional_lots=round(institutional / 1000, 2),
            institutional_abs_lots=round(abs(institutional) / 1000, 2),
        )
        item.update(_trade_levels(item, direction))
        by_direction[direction].append(item)

    for direction, qualified in by_direction.items():
        n = len(qualified)
        if not n:
            continue

        def percentile(value, key):
            return sum(1 for row in qualified if row[key] <= value) / n * 100

        for item in qualified:
            volume_component = max(0.0, 101 - item["volume_rank"])
            score = (
                weights["volume_growth_pct"] * percentile(item["volume_growth_pct"], "volume_growth_pct")
                + weights["ma5_distance_pct"] * percentile(item["ma5_distance_pct"], "ma5_distance_pct")
                + weights["institutional_lots"] * percentile(item["institutional_abs_lots"], "institutional_abs_lots")
                + weights["volume_rank"] * volume_component
            )
            item["score"] = round(score, 2)
            side = "高於" if direction == "long" else "低於"
            flow = "買超" if direction == "long" else "賣超"
            item["reason"] = (
                f"量增{item['volume_growth_pct']:.1f}%；收盤{side}MA5 "
                f"{item['ma5_distance_pct']:.1f}%；法人{flow}{item['institutional_abs_lots']:.1f}張；"
                f"5日波動代理{item['volatility_proxy_pct']:.2f}%"
            )
        qualified.sort(key=lambda row: (-row["score"], row["volume_rank"], row["code"]))
    return by_direction["long"] + by_direction["short"]


def audit_candidates(rows: list[dict], weights: dict | None = None) -> list[dict]:
    """保留完整候選並記錄當日可知衍生值、資格與未入選原因。"""
    evaluated = evaluate_candidates(rows, weights)
    eligible_by_key = {
        (item.get("date"), item.get("code"), item.get("volume_rank")): item
        for item in evaluated
    }
    audited = []
    for source in rows:
        key = (source.get("date"), source.get("code"), source.get("volume_rank"))
        eligible = eligible_by_key.get(key)
        if eligible is not None:
            item = dict(eligible)
            item.update(eligible=True, rejection_reasons=[])
            audited.append(item)
            continue

        item = dict(source)
        closes = list(source.get("closes") or [])
        previous = source.get("prev_volume")
        institutional = source.get("institutional_net", 0)
        reasons = []
        if len(closes) < 5:
            reasons.append("insufficient_history")
        if previous is not None and previous <= 0:
            reasons.append("zero_previous_volume")
        ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else None
        growth = ((source["volume"] / previous - 1) * 100
                  if previous is not None and previous > 0 else None)
        if growth is not None and growth < 20:
            reasons.append("volume_growth_below_20")
        direction = None
        if ma5 is not None:
            if source["close"] > ma5 and institutional > 0:
                direction = "long"
            elif source["close"] < ma5 and institutional < 0:
                direction = "short"
            else:
                reasons.append("ma5_institutional_misaligned")
        distance = abs((source["close"] / ma5 - 1) * 100) if ma5 else None
        returns = [abs(closes[i] / closes[i - 1] - 1)
                   for i in range(max(1, len(closes) - 4), len(closes))
                   if closes[i - 1] > 0]
        volatility = (sum(returns) / len(returns) * 100) if returns else None
        item.update(
            ma5=round(ma5, 2) if ma5 is not None else None,
            volume_growth_pct=round(growth, 2) if growth is not None else None,
            above_ma5_pct=(round((source["close"] / ma5 - 1) * 100, 2) if ma5 else None),
            ma5_distance_pct=round(distance, 2) if distance is not None else None,
            institutional_lots=round(institutional / 1000, 2),
            institutional_abs_lots=round(abs(institutional) / 1000, 2),
            volatility_proxy_pct=round(volatility, 2) if volatility is not None else None,
            eligible=False,
            signal_direction=None,
            rejection_reasons=reasons,
            score=None,
            entry_trigger=None,
            stop_loss=None,
            target1=None,
            target2=None,
            risk_pct=None,
            entry_offset_pct=None,
        )
        audited.append(item)
    return audited
