#!/usr/bin/env python
"""Run and audit the post-close TaiStockScanner pipeline."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import run_backtest, run_strategy_optimization, update
from taistock.global_market import GlobalMarketClient
from taistock.health import build_health_report
from taistock.market_history import capture_market_history
from taistock.strategy_freeze import (
    apply_active_gate_to_report,
    build_registry_update,
    load_registry,
    write_registry,
)
from taistock.v2_optimize import optimize_v2

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
TAIPEI = timezone(timedelta(hours=8))


def taipei_now():
    return datetime.now(TAIPEI).isoformat(timespec="seconds")


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


def _app_version():
    try:
        return str(json.loads((ROOT / "software_manifest.json").read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return "unknown"


def write_status_with_health(status):
    """Write source status first, then attach a non-recursive health snapshot."""
    clean_status = {key: value for key, value in status.items() if key != "health"}
    path = DATA / "daily_status.json"
    atomic_json(path, clean_status)
    now = datetime.now(TAIPEI)
    final_status = dict(clean_status)
    final_status["health"] = build_health_report(DATA, _app_version(), now)
    atomic_json(path, final_status)


def validate_outputs(backtest, latest, expected_data_date):
    if backtest.get("metrics", {}).get("signal_days") != 100:
        raise ValueError("backtest must contain exactly 100 signal days")
    if latest.get("data_date") != expected_data_date:
        raise ValueError("latest data_date is not the newest complete official trading day")
    if latest.get("top100_count") != 100:
        raise ValueError("top100_count must be 100")
    if latest.get("history_success_count") != 100:
        raise ValueError("history_success_count must be 100")
    if latest.get("errors"):
        raise ValueError("latest contains collection errors")
    for direction in ("long", "short"):
        rows = latest.get(f"{direction}_top10", [])
        if len(rows) > 10:
            raise ValueError(f"{direction}_top10 exceeds 10 rows")
        for row in rows:
            risk = row.get("risk_pct")
            if not isinstance(risk, (int, float)) or not 1.5 <= risk <= 5:
                raise ValueError("risk_pct must be between 1.5 and 5")
            entry, stop, target = row.get("entry_trigger"), row.get("stop_loss"), row.get("target1")
            valid = stop < entry < target if direction == "long" else target < entry < stop
            if not valid:
                raise ValueError(f"invalid {direction} price direction")


def history_record(data_date, report):
    return {
        "data_date": data_date,
        "recorded_at": taipei_now(),
        "baseline": {"train": report["baseline"]["train"], "test": report["baseline"]["test"]},
        "candidate": {"train": report["candidate"]["train"], "test": report["candidate"]["test"]},
        "decision": {"enabled": report["decision"]["enabled"], "status": report["decision"]["status"]},
        "applied_gate": report["decision"].get("applied_gate"),
    }


def append_history_once(path, record):
    existing = []
    if path.exists():
        existing = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if any(item.get("data_date") == record["data_date"] for item in existing):
        return False
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
    return True


def record_market_history(client=None, clock=None):
    """Capture TAIFEX context without allowing it to affect stock-pipeline success."""
    try:
        result = capture_market_history(
            DATA,
            client or GlobalMarketClient(clock=clock),
            clock=clock,
        )
        snapshot = {"status": result["status"]}
        if result.get("snapshot_id"):
            snapshot["snapshot_id"] = result["snapshot_id"]
        if result.get("error"):
            snapshot["error"] = result["error"]
        return {**result, "history_snapshot": snapshot, "affects_stock_scores": False}
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        regime = None
        path = DATA / "market_regime.json"
        try:
            if path.exists():
                loaded = json.loads(path.read_text(encoding="utf-8"))
                regime = loaded if isinstance(loaded, dict) else None
        except (OSError, ValueError, TypeError):
            regime = None
        return {
            "status": "error",
            "error": error,
            "history_snapshot": {"status": "error", "error": error},
            "regime": regime,
            "affects_stock_scores": False,
        }


def main():
    started = taipei_now()
    status = {"started_at": started, "completed_at": None, "status": "running", "data_date": None}
    strategy_report_path = DATA / "strategy_optimization.json"
    previous_strategy_report = None
    strategy_report_staged = False
    atomic_json(DATA / "daily_status.json", status)
    try:
        backtest = run_backtest(100)
        v2_report = optimize_v2(backtest, DATA / "v2_optimization.json")
        registry_path = DATA / "strategy_registry.json"
        existing_registry = load_registry(registry_path)
        incumbent_gate = existing_registry["active_gate"] if existing_registry else None
        strategy = run_strategy_optimization(
            backtest=backtest, incumbent_gate=incumbent_gate, write_output=False)
        expected = backtest.get("data_collection", {}).get("latest_complete_date")
        if not expected:
            raise ValueError("backtest did not report latest_complete_date")
        proposed_registry = build_registry_update(existing_registry, strategy, expected)
        effective_strategy = apply_active_gate_to_report(strategy, proposed_registry)
        # app.update reads this report; publish the optimizer decision separately inside it.
        if strategy_report_path.exists():
            previous_strategy_report = json.loads(strategy_report_path.read_text(encoding="utf-8"))
        atomic_json(strategy_report_path, effective_strategy)
        strategy_report_staged = True
        latest = update(False)
        validate_outputs(backtest, latest, expected)
        market_history = record_market_history()
        write_registry(registry_path, proposed_registry)
        status.update({
            "completed_at": taipei_now(), "status": "success", "data_date": latest["data_date"],
            "backtest": {
                "period": backtest["period"],
                "metrics": backtest["metrics"],
                "metrics_scenarios": backtest.get("metrics_scenarios") or {"gross": backtest["metrics"]},
                "costs": backtest.get("costs") or {"gross": 0.0, "standard": 0.00435, "stress": 0.00535},
                "portfolio_standard": {
                    key: value for key, value in backtest.get("portfolio_standard", {}).items()
                    if key not in {"realized_trades", "skipped", "equity_curve"}
                },
            },
            "strategy": {"decision": strategy["decision"], "baseline": strategy["baseline"], "candidate": strategy["candidate"]},
            "strategy_freeze": {
                **effective_strategy["strategy_freeze"],
                "active_gate": proposed_registry["active_gate"],
                "last_observed_date": proposed_registry["last_observed_date"],
                "pending_candidate": proposed_registry.get("pending_candidate"),
            },
            "v2_analysis": {
                "version": v2_report["version"],
                "costs": v2_report["costs"],
                "decision": {
                    "enabled": v2_report["decision"]["enabled"],
                    "status": v2_report["decision"]["status"],
                },
                "directions": {
                    name: {"passed": bool(result["passed"]), "holdout": result["holdout"]}
                    for name, result in v2_report["directions"].items()
                },
                "limitations": v2_report["limitations"],
            },
            "gate": proposed_registry["active_gate"],
            "weights": latest.get("optimization", {}).get("new_weights") or latest.get("optimization", {}).get("weights"),
            "market_history": market_history,
        })
        append_history_once(DATA / "strategy_history.jsonl", history_record(latest["data_date"], effective_strategy))
        write_status_with_health(status)
        return 0
    except Exception as exc:
        if strategy_report_staged:
            if previous_strategy_report is None:
                strategy_report_path.unlink(missing_ok=True)
            else:
                atomic_json(strategy_report_path, previous_strategy_report)
        status.update({"completed_at": taipei_now(), "status": "error", "error": f"{type(exc).__name__}: {exc}"})
        write_status_with_health(status)
        print(status["error"], file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
