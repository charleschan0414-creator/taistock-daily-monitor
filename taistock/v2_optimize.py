"""成本敏感 V2.1 研究優化；只產生稽核報告，不修改即時選股規則。"""
from __future__ import annotations

import itertools
import json
import math
from collections import defaultdict
from datetime import datetime
from pathlib import Path

VERSION = "2.1"
TRAIN_DAYS = 70
HOLDOUT_DAYS = 30
COSTS = {"standard": 0.00435, "stress": 0.00535}
GRID = {
    "score_min": (50, 60, 70, 80, 90),
    "volume_rank_max": (20, 50, 100),
    "volume_growth_min": (20, 50, 100),
    "ma5_distance_max": (3, 5, 10, 20),
    "daily_limit": (3, 5),
}


def _select_rows(rows: list[dict], direction: str, parameters: dict) -> list[dict]:
    """先套固定門檻，再按日、方向以分數降冪及量排名升冪取限額。"""
    by_day = defaultdict(list)
    for row in rows:
        rank = row.get("volume_rank")
        if (row.get("direction", row.get("signal_direction")) != direction
                or float(row.get("score", 0) or 0) < parameters["score_min"]
                or rank is None or float(rank) > parameters["volume_rank_max"]
                or float(row.get("volume_growth_pct", 0) or 0) < parameters["volume_growth_min"]
                or abs(float(row.get("ma5_distance_pct", 0) or 0)) > parameters["ma5_distance_max"]):
            continue
        by_day[row.get("signal_date")].append(row)
    selected = []
    for day in sorted(by_day):
        ranked = sorted(by_day[day], key=lambda row: (
            -float(row.get("score", 0) or 0), float(row["volume_rank"]), str(row.get("code", ""))))
        selected.extend(ranked[:parameters["daily_limit"]])
    return selected


def _net_metrics(trades: list[dict], signal_dates: list[str], cost: float) -> dict:
    """每筆已成交毛報酬扣一次指定雙邊總成本，再按訊號日等權複利。"""
    requested_dates = set(signal_dates)
    executed = [row for row in trades
                if row.get("signal_date") in requested_dates
                and row.get("triggered") and row.get("return") is not None]
    returns = [float(row["return"]) - cost for row in executed]
    daily_returns = []
    for day in signal_dates:
        values = [float(row["return"]) - cost for row in executed if row.get("signal_date") == day]
        daily_returns.append(sum(values) / len(values) if values else 0.0)

    equity = peak = 1.0
    max_drawdown = 0.0
    for value in daily_returns:
        equity *= 1.0 + value
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1.0)
    gains = sum(value for value in returns if value > 0)
    losses = -sum(value for value in returns if value < 0)
    return {
        "trades": len(returns),
        "win_rate": sum(value > 0 for value in returns) / len(returns) if returns else 0.0,
        "average_return": sum(returns) / len(returns) if returns else 0.0,
        "profit_factor": gains / losses if losses else None,
        "max_drawdown": max_drawdown,
        "cumulative_return": equity - 1.0,
    }


def _date_range(dates: list[str]) -> dict:
    return {"from": dates[0], "to": dates[-1], "days": len(dates)}


def _parameter_grid():
    keys = tuple(GRID)
    for values in itertools.product(*(GRID[key] for key in keys)):
        yield dict(zip(keys, values))


def _objective(metrics: dict) -> tuple:
    pf = metrics["profit_factor"]
    effective_pf = 999.0 if pf is None and metrics["trades"] else (pf or 0.0)
    return (metrics["average_return"], metrics["cumulative_return"], effective_pf,
            metrics["max_drawdown"], metrics["trades"])


def _scenario_metrics(rows: list[dict], dates: list[str]) -> dict:
    return {name: _net_metrics(rows, dates, cost) for name, cost in COSTS.items()}


def _pf_above(value, threshold: float, trades: int) -> bool:
    return bool(trades) and (value is None or value > threshold)


def _rolling_checks(rows: list[dict], holdout_dates: list[str]) -> dict:
    windows = []
    if len(holdout_dates) < 22:
        return {"window_days": 20, "required_profitable_windows": 2, "windows": [],
                "profitable_windows": 0, "data_sufficient": False, "passed": False,
                "reason": "資料不足：需要至少22個保留訊號日形成連續三個20日滾動窗口"}
    for end in range(len(holdout_dates) - 3, len(holdout_dates)):
        dates = holdout_dates[end - 19:end + 1]
        metrics = _net_metrics(rows, dates, COSTS["standard"])
        available = metrics["trades"] > 0
        passed = available and _pf_above(metrics["profit_factor"], 1.0, metrics["trades"])
        windows.append({"from": dates[0], "to": dates[-1], "days": 20,
                        "trades": metrics["trades"], "profit_factor": metrics["profit_factor"],
                        "data_sufficient": available, "profit_factor_above_1": passed})
    sufficient = all(window["data_sufficient"] for window in windows)
    profitable = sum(window["profit_factor_above_1"] for window in windows)
    passed = sufficient and profitable >= 2
    reason = ("通過：三個窗口至少兩個 standard PF>1" if passed else
              "資料不足：至少一個20日窗口沒有已成交樣本" if not sufficient else
              "不通過：三個窗口中少於兩個 standard PF>1")
    return {"window_days": 20, "required_profitable_windows": 2, "windows": windows,
            "profitable_windows": profitable, "data_sufficient": sufficient,
            "passed": passed, "reason": reason}


