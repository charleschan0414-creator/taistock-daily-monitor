#!/usr/bin/env python
"""Run and audit the post-close TaiStockScanner pipeline."""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app import run_backtest, run_strategy_optimization, update, validate_daily_scan
from taistock.global_market import GlobalMarketClient
from taistock.health import build_health_report
from taistock.market_history import capture_market_history
from taistock.strategy_campaign import build_campaign_status, update_unique_signal_dates
from taistock.strategy_freeze import (
    apply_active_gate_to_report,
    build_registry_update,
    load_registry,
    write_registry,
)
from taistock.strategy_optimize import apply_strategy_gate
from taistock.v2_optimize import optimize_v2

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
TAIPEI = timezone(timedelta(hours=8))
CAMPAIGN_CONFIG = ROOT / "strategy_campaign.json"


def taipei_now():
    return datetime.now(TAIPEI).isoformat(timespec="seconds")


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


def load_campaign_status(backtest, data_date, pending_candidate=None, previous_campaign=None):
    """Read the bounded campaign config and compare like-for-like Standard metrics."""
    if not CAMPAIGN_CONFIG.exists():
        return None
    config = json.loads(CAMPAIGN_CONFIG.read_text(encoding="utf-8"))
    scenarios = backtest.get("metrics_scenarios") or {}
    current_standard = scenarios.get("standard") or backtest.get("metrics", {})
    previous = previous_campaign if isinstance(previous_campaign, dict) else {}
    previous_dates = (previous.get("unique_signal_dates", [])
                      if previous.get("id") == config["id"] else [])
    dates = update_unique_signal_dates(config, previous_dates, data_date)
    pending = pending_candidate if isinstance(pending_candidate, dict) else None
    if pending:
        try:
            proposed = date.fromisoformat(pending.get("proposed_on", ""))
            inside = (date.fromisoformat(config["start_date"]) <= proposed
                      <= date.fromisoformat(config["end_date"]))
        except (TypeError, ValueError):
            inside = False
        if pending.get("campaign_id") != config["id"] or not inside:
            pending = None
    return build_campaign_status(
        config, data_date, current_standard,
        candidate_found=pending is not None,
        unique_signal_dates=dates,
        candidate_proposed_on=pending.get("proposed_on") if pending else None,
        candidate_forward_signal_days=pending.get("forward_signal_days", 0) if pending else 0,
    )


def previous_campaign_state(path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        campaign = payload.get("strategy_campaign", {})
        return campaign if isinstance(campaign, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def campaign_id_for_date(config, data_date):
    """Attach a campaign only while its configured calendar window is active."""
    try:
        current = date.fromisoformat(data_date)
        start = date.fromisoformat(config["start_date"])
        end = date.fromisoformat(config["end_date"])
        campaign_id = config["id"]
    except (KeyError, TypeError, ValueError):
        return None
    return campaign_id if start <= current <= end else None


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
    validate_daily_scan(latest, expected_data_date)


def build_today_result(backtest, active_gate):
    """Summarize the newest fully settled signal date under the active gate."""
    gated = apply_strategy_gate(
        backtest.get("trades", []),
        {"enabled": True, "applied_mode": "incumbent_protective", "applied_gate": active_gate},
    )
    groups = {}
    for row in gated:
        if row.get("status") in {"settled", "untriggered"}:
            groups.setdefault(row.get("signal_date"), []).append(row)
    complete_dates = [day for day, rows in groups.items() if day and rows and all(
        row.get("status") == "untriggered" or row.get("exit_date") for row in rows)]
    if not complete_dates:
        return {"status": "unavailable", "signals": 0, "triggered": 0, "untriggered": 0,
                "unresolved": 0, "items": []}
    signal_date = max(complete_dates)
    rows = groups[signal_date]
    cost = float(backtest.get("costs", {}).get("standard", 0.00435))
    items, net_returns, trade_dates = [], [], []
    for row in rows:
        triggered = bool(row.get("triggered"))
        gross = row.get("gross_return", row.get("return")) if triggered else None
        net = float(gross) - cost if gross is not None else None
        if net is not None:
            net_returns.append(net)
        trade_date = row.get("exit_date") or row.get("trade_date")
        if trade_date:
            trade_dates.append(trade_date)
        items.append({
            "code": row.get("code"), "name": row.get("name"),
            "direction": row.get("direction", row.get("signal_direction")),
            "status": "triggered" if triggered else "untriggered",
            "exit_reason": row.get("exit_reason") if triggered else None,
            "net_return_standard": net,
        })
    gains = sum(value for value in net_returns if value > 0)
    losses = -sum(value for value in net_returns if value < 0)
    profit_factor = gains / losses if losses else (None if gains else 0.0)
    return {
        "status": "complete", "signal_date": signal_date,
        "trade_date": max(trade_dates) if trade_dates else None,
        "signals": len(rows), "triggered": len(net_returns),
        "untriggered": len(rows) - len(net_returns), "unresolved": 0,
        "standard": {
            "cost_rate": cost,
            "average_return": sum(net_returns) / len(net_returns) if net_returns else None,
            "profit_factor": profit_factor,
        },
        "items": items,
    }


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
    prior_campaign = previous_campaign_state(DATA / "daily_status.json")
    status = {"started_at": started, "completed_at": None, "status": "running", "data_date": None}
    if prior_campaign:
        status["strategy_campaign"] = prior_campaign
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
        campaign_config = (json.loads(CAMPAIGN_CONFIG.read_text(encoding="utf-8"))
                           if CAMPAIGN_CONFIG.exists() else {})
        proposed_registry = build_registry_update(
            existing_registry, strategy, expected,
            campaign_id=campaign_id_for_date(campaign_config, expected))
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
            "strategy_campaign": load_campaign_status(
                backtest,
                latest["data_date"],
                pending_candidate=proposed_registry.get("pending_candidate"),
                previous_campaign=prior_campaign,
            ),
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
            "today_result": build_today_result(backtest, proposed_registry["active_gate"]),
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
