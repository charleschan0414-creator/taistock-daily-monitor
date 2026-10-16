"""使用 TWSE/TPEx 官方逐日資料的無前視多空回測。"""
from __future__ import annotations

import csv
import gzip
import json
import sqlite3
import statistics
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from .core import BASELINE_WEIGHTS, evaluate_candidates, is_common_stock
from .data import _number
from .storage import CANDIDATE_WEIGHTS, MIN_SAMPLES

HOLDING_DAYS = 5


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
    """收盤訊號最早次日觸發；最多五日，OHLC 同日採停損、target1 保守優先。"""
    single_day_call = isinstance(future_days, dict)
    days = [future_days] if single_day_call else list(future_days[:HOLDING_DAYS])
    if require_full_horizon and len(days) < HOLDING_DAYS:
        return {"status": "unresolved", "triggered": False, "entry_date": None, "exit": None,
                "exit_date": None, "exit_reason": "insufficient_future_days", "return": None,
                "mfe": None, "mae": None}
    direction = signal["signal_direction"]
    entry, stop = float(signal["entry_trigger"]), float(signal["stop_loss"])
    target1, target2 = float(signal["target1"]), float(signal["target2"])
    triggered = False
    entry_date = None
    highs, lows = [], []
    for index, day in enumerate(days):
        touched = day["high"] >= entry if direction == "long" else day["low"] <= entry
        if not triggered and not touched:
            continue
        if not triggered:
            triggered, entry_date = True, day.get("date")
        highs.append(float(day["high"])); lows.append(float(day["low"]))
        stop_hit = day["low"] <= stop if direction == "long" else day["high"] >= stop
        target_hit = day["high"] >= target1 if direction == "long" else day["low"] <= target1
        target2_hit = day["high"] >= target2 if direction == "long" else day["low"] <= target2
        if stop_hit:
            exit_price, reason = stop, "stop"
        elif target_hit:
            exit_price, reason = target1, "target1"
        elif target2_hit:
            exit_price, reason = target2, "target2"
        elif index == len(days) - 1:
            exit_price, reason = float(day["close"]), "close" if single_day_call else "day5_close"
        else:
            continue
        value = ((exit_price - entry) / entry if direction == "long" else (entry - exit_price) / entry)
        favorable = (max(highs) - entry) / entry if direction == "long" else (entry - min(lows)) / entry
        adverse = (min(lows) - entry) / entry if direction == "long" else (entry - max(highs)) / entry
        return {"status": "settled", "triggered": True, "entry_date": entry_date,
                "exit": exit_price, "exit_date": day.get("date"), "exit_reason": reason,
                "return": value, "mfe": favorable, "mae": adverse}
    if not triggered:
        return {"status": "untriggered", "triggered": False, "entry_date": None, "exit": None,
                "exit_date": days[-1].get("date") if days else None, "exit_reason": "untriggered",
                "return": None, "mfe": None, "mae": None}
    # This occurs only when a caller explicitly allows a short horizon.
    day = days[-1]
    exit_price = float(day["close"])
    value = ((exit_price - entry) / entry if direction == "long" else (entry - exit_price) / entry)
    favorable = (max(highs) - entry) / entry if direction == "long" else (entry - min(lows)) / entry
    adverse = (min(lows) - entry) / entry if direction == "long" else (entry - max(highs)) / entry
    return {"status": "settled", "triggered": True, "entry_date": entry_date,
            "exit": exit_price, "exit_date": day.get("date"), "exit_reason": "close",
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
            scored.append((score, row["return"]))
        top = sorted(scored, reverse=True)[:3]
        daily.append(sum(value for _, value in top) / len(top))
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


def build_backtest(days: list[dict], signal_days: int = 100, weights: dict | None = None) -> dict:
    del weights  # Walk-forward always starts at the fixed baseline; no full-period fitted weights.
    if signal_days < 1 or len(days) < signal_days + 6:
        raise ValueError(f"回測需要至少 {signal_days + 6} 個完整交易日")
    ordered = sorted(days, key=lambda row: row["date"])
    # Prefer five complete future days. Small fixture compatibility keeps one future day.
    future_buffer = HOLDING_DAYS if len(ordered) >= signal_days + 10 else 1
    selected = list(range(len(ordered) - signal_days - future_buffer,
                          len(ordered) - future_buffer))
    if selected[0] < 5:
        raise ValueError("不足 5 個暖機交易日")
    trades, all_samples, weight_history = [], [], []
    for i in selected:
        current = ordered[i]
        wf = walk_forward_weights(all_samples, current["date"])
        current_weights = wf["weights"]
        weight_history.append({"signal_date": current["date"], **wf})
        leaders = sorted(current["quotes"].values(), key=lambda row: (-row["volume"], row["code"]))[:100]
        rows = []
        for rank, quote in enumerate(leaders, 1):
            history = [ordered[j]["quotes"].get(quote["code"]) for j in range(i - 4, i + 1)]
            previous = ordered[i - 1]["quotes"].get(quote["code"])
            if any(item is None for item in history) or previous is None:
                continue
            rows.append({**quote, "date": current["date"], "prev_volume": previous["volume"],
                         "closes": [item["close"] for item in history],
                         "institutional_net": current["institutions"].get(quote["code"], 0),
                         "volume_rank": rank})
        evaluated = evaluate_candidates(rows, current_weights)
        signals = ([row for row in evaluated if row["signal_direction"] == "long"][:10] +
                   [row for row in evaluated if row["signal_direction"] == "short"][:10])
        for signal in signals:
            future = []
            for day in ordered[i + 1:i + 1 + HOLDING_DAYS]:
                quote = day["quotes"].get(signal["code"])
                if quote is None:
                    break
                future.append({**quote, "date": day["date"]})
            execution = simulate_trade(signal, future, require_full_horizon=(future_buffer == HOLDING_DAYS))
            trade = {"signal_date": current["date"], "code": signal["code"], "name": signal["name"],
                     "direction": signal["signal_direction"], "entry": signal["entry_trigger"],
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
                    "sample_count": final_wf["sample_count"], "changed": final_wf["changed"],
                    "period": {"from": signal_dates[0], "to": last_settle_date},
                    "old_weights": dict(BASELINE_WEIGHTS), "new_weights": final_wf["weights"],
                    "metrics": {"baseline": final_wf["baseline_metric"], "selected": final_wf["selected_metric"]},
                    "updated_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    return {"period": {"from": signal_dates[0], "to": signal_dates[-1]},
            "signal_dates": signal_dates, "metrics": summarize_trades(trades, signal_dates),
            "trades": trades, "walk_forward": weight_history, "optimization": optimization,
            "cost_rate": 0.0, "cost_assumption": "未計入手續費、交易稅、滑價、借券費與融券限制",
            "completed_at": datetime.now().astimezone().isoformat(timespec="seconds")}


TRADE_FIELDS = ["signal_date", "trade_date", "code", "name", "direction", "entry_date", "exit_date",
                "status", "entry", "exit", "exit_reason", "return", "triggered", "mfe", "mae", "score",
                "stop_loss", "target1", "target2", "volume_growth_pct", "ma5_distance_pct",
                "institutional_abs_lots", "volume_rank"]


def write_backtest_outputs(result: dict, directory: Path, days: int = 100) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"backtest_{days}d.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    csv_path = directory / f"backtest_{days}d.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRADE_FIELDS, extrasaction="ignore")
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
        payload = json.dumps(result, ensure_ascii=False, allow_nan=False)
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
        (directory / "optimizer.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
