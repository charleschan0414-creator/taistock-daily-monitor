#!/usr/bin/env python
"""Run and audit the post-close TaiStockScanner pipeline."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import run_backtest, run_strategy_optimization, update

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


def main():
    started = taipei_now()
    status = {"started_at": started, "completed_at": None, "status": "running", "data_date": None}
    atomic_json(DATA / "daily_status.json", status)
    try:
        backtest = run_backtest(100)
        strategy = run_strategy_optimization()
        latest = update(False)
        expected = backtest.get("data_collection", {}).get("latest_complete_date")
        if not expected:
            raise ValueError("backtest did not report latest_complete_date")
        validate_outputs(backtest, latest, expected)
        status.update({
            "completed_at": taipei_now(), "status": "success", "data_date": latest["data_date"],
            "backtest": {"period": backtest["period"], "metrics": backtest["metrics"]},
            "strategy": {"decision": strategy["decision"], "baseline": strategy["baseline"], "candidate": strategy["candidate"]},
            "gate": strategy["decision"].get("applied_gate"),
            "weights": latest.get("optimization", {}).get("new_weights") or latest.get("optimization", {}).get("weights"),
        })
        append_history_once(DATA / "strategy_history.jsonl", history_record(latest["data_date"], strategy))
        atomic_json(DATA / "daily_status.json", status)
        return 0
    except Exception as exc:
        status.update({"completed_at": taipei_now(), "status": "error", "error": f"{type(exc).__name__}: {exc}"})
        atomic_json(DATA / "daily_status.json", status)
        print(status["error"], file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
