"""使用 TWSE/TPEx 官方逐日資料的無前視多空回測。"""
from __future__ import annotations

import csv
import gzip
import json
import os
import sqlite3
import statistics
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from .core import BASELINE_WEIGHTS, audit_candidates, evaluate_candidates, is_common_stock
from .technical import calculate_technical_research
from .data import _number
from .portfolio import simulate_portfolio
from .storage import CANDIDATE_WEIGHTS, MIN_SAMPLES

INDICATOR_WARMUP_DAYS = 5
NEXT_DAY_EXECUTION_DAYS = 1
COST_SCENARIOS = {"gross": 0.0, "standard": 0.00435, "stress": 0.00535}


def required_history_days(signal_days: int) -> int:
    if signal_days < 1:
        raise ValueError("signal_days must be positive")
    return INDICATOR_WARMUP_DAYS + signal_days + NEXT_DAY_EXECUTION_DAYS


def _table_with_security_code(payload: dict) -> dict:
    for table in payload.get("tables", []):
        fields = table.get("fields") or []
        if fields and fields[0] == "證券代號":
            return table
    return {}


def parse_twse_day(payload: dict) -> dict[str, dict]:
    result = {}
    for row in _table_with_security_code(payload).get("data", []):
        code = str(row[0]).strip()
        if not is_common_stock(code) or len(row) < 9:
            continue
        close = _number(row[8])
        if close <= 0:
            continue
        result[code] = {"code": code, "name": str(row[1]).strip(), "market": "上市",
                        "volume": int(_number(row[2])), "open": _number(row[5]),
                        "high": _number(row[6]), "low": _number(row[7]), "close": close}
    return result


def parse_tpex_day(payload: dict) -> dict[str, dict]:
    tables = payload.get("tables", [])
    rows = tables[0].get("data", []) if tables else []
    result = {}
    for row in rows:
        code = str(row[0]).strip()
        if not is_common_stock(code) or len(row) < 9:
            continue
        close = _number(row[2])
        if close <= 0:
            continue
        result[code] = {"code": code, "name": str(row[1]).strip(), "market": "上櫃",
                        "volume": int(_number(row[8])), "open": _number(row[4]),
                        "high": _number(row[5]), "low": _number(row[6]), "close": close}
    return result


def parse_twse_institutions(payload: dict) -> dict[str, int]:
    return {str(row[0]).strip(): int(_number(row[-1])) for row in payload.get("data", [])
            if row and is_common_stock(str(row[0]).strip())}


def parse_tpex_institutions(payload: dict) -> dict[str, int]:
    tables = payload.get("tables", [])
    rows = tables[0].get("data", []) if tables else []
    return {str(row[0]).strip(): int(_number(row[23])) for row in rows
            if len(row) > 23 and is_common_stock(str(row[0]).strip())}


