"""以 70/30 時間切分評估有限網格多空品質閘門。"""
from __future__ import annotations

import itertools
import json
import math
import statistics
from datetime import datetime
from pathlib import Path

from .backtest import summarize_trades

VERSION = 2
TRAIN_DAYS = 70
TEST_DAYS = 30
MIN_TRAIN_TRADES = 100
MIN_TEST_TRADES = 30
STANDARD_COST = 0.00435
STRESS_COST = 0.00535
BASELINE_GATE = {
    "long_score_min": 55,
    "short_score_min": 70,
    "short_enabled": True,
    "volume_growth_min": 20,
}
GRID = {
    "long_score_min": (0, 55, 70),
    "short_score_min": (0, 55, 70),
    "short_enabled": (True, False),
    "volume_growth_min": (20, 50),
}
METRIC_KEYS = (
    "signals", "trades", "win_rate", "average_return", "median_return",
    "cumulative_return", "max_drawdown", "profit_factor",
)


def _passes(row: dict, gate: dict) -> bool:
    direction = row.get("direction", row.get("signal_direction"))
    if direction == "short" and not gate["short_enabled"]:
        return False
    minimum = gate["long_score_min"] if direction == "long" else gate["short_score_min"]
    return (float(row.get("score", 0) or 0) >= minimum
            and float(row.get("volume_growth_pct", 0) or 0) >= gate["volume_growth_min"])


def apply_strategy_gate(rows: list[dict], state: dict | None) -> list[dict]:
    """候選升級或既有保護模式都套用其門檻；僅舊版停用狀態維持相容。"""
    if not state:
        return list(rows)
    if not state.get("applied_mode") and not state.get("enabled"):
        return list(rows)
    gate = state.get("applied_gate") or BASELINE_GATE
    return [row for row in rows if _passes(row, gate)]


def _batch_mean(trades: list[dict], dates: list[str]) -> float:
    executed = [row for row in trades if row.get("triggered") and row.get("return") is not None]
    daily = []
    for day in dates:
        values = [float(row["return"]) for row in executed if row.get("signal_date") == day]
        daily.append(sum(values) / len(values) if values else 0.0)
    return sum(daily) / len(daily) if daily else 0.0


def _metrics(trades: list[dict], dates: list[str]) -> dict:
    raw = summarize_trades(trades, dates)
    result = {key: raw[key] for key in METRIC_KEYS}
    result["signal_days"] = len(dates)
    result["average_signal_batch_return"] = _batch_mean(trades, dates)
    result["cumulative_signal_batch_metric"] = raw["cumulative_return"]
    return result


def _cost_metrics(trades: list[dict], dates: list[str], cost: float) -> dict:
    adjusted = [dict(row, **({"return": float(row["return"]) - cost}
                            if row.get("triggered") and row.get("return") is not None else {}))
                for row in trades]
    return _metrics(adjusted, dates)


def _metric_set(trades: list[dict], dates: list[str]) -> dict:
    return {"gross": _metrics(trades, dates),
            "standard": _cost_metrics(trades, dates, STANDARD_COST),
            "stress": _cost_metrics(trades, dates, STRESS_COST)}


def _train_robust(candidate: dict, baseline: dict) -> bool:
    """訓練期先排除 PF<=1 或最大回撤惡化超過 5 個百分點的脆弱候選。"""
    pf = candidate["profit_factor"]
    return ((pf is None or pf > 1.0)
            and candidate["max_drawdown"] + 1e-12 >= baseline["max_drawdown"] - 0.05)