def _optimize_direction(train_rows: list[dict], holdout_rows: list[dict], train_dates: list[str],
                        holdout_dates: list[str], direction: str) -> dict:
    evaluated = []
    for parameters in _parameter_grid():
        selected = _select_rows(train_rows, direction, parameters)
        scenarios = _scenario_metrics(selected, train_dates)
        evaluated.append((_objective(scenarios["standard"]), parameters, scenarios))
    _, parameters, train_scenarios = max(evaluated, key=lambda item: item[0])
    holdout_selected = _select_rows(holdout_rows, direction, parameters)
    holdout_scenarios = _scenario_metrics(holdout_selected, holdout_dates)
    standard = holdout_scenarios["standard"]
    rolling = _rolling_checks(holdout_selected, holdout_dates)
    checks = {
        "standard_profit_factor_at_least_1_3": _pf_above(
            standard["profit_factor"], 1.3 - 1e-12, standard["trades"]),
        "standard_average_return_above_0": standard["average_return"] > 0,
        "holdout_trades_at_least_30": standard["trades"] >= 30,
        "standard_max_drawdown_at_least_minus_0_10": standard["max_drawdown"] >= -0.10,
        "rolling_20_signal_day_windows": rolling["passed"],
    }
    return {"selected_parameters": parameters, "selection": train_scenarios,
            "holdout": holdout_scenarios, "activation_checks": checks,
            "rolling_20_signal_day_checks": rolling, "passed": all(checks.values())}


def optimize_v2(backtest: dict, output_path=None) -> dict:
    """以前70訊號日選候選、後30日只判定啟用，並回傳/寫入研究稽核報告。"""
    dates = sorted(dict.fromkeys(backtest.get("signal_dates", [])))
    if len(dates) != TRAIN_DAYS + HOLDOUT_DAYS:
        raise ValueError("V2.1 優化需要恰好 100 個唯一訊號日（前70選擇、後30啟用判定）")
    train_dates, holdout_dates = dates[:TRAIN_DAYS], dates[TRAIN_DAYS:]
    train_set, holdout_set = set(train_dates), set(holdout_dates)
    trades = list(backtest.get("trades", []))
    train_rows = [row for row in trades if row.get("signal_date") in train_set]
    holdout_rows = [row for row in trades if row.get("signal_date") in holdout_set]
    directions = {
        direction: _optimize_direction(train_rows, holdout_rows, train_dates, holdout_dates, direction)
        for direction in ("long", "short")
    }
    enabled = all(result["passed"] for result in directions.values())
    status = ("通過研究啟用門檻；本報告仍不套用即時選股規則" if enabled else
              "未通過研究啟用門檻；保留現有 strategy_optimization 與即時選股規則")
    report = {
        "version": VERSION,
        "report_type": "research_audit_only",
        "method": {
            "split": "first_70_signal_days_selection_last_30_activation_only",
            "selection_uses_holdout_outcomes": False,
            "ranking": "each signal day and direction: score descending, volume_rank ascending",
            "selection_objective": "standard net average return, then cumulative return, PF, drawdown, trades",
        },
        "train_dates": _date_range(train_dates),
        "holdout_dates": _date_range(holdout_dates),
        "costs": dict(COSTS),
        "cost_definition": "每筆已成交 return 扣除一次雙邊總成本（小數報酬）",
        "grid": {key: list(values) for key, values in GRID.items()},
        "candidate_count_per_direction": math.prod(len(values) for values in GRID.values()),
        "activation_thresholds": {"standard_profit_factor": ">=1.3", "standard_average_return": ">0",
                                  "holdout_trades": ">=30", "standard_max_drawdown": ">=-0.10",
                                  "rolling_windows": "latest 3 rolling 20-signal-day windows; at least 2 standard PF>1"},
        "directions": directions,
        "decision": {"enabled": enabled, "status": status,
                     "changes_existing_strategy_optimization": False,
                     "changes_live_screening_rules": False},
        "limitations": {
            "source_is_daily_ohlc": True,
            "not_true_intraday": True,
            "claims": ["僅模擬次一交易日當沖並於收盤強制沖銷", "不可宣稱60%勝率"],
            "notice": "日 OHLC 只能保守模擬次一交易日當沖，不能重建盤中先後；不跨日持有，也不保證勝率或獲利。",
        },
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return report