class OfficialHistoryFetcher:
    """逐日抓四個官方來源；成功 JSON 以 gzip 快取，支援中斷續跑。"""
    def __init__(self, client, cache_dir: Path, throttle: float = 0.35, progress=None):
        self.client = client
        self.cache_dir = cache_dir
        self.throttle = throttle
        self.progress = progress or (lambda *_: None)
        self.requests = 0
        self.cache_hits = 0
        cache_dir.mkdir(parents=True, exist_ok=True)

    def _load(self, date_text: str, source: str, url: str) -> dict:
        path = self.cache_dir / f"{date_text}_{source}.json.gz"
        if path.exists():
            try:
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    payload = json.load(handle)
                self.cache_hits += 1
                return payload
            except (OSError, ValueError):
                path.unlink(missing_ok=True)
        payload = self.client.json(url)
        self.requests += 1
        temporary = path.with_suffix(path.suffix + ".tmp")
        with gzip.open(temporary, "wt", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        temporary.replace(path)
        if self.throttle:
            time.sleep(self.throttle)
        return payload

    @staticmethod
    def _payload_date(payload: dict) -> str:
        value = str(payload.get("date", ""))
        if not value:
            for table in payload.get("tables", []):
                if table.get("date"):
                    value = str(table["date"])
                    break
        compact = value.replace("/", "").replace("-", "")
        if len(compact) == 7 and compact.isdigit():
            compact = str(int(compact[:3]) + 1911) + compact[3:]
        return compact[:8]

    def fetch_day(self, day: datetime) -> dict | None:
        compact, slash = day.strftime("%Y%m%d"), day.strftime("%Y/%m/%d")
        urls = {
            "twse_quotes": "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?" + urlencode(
                {"date": compact, "type": "ALLBUT0999", "response": "json"}),
            "twse_institutions": "https://www.twse.com.tw/rwd/zh/fund/T86?" + urlencode(
                {"date": compact, "selectType": "ALLBUT0999", "response": "json"}),
            "tpex_quotes": "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?" + urlencode(
                {"date": slash, "type": "EW", "response": "json"}),
            "tpex_institutions": "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade?" + urlencode(
                {"type": "Daily", "sect": "EW", "date": slash, "id": "", "response": "json"}),
        }
        payloads = {name: self._load(compact, name, url) for name, url in urls.items()}
        mismatched = [name for name, payload in payloads.items() if self._payload_date(payload) != compact]
        if mismatched:
            for name in mismatched:
                (self.cache_dir / f"{compact}_{name}.json.gz").unlink(missing_ok=True)
            return None
        twse, tpex = parse_twse_day(payloads["twse_quotes"]), parse_tpex_day(payloads["tpex_quotes"])
        if not twse or not tpex:
            if not twse:
                (self.cache_dir / f"{compact}_twse_quotes.json.gz").unlink(missing_ok=True)
            if not tpex:
                (self.cache_dir / f"{compact}_tpex_quotes.json.gz").unlink(missing_ok=True)
            return None
        institutions = parse_twse_institutions(payloads["twse_institutions"])
        institutions.update(parse_tpex_institutions(payloads["tpex_institutions"]))
        return {"date": day.strftime("%Y-%m-%d"), "quotes": {**twse, **tpex},
                "institutions": institutions}

    def collect(self, end_date: str, total: int) -> list[dict]:
        day = datetime.strptime(end_date, "%Y-%m-%d")
        found, checked = [], 0
        while len(found) < total:
            if day.weekday() < 5:
                checked += 1
                result = self.fetch_day(day)
                if result is not None:
                    found.append(result)
                    self.progress(f"官方回測資料 {len(found)}/{total}（檢查 {checked} 日）", len(found) / total)
            day -= timedelta(days=1)
            if checked > total * 3:
                raise RuntimeError(f"檢查 {checked} 個平日仍不足 {total} 個四源完整交易日")
        return sorted(found, key=lambda row: row["date"])


def simulate_trade(signal: dict, future_days, require_full_horizon: bool = False) -> dict:
    """收盤訊號只交易次一交易日；停損優先，未出場則當日收盤強制沖銷。"""
    days = [future_days] if isinstance(future_days, dict) else list(future_days[:NEXT_DAY_EXECUTION_DAYS])
    if require_full_horizon and len(days) < NEXT_DAY_EXECUTION_DAYS:
        return {"status": "unresolved", "triggered": False, "entry_date": None,
                "entry": None, "exit": None, "exit_date": None,
                "exit_reason": "missing_next_trading_day", "return": None,
                "mfe": None, "mae": None}
    direction = signal["signal_direction"]
    entry_trigger, stop = float(signal["entry_trigger"]), float(signal["stop_loss"])
    target1, target2 = float(signal["target1"]), float(signal["target2"])
    if not days:
        return {"status": "unresolved", "triggered": False, "entry_date": None,
                "entry": None, "exit": None, "exit_date": None,
                "exit_reason": "missing_next_trading_day", "return": None,
                "mfe": None, "mae": None}
    day = days[0]
    touched = day["high"] >= entry_trigger if direction == "long" else day["low"] <= entry_trigger
    if not touched:
        return {"status": "untriggered", "triggered": False, "entry_date": None,
                "entry": None, "exit": None, "exit_date": day.get("date"),
                "exit_reason": "untriggered", "return": None, "mfe": None, "mae": None}
    opening = float(day["open"])
    entry = max(entry_trigger, opening) if direction == "long" else min(entry_trigger, opening)
    stop_hit = day["low"] <= stop if direction == "long" else day["high"] >= stop
    target_hit = day["high"] >= target1 if direction == "long" else day["low"] <= target1
    target2_hit = day["high"] >= target2 if direction == "long" else day["low"] <= target2
    if stop_hit:
        exit_price, reason = stop, "stop"
    elif target_hit:
        exit_price, reason = target1, "target1"
    elif target2_hit:
        exit_price, reason = target2, "target2"
    else:
        exit_price, reason = float(day["close"]), "forced_close"
    value = ((exit_price - entry) / entry if direction == "long" else (entry - exit_price) / entry)
    favorable = ((float(day["high"]) - entry) / entry if direction == "long"
                 else (entry - float(day["low"])) / entry)
    adverse = ((float(day["low"]) - entry) / entry if direction == "long"
               else (entry - float(day["high"])) / entry)
    return {"status": "settled", "triggered": True, "entry_date": day.get("date"), "entry": entry,
            "exit": exit_price, "exit_date": day.get("date"), "exit_reason": reason,
            "return": value, "mfe": favorable, "mae": adverse}


def _metric(rows: list[dict], weights: dict) -> float:
    groups = {}
    for row in rows:
        groups.setdefault((row["signal_date"], row["direction"]), []).append(row)
    daily = []
    for group in groups.values():
        n, scored = len(group), []
        for row in group:
            def pct(key):
                return sum(1 for other in group if other[key] <= row[key]) / n * 100
            score = (weights["volume_growth_pct"] * pct("volume_growth_pct") +
                     weights["ma5_distance_pct"] * pct("ma5_distance_pct") +
                     weights["institutional_lots"] * pct("institutional_abs_lots") +
                     weights["volume_rank"] * max(0, 101 - row["volume_rank"]))
            scored.append((score, row["volume_rank"], str(row.get("code", "")), row["return"]))
        top = sorted(scored, key=lambda item: (-item[0], item[1], item[2]))[:3]
        daily.append(sum(item[3] for item in top) / len(top))
    return sum(daily) / len(daily) if daily else 0.0


def walk_forward_weights(samples: list[dict], as_of_date: str) -> dict:
    eligible = [row for row in samples if row.get("triggered") and row.get("return") is not None
                and row.get("exit_date") and row["exit_date"] < as_of_date]
    baseline = dict(BASELINE_WEIGHTS)
    state = {"weights": baseline, "sample_count": len(eligible), "changed": False,
             "baseline_metric": None, "selected_metric": None}
    if len(eligible) < MIN_SAMPLES:
        return state
    metrics = [(dict(weights), _metric(eligible, weights)) for weights in CANDIDATE_WEIGHTS]
    base_metric = _metric(eligible, baseline)
    best_weights, best_metric = max(metrics, key=lambda item: item[1])
    changed = best_weights != baseline and best_metric > base_metric
    state.update({"weights": best_weights if changed else baseline, "changed": changed,
                  "baseline_metric": base_metric, "selected_metric": best_metric if changed else base_metric})
    return state


def _stats(trades: list[dict], signal_dates: list[str]) -> dict:
    resolved = [row for row in trades if row.get("status") != "unresolved"]
    executed = [row for row in resolved if row.get("triggered")]
    returns = [row.get("return", row.get("net_return")) for row in executed]
    unresolved = len(trades) - len(resolved)
    daily = []
    for date in signal_dates:
        values = [row.get("return", row.get("net_return")) for row in executed if row["signal_date"] == date]
        daily.append(sum(values) / len(values) if values else 0.0)
    equity = peak = 1.0
    max_drawdown = 0.0
    for value in daily:
        equity *= 1 + value
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1)
    gains = sum(value for value in returns if value > 0)
    losses = -sum(value for value in returns if value < 0)
    average = sum(returns) / len(returns) if returns else 0.0
    return {"signals": len(trades), "trades": len(executed), "triggered": len(executed),
            "untriggered": len(resolved) - len(executed), "unresolved": unresolved,
            "win_rate": sum(value > 0 for value in returns) / len(returns) if returns else 0.0,
            "average_return": average,
            "median_return": statistics.median(returns) if returns else 0.0,
            "cumulative_return": equity - 1, "max_drawdown": max_drawdown,
            "profit_factor": gains / losses if losses else None}