def _objective(metrics: dict, baseline_trades: int) -> tuple[float, float]:
    """報酬/批次期望為主，回撤/PF 次之，勝率只給極小權重。"""
    profit_factor = metrics["profit_factor"]
    bounded_pf = 3.0 if profit_factor is None else min(max(float(profit_factor), 0.0), 3.0)
    trade_ratio = metrics["trades"] / max(1, baseline_trades)
    sparse_penalty = max(0.0, 0.50 - trade_ratio) * 0.20
    score = (100 * metrics["average_return"]
             + 100 * metrics["average_signal_batch_return"]
             + 0.25 * metrics["max_drawdown"]
             + 0.05 * bounded_pf
             + 0.01 * metrics["win_rate"]
             - sparse_penalty)
    return score, sparse_penalty


def _candidates():
    keys = tuple(GRID)
    for values in itertools.product(*(GRID[key] for key in keys)):
        yield dict(zip(keys, values))


def _date_range(dates: list[str]) -> dict:
    return {"from": dates[0], "to": dates[-1], "days": len(dates)}


def _holdout_checks(candidate_standard: dict, baseline_standard: dict,
                    candidate_stress: dict) -> dict:
    allowed_dd = baseline_standard["max_drawdown"] - 0.02
    standard_pf = candidate_standard["profit_factor"]
    stress_pf = candidate_stress["profit_factor"]
    return {
        "standard_minimum_test_trades_30": candidate_standard["trades"] >= MIN_TEST_TRADES,
        "standard_average_return_above_0": candidate_standard["average_return"] > 0,
        "standard_profit_factor_above_1": standard_pf is None or standard_pf > 1.0,
        "standard_max_drawdown_not_worse_than_incumbent_by_more_than_2pp": (
            candidate_standard["max_drawdown"] >= allowed_dd),
        "stress_average_return_above_0": candidate_stress["average_return"] > 0,
        "stress_profit_factor_above_1": stress_pf is None or stress_pf > 1.0,
    }


