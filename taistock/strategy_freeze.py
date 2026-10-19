"""Persist a live strategy gate while measuring new forward signal dates.

The forward counter is an operational observation window after activation.  It is
not another evaluation of, or reuse of, the optimizer's 30-day holdout.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

MINIMUM_FORWARD_SIGNAL_DAYS = 20
EXECUTION_MODEL = "next_day_intraday_forced_close_v1"
_REQUIRED_FIELDS = {
    "active_gate",
    "strategy_version",
    "activated_on",
    "minimum_forward_signal_days",
    "forward_signal_days",
    "last_observed_date",
    "status",
}


def _approved_candidate(report: dict) -> dict | None:
    decision = report.get("decision", {})
    if not (decision.get("upgrade_approved") and decision.get("cost_validated")):
        return None
    gate = decision.get("applied_gate")
    return copy.deepcopy(gate) if isinstance(gate, dict) and gate else None


def _initial_gate(report: dict) -> dict:
    decision = report.get("decision", {})
    gate = decision.get("applied_gate")
    if not isinstance(gate, dict) or not gate:
        raise ValueError("optimization report has no usable applied_gate")
    return copy.deepcopy(gate)


def _new_registry_state(gate: dict, version: int, data_date: str,
                        cost_validated: bool) -> dict:
    return {
        "active_gate": copy.deepcopy(gate),
        "strategy_version": version,
        "execution_model": EXECUTION_MODEL,
        "cost_validated": cost_validated,
        "activated_on": data_date,
        "minimum_forward_signal_days": MINIMUM_FORWARD_SIGNAL_DAYS,
        "forward_signal_days": 0,
        "last_observed_date": data_date,
        "status": "forward_observation",
        "forward_observation": {
            "not_holdout_reuse": True,
            "description": "Counts new unique live signal dates after activation regardless of outcome.",
        },
    }


def _validate(registry: dict) -> dict:
    if not isinstance(registry, dict) or not _REQUIRED_FIELDS.issubset(registry):
        raise ValueError("strategy registry is missing required fields")
    if not isinstance(registry["active_gate"], dict) or not registry["active_gate"]:
        raise ValueError("strategy registry active_gate is invalid")
    if registry["minimum_forward_signal_days"] != MINIMUM_FORWARD_SIGNAL_DAYS:
        raise ValueError("strategy registry minimum forward window is invalid")
    if not isinstance(registry["forward_signal_days"], int) or registry["forward_signal_days"] < 0:
        raise ValueError("strategy registry forward_signal_days is invalid")
    return registry


def load_registry(path: Path) -> dict | None:
    """Load a valid registry; absence is the only migration-to-new state."""
    path = Path(path)
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return _validate(value)


def write_registry(path: Path, registry: dict) -> None:
    """Atomically replace the registry after complete JSON serialization."""
    path = Path(path)
    _validate(registry)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(registry, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def build_registry_update(existing: dict | None, optimization_report: dict,
                          data_date: str) -> dict:
    """Return the registry state for one successful unique signal date."""
    if not isinstance(data_date, str) or not data_date:
        raise ValueError("data_date is required for strategy forward observation")
    if existing is None:
        gate = _initial_gate(optimization_report)
        return _new_registry_state(
            gate, 1, data_date,
            bool(optimization_report.get("decision", {}).get("cost_validated")),
        )

    current = copy.deepcopy(_validate(existing))
    if current.get("execution_model") != EXECUTION_MODEL:
        return _new_registry_state(
            current["active_gate"], int(current["strategy_version"]) + 1,
            data_date, False,
        )
    last_date = current["last_observed_date"]
    is_new_date = data_date > last_date
    if is_new_date:
        current["forward_signal_days"] += 1
        current["last_observed_date"] = data_date

    candidate = _approved_candidate(optimization_report)
    differs = candidate is not None and candidate != current["active_gate"]
    mature = current["forward_signal_days"] >= current["minimum_forward_signal_days"]
    if differs:
        pending = current.get("pending_candidate")
        if not isinstance(pending, dict) or pending.get("gate") != candidate:
            pending = {
                "gate": candidate,
                "proposed_on": data_date,
                "last_observed_date": data_date,
                "forward_signal_days": 0,
                "minimum_forward_signal_days": current["minimum_forward_signal_days"],
                "auto_promotion_allowed": False,
                "requires_outcome_validation": True,
            }
        elif is_new_date and data_date > pending.get("last_observed_date", ""):
            pending["forward_signal_days"] = int(pending.get("forward_signal_days", 0)) + 1
            pending["last_observed_date"] = data_date
        current["pending_candidate"] = pending
        current["status"] = "pending_candidate"
    elif mature:
        current.pop("pending_candidate", None)
        current["status"] = "eligible_for_new_version"
    else:
        current.pop("pending_candidate", None)
        current["status"] = "forward_observation"
    return current


def apply_active_gate_to_report(optimization_report: dict, registry: dict) -> dict:
    """Create the report consumed by app.update with the frozen active gate."""
    active = _validate(registry)
    report = copy.deepcopy(optimization_report)
    original_decision = copy.deepcopy(report.get("decision", {}))
    report["optimization_decision"] = original_decision
    decision = report.setdefault("decision", {})
    cost_validated = bool(active.get("cost_validated", True))
    decision.update({
        "enabled": True,
        "upgrade_approved": cost_validated,
        "cost_validated": cost_validated,
        "applied_mode": "candidate" if cost_validated else "incumbent_protective",
        "applied_gate": copy.deepcopy(active["active_gate"]),
        "status": ((f"策略版本 v{active['strategy_version']} 凍結中｜"
                    f"前瞻新訊號日 {active['forward_signal_days']}/"
                    f"{active['minimum_forward_signal_days']}") if cost_validated else
                   (f"策略版本 v{active['strategy_version']} 保護性觀察｜"
                    "未經新當沖模式成本驗證")),
    })
    report["strategy_freeze"] = {
        "strategy_version": active["strategy_version"],
        "execution_model": active.get("execution_model", "legacy_unknown"),
        "cost_validated": cost_validated,
        "activated_on": active["activated_on"],
        "forward_signal_days": active["forward_signal_days"],
        "minimum_forward_signal_days": active["minimum_forward_signal_days"],
        "status": active["status"],
        "not_holdout_reuse": True,
    }
    return report