def summarize_trades(trades: list[dict], signal_dates: list[str]) -> dict:
    result = _stats(trades, signal_dates)
    result["signal_days"] = len(signal_dates)
    result["long"] = _stats([row for row in trades if row["direction"] == "long"], signal_dates)
    result["short"] = _stats([row for row in trades if row["direction"] == "short"], signal_dates)
    return result


def summarize_trade_scenarios(trades: list[dict], signal_dates: list[str]) -> dict:
    """彙總毛報酬及每筆已成交僅扣一次雙邊總成本的情境。"""
    scenarios = {}
    for name, cost in COST_SCENARIOS.items():
        adjusted = []
        for row in trades:
            item = dict(row)
            if item.get("triggered") and item.get("return") is not None:
                item["return"] = float(item["return"]) - cost
            adjusted.append(item)
        scenarios[name] = summarize_trades(adjusted, signal_dates)
    return scenarios


def build_backtest(days: list[dict], signal_days: int = 100, weights: dict | None = None) -> dict:
    del weights  # Walk-forward always starts at the fixed baseline; no full-period fitted weights.
    required_days = required_history_days(signal_days)
    if len(days) < required_days:
        raise ValueError(f"回測需要至少 {required_days} 個完整交易日")
    ordered = sorted(days, key=lambda row: row["date"])
    future_buffer = NEXT_DAY_EXECUTION_DAYS
    selected = list(range(len(ordered) - signal_days - future_buffer,
                          len(ordered) - future_buffer))
    if selected[0] < 5:
        raise ValueError("不足 5 個暖機交易日")
    trades, all_samples, weight_history, candidate_universe = [], [], [], []
    for i in selected:
        current = ordered[i]
        wf = walk_forward_weights(all_samples, current["date"])
        current_weights = dict(BASELINE_WEIGHTS)
        weight_history.append({"signal_date": current["date"], **wf,
                               "applied": False, "research_only": True})
        leaders = sorted(current["quotes"].values(), key=lambda row: (-row["volume"], row["code"]))[:100]
        rows = []
        for rank, quote in enumerate(leaders, 1):
            history = [ordered[j]["quotes"].get(quote["code"]) for j in range(i - 5, i + 1)]
            previous = ordered[i - 1]["quotes"].get(quote["code"])
            rows.append({**quote, "date": current["date"],
                         "prev_volume": previous["volume"] if previous is not None else None,
                         "closes": [item["close"] for item in history if item is not None],
                         "institutional_net": current["institutions"].get(quote["code"], 0),
                         "volume_rank": rank,
                         "technical_research": calculate_technical_research(
                             [{**item, "date": ordered[j]["date"]} for j, item in
                              zip(range(i - 5, i + 1), history) if item is not None],
                             current["date"])})
        evaluated = evaluate_candidates(rows, current_weights)
        signals = ([row for row in evaluated if row["signal_direction"] == "long"][:10] +
                   [row for row in evaluated if row["signal_direction"] == "short"][:10])
        selected_ranks = {
            (signal["code"], signal["signal_direction"]): rank
            for direction in ("long", "short")
            for rank, signal in enumerate(
                [row for row in signals if row["signal_direction"] == direction], 1
            )
        }
        for audited in audit_candidates(rows, current_weights):
            direction = audited.get("signal_direction")
            selected_rank = selected_ranks.get((audited["code"], direction))
            candidate_universe.append({
                field: value for field, value in {
                    "signal_date": audited.get("date"),
                    "code": audited.get("code"), "name": audited.get("name"),
                    "market": audited.get("market"), "volume_rank": audited.get("volume_rank"),
                    "close": audited.get("close"), "volume": audited.get("volume"),
                    "prev_volume": audited.get("prev_volume"),
                    "institutional_net": audited.get("institutional_net"),
                    "history_count": len(audited.get("closes") or []),
                    "ma5": audited.get("ma5"),
                    "volume_growth_pct": audited.get("volume_growth_pct"),
                    "above_ma5_pct": audited.get("above_ma5_pct"),
                    "ma5_distance_pct": audited.get("ma5_distance_pct"),
                    "institutional_lots": audited.get("institutional_lots"),
                    "institutional_abs_lots": audited.get("institutional_abs_lots"),
                    "volatility_proxy_pct": audited.get("volatility_proxy_pct"),
                    "technical_rsi_5": (audited.get("technical_research", {}).get("indicators", {}).get("rsi_5")),
                    "technical_atr_5_pct": (audited.get("technical_research", {}).get("indicators", {}).get("atr_5_pct")),
                    "technical_close_vs_prior5_high_pct": (audited.get("technical_research", {}).get("indicators", {}).get("close_vs_prior5_high_pct")),
                    "technical_close_vs_prior5_low_pct": (audited.get("technical_research", {}).get("indicators", {}).get("close_vs_prior5_low_pct")),
                    "eligible": audited.get("eligible"), "signal_direction": direction,
                    "rejection_reasons": audited.get("rejection_reasons", []),
                    "score": audited.get("score"), "reason": audited.get("reason"),
                    "action_text": audited.get("action_text"),
                    "entry_trigger": audited.get("entry_trigger"),
                    "stop_loss": audited.get("stop_loss"), "target1": audited.get("target1"),
                    "target2": audited.get("target2"), "risk_pct": audited.get("risk_pct"),
                    "entry_offset_pct": audited.get("entry_offset_pct"),
                    "selected_signal": selected_rank is not None, "selected_rank": selected_rank,
                }.items()
            })
        for signal in signals:
            next_day = ordered[i + 1]
            quote = next_day["quotes"].get(signal["code"])
            future = [] if quote is None else [{**quote, "date": next_day["date"]}]
            execution = simulate_trade(signal, future, require_full_horizon=True)
            trade = {"signal_date": current["date"], "code": signal["code"], "name": signal["name"],
                     "direction": signal["signal_direction"], "entry_trigger": signal["entry_trigger"],
                     "stop_loss": signal["stop_loss"], "target1": signal["target1"], "target2": signal["target2"],
                     "score": signal["score"], "volume_growth_pct": signal["volume_growth_pct"],
                     "ma5_distance_pct": signal["ma5_distance_pct"],
                     "institutional_abs_lots": signal["institutional_abs_lots"],
                     "volume_rank": signal["volume_rank"], "weights_used": current_weights, **execution}
            trade["trade_date"] = trade.get("entry_date")
            trade["gross_return"] = trade.get("return")
            trades.append(trade)
            if trade["status"] != "unresolved":
                all_samples.append(trade)
    signal_dates = [ordered[i]["date"] for i in selected]
    last_settle_date = max((row["exit_date"] for row in trades if row.get("exit_date")), default=signal_dates[-1])
    final_wf = walk_forward_weights(all_samples, "9999-12-31")
    optimization = {"source": "walk_forward_backtest_100d", "minimum_samples": MIN_SAMPLES,
                    "sample_count": final_wf["sample_count"], "changed": False,
                    "research_changed": final_wf["changed"], "approved": False,
                    "cost_validated": False, "research_only": True,
                    "period": {"from": signal_dates[0], "to": last_settle_date},
                    "old_weights": dict(BASELINE_WEIGHTS), "new_weights": dict(BASELINE_WEIGHTS),
                    "research_weights": final_wf["weights"],
                    "metrics": {"baseline": final_wf["baseline_metric"], "selected": final_wf["selected_metric"]},
                    "updated_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    metrics_scenarios = summarize_trade_scenarios(trades, signal_dates)
    portfolio_standard = simulate_portfolio(trades)
    return {"period": {"from": signal_dates[0], "to": signal_dates[-1]},
            "signal_dates": signal_dates, "metrics": metrics_scenarios["gross"],
            "metrics_scenarios": metrics_scenarios, "costs": dict(COST_SCENARIOS),
            "portfolio_standard": portfolio_standard,
            "trades": trades, "walk_forward": weight_history, "optimization": optimization,
            "_candidate_universe": candidate_universe,
            "cost_rate": 0.0,
            "cost_assumption": "gross 未扣成本；standard 每筆已成交扣雙邊總成本 0.435%；stress 扣 0.535%；未觸發不扣成本；未另計固定滑價、借券費與融券限制",
            "completed_at": datetime.now().astimezone().isoformat(timespec="seconds")}


TRADE_FIELDS = ["signal_date", "trade_date", "code", "name", "direction", "entry_date", "exit_date",
                "status", "entry_trigger", "entry", "exit", "exit_reason", "return", "triggered", "mfe", "mae", "score",
                "stop_loss", "target1", "target2", "volume_growth_pct", "ma5_distance_pct",
                "institutional_abs_lots", "volume_rank"]
CANDIDATE_AUDIT_FIELDS = [
    "signal_date", "code", "name", "market", "volume_rank", "close", "volume", "prev_volume",
    "institutional_net", "history_count", "ma5", "volume_growth_pct", "above_ma5_pct",
    "ma5_distance_pct", "institutional_lots", "institutional_abs_lots", "volatility_proxy_pct",
    "technical_rsi_5", "technical_atr_5_pct", "technical_close_vs_prior5_high_pct",
    "technical_close_vs_prior5_low_pct",
    "eligible", "signal_direction", "rejection_reasons", "score", "reason", "action_text",
    "entry_trigger", "stop_loss", "target1", "target2", "risk_pct", "entry_offset_pct",
    "selected_signal", "selected_rank",
]


def _candidate_summary(rows: list[dict], signal_dates: list[str], artifact: str) -> dict:
    rejection_counts = {}
    for row in rows:
        for reason in row.get("rejection_reasons", []):
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
    return {
        "signal_dates": len(signal_dates),
        "total_rows": len(rows),
        "eligible": sum(bool(row.get("eligible")) for row in rows),
        "selected": sum(bool(row.get("selected_signal")) for row in rows),
        "rejection_reasons": dict(sorted(rejection_counts.items())),
        "artifact": artifact,
    }


def _write_candidate_audit_atomic(rows: list[dict], path: Path) -> None:
    temporary = path.with_name(path.name + ".write.tmp")
    temporary.unlink(missing_ok=True)
    try:
        with gzip.open(temporary, "wt", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CANDIDATE_AUDIT_FIELDS, extrasaction="ignore")
            writer.writeheader()
            for source in rows:
                row = dict(source)
                row["rejection_reasons"] = "|".join(row.get("rejection_reasons", []))
                writer.writerow(row)
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _write_json_atomic(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False),
                         encoding="utf-8")
    os.replace(temporary, path)