def optimize_strategy(backtest: dict, output_path: Path | None = None) -> dict:
    dates = sorted(dict.fromkeys(backtest.get("signal_dates", [])))
    if len(dates) != TRAIN_DAYS + TEST_DAYS:
        raise ValueError("策略優化需要恰好 100 個唯一訊號日（前70訓練、後30保留測試）")
    train_dates, test_dates = dates[:TRAIN_DAYS], dates[TRAIN_DAYS:]
    train_set, test_set = set(train_dates), set(test_dates)
    trades = list(backtest.get("trades", []))
    train = [row for row in trades if row.get("signal_date") in train_set]
    test = [row for row in trades if row.get("signal_date") in test_set]
    baseline_train_rows = [row for row in train if _passes(row, BASELINE_GATE)]
    baseline_test_rows = [row for row in test if _passes(row, BASELINE_GATE)]
    baseline_train = _metric_set(baseline_train_rows, train_dates)
    baseline_test = _metric_set(baseline_test_rows, test_dates)

    evaluated = []
    for gate in _candidates():
        selected = [row for row in train if _passes(row, gate)]
        metrics = _metric_set(selected, train_dates)
        standard = metrics["standard"]
        if standard["trades"] < MIN_TRAIN_TRADES or not _train_robust(standard, baseline_train["standard"]):
            continue
        objective, penalty = _objective(standard, baseline_train["standard"]["trades"])
        evaluated.append((objective, -penalty, standard["trades"], gate, metrics, penalty))
    if not evaluated:
        raise ValueError("有限網格沒有符合至少 100 筆訓練成交的候選")
    _, _, _, selected_gate, candidate_train, sparse_penalty = max(
        evaluated, key=lambda item: (item[0], item[1], item[2]))
    candidate_test_rows = [row for row in test if _passes(row, selected_gate)]
    candidate_test = _metric_set(candidate_test_rows, test_dates)
    checks = _holdout_checks(candidate_test["standard"], baseline_test["standard"],
                             candidate_test["stress"])
    upgrade_approved = selected_gate != BASELINE_GATE and all(checks.values())
    enabled = upgrade_approved
    applied_gate = dict(selected_gate if upgrade_approved else BASELINE_GATE)
    applied_mode = "candidate" if upgrade_approved else "incumbent_protective"
    direction_text = "偏空啟用" if applied_gate["short_enabled"] else "偏空停用"
    status = (f"已啟用｜偏多分數≥{applied_gate['long_score_min']}、偏空分數≥{applied_gate['short_score_min']}、"
              f"{direction_text}、量增≥{applied_gate['volume_growth_min']}%｜成本後驗證通過" if upgrade_approved
              else "保留既有保護性篩選｜成本後升級未通過")
    report = {
        "version": VERSION,
        "method": {
            "split": "first_70_signal_days_train_last_30_holdout",
            "selection_uses_holdout_outcomes": False,
            "selection_metric": "standard_cost_after",
            "incumbent": dict(BASELINE_GATE),
            "grid": {key: list(values) for key, values in GRID.items()},
            "objective": ("標準成本後：100*平均逐筆報酬 + 100*平均訊號日批次報酬 + 0.25*最大回撤 "
                          "+ 0.05*min(PF,3) + 0.01*勝率 - 交易過少懲罰"),
            "sparse_trade_penalty": "交易數低於基準50%時，每少1比例扣0.20；另硬性要求訓練成交>=100",
            "training_robustness_constraints": "訓練 PF>1，且最大回撤不得比訓練基準惡化超過 5 個百分點",
        },
        "train_dates": _date_range(train_dates),
        "test_dates": _date_range(test_dates),
        "candidate_count": math.prod(len(values) for values in GRID.values()),
        "eligible_train_candidate_count": len(evaluated),
        "minimum_samples": {"train_trades": MIN_TRAIN_TRADES, "test_trades": MIN_TEST_TRADES},
        "baseline_gate": dict(BASELINE_GATE),
        "selected_gate": dict(selected_gate),
        "costs": {"standard": STANDARD_COST, "stress": STRESS_COST},
        "baseline": {"train": baseline_train, "test": baseline_test},
        "candidate": {"train": candidate_train, "test": candidate_test},
        "train_sparse_trade_penalty": sparse_penalty,
        "holdout_thresholds": {
            "standard": {"average_return": "> 0", "profit_factor": "> 1",
                         "test_trades": ">= 30",
                         "max_drawdown": "不可比 incumbent 惡化超過 2 個百分點"},
            "stress": {"average_return": "> 0", "profit_factor": "> 1"},
        },
        "decision": {"enabled": enabled, "upgrade_approved": upgrade_approved,
                     "cost_validated": upgrade_approved, "applied_mode": applied_mode,
                     "checks": checks, "applied_gate": applied_gate, "status": status},
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    if output_path is not None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return report


def load_strategy_gate(directory: Path) -> dict:
    path = Path(directory) / "strategy_optimization.json"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
        decision = report["decision"]
        cost_validated = bool(decision.get("cost_validated", False))
        approved = (cost_validated and decision.get("applied_mode") == "candidate"
                    and bool(decision.get("upgrade_approved")))
        gate = decision["applied_gate"] if approved else dict(BASELINE_GATE)
        status = (decision.get("status", "策略閘門狀態不明") if approved or
                  decision.get("applied_mode") == "incumbent_protective"
                  else "保留既有保護性篩選｜舊版報告未經成本後驗證")
        return {"version": report.get("version", VERSION), "enabled": approved,
                "applied_mode": "candidate" if approved else "incumbent_protective",
                "cost_validated": approved, "status": status, "applied_gate": gate,
                "directions": {"long": True, "short": bool(gate.get("short_enabled", True))},
                "updated_at": report.get("updated_at")}
    except (OSError, ValueError, KeyError, TypeError):
        return {"version": VERSION, "enabled": False, "applied_mode": "incumbent_protective",
                "cost_validated": False, "status": "保留既有保護性篩選｜尚無有效成本後升級報告",
                "applied_gate": dict(BASELINE_GATE), "directions": {"long": True, "short": True},
                "updated_at": None}