def _portfolio_summary(portfolio: dict) -> dict:
    summary = {key: value for key, value in portfolio.items()
               if key not in {"realized_trades", "skipped", "equity_curve"}}
    summary["realized_trade_count"] = len(portfolio.get("realized_trades", []))
    summary["skipped_count"] = len(portfolio.get("skipped", []))
    summary["equity_curve_points"] = len(portfolio.get("equity_curve", []))
    return summary


def write_backtest_outputs(result: dict, directory: Path, days: int = 100) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    candidates = list(result.get("_candidate_universe", []))
    artifact_name = f"candidate_universe_{days}d.csv.gz"
    _write_candidate_audit_atomic(candidates, directory / artifact_name)
    public_result = {key: value for key, value in result.items() if not key.startswith("_")}
    public_result["candidate_universe_summary"] = _candidate_summary(
        candidates, result.get("signal_dates", []), artifact_name
    )
    portfolio = result.get("portfolio_standard")
    if portfolio is not None:
        _write_json_atomic(directory / f"portfolio_{days}d.json", portfolio)
        public_result["portfolio_standard"] = _portfolio_summary(portfolio)
    json_path = directory / f"backtest_{days}d.json"
    _write_json_atomic(json_path, public_result)
    csv_path = directory / f"backtest_{days}d.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRADE_FIELDS, extrasaction="ignore", lineterminator="\n")
        writer.writeheader(); writer.writerows(result["trades"])
    # Keep the old generic filename for existing integrations.
    (directory / "backtest_trades.csv").write_bytes(csv_path.read_bytes())
    db = sqlite3.connect(directory / "scanner.db")
    try:
        db.execute("""CREATE TABLE IF NOT EXISTS backtest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, completed_at TEXT NOT NULL, signal_days INTEGER NOT NULL,
            period_from TEXT NOT NULL, period_to TEXT NOT NULL, payload TEXT NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS backtest_trades (
            run_id INTEGER NOT NULL, signal_date TEXT NOT NULL, code TEXT NOT NULL, direction TEXT NOT NULL,
            status TEXT NOT NULL, triggered INTEGER NOT NULL, entry_date TEXT, exit_date TEXT,
            return_value REAL, payload TEXT NOT NULL,
            PRIMARY KEY(run_id,signal_date,code,direction))""")
        payload = json.dumps(public_result, ensure_ascii=False, allow_nan=False)
        cursor = db.execute("INSERT INTO backtest_runs(completed_at,signal_days,period_from,period_to,payload) VALUES(?,?,?,?,?)",
                            (result.get("completed_at", datetime.now().isoformat()), len(result["signal_dates"]),
                             result["period"]["from"], result["period"]["to"], payload))
        run_id = cursor.lastrowid
        for row in result["trades"]:
            db.execute("INSERT INTO backtest_trades VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (run_id, row["signal_date"], row["code"], row["direction"], row.get("status", ""),
                        int(bool(row.get("triggered"))), row.get("entry_date"), row.get("exit_date"),
                        row.get("return"), json.dumps(row, ensure_ascii=False)))
        db.commit()
    finally:
        db.close()
    audit = result.get("optimization")
    if audit and audit.get("source") == "walk_forward_backtest_100d":
        _write_json_atomic(directory / "optimizer.json", audit)
