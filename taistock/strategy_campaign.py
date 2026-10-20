"""Bounded strategy-research campaign tracking.

This module records research progress only.  It never activates a strategy.
"""
from __future__ import annotations

from datetime import date


def _parse_day(value: str) -> date:
    return date.fromisoformat(value)


def _pf_above(current, baseline) -> bool:
    """Treat None PF as infinite (no losing trades), without claiming ties improve."""
    if current is None:
        return baseline is not None
    if baseline is None:
        return False
    return float(current) > float(baseline)


def update_unique_signal_dates(config: dict, previous_dates: list[str], signal_date: str) -> list[str]:
    """Add one in-campaign signal date; reruns and out-of-window dates are ignored."""
    start = _parse_day(config["start_date"])
    end = _parse_day(config["end_date"])
    current = _parse_day(signal_date)
    dates = set()
    for value in previous_dates:
        if not isinstance(value, str):
            continue
        try:
            previous = _parse_day(value)
        except ValueError:
            continue
        if start <= previous <= end:
            dates.add(previous.isoformat())
    if start <= current <= end:
        dates.add(signal_date)
    return sorted(dates)


def build_campaign_status(config: dict, as_of_date: str, current_standard: dict,
                          candidate_found: bool = False,
                          unique_signal_dates: list[str] | None = None,
                          candidate_proposed_on: str | None = None,
                          candidate_forward_signal_days: int = 0) -> dict:
    """Build an auditable snapshot for a fixed-date research campaign."""
    start = _parse_day(config["start_date"])
    end = _parse_day(config["end_date"])
    as_of = _parse_day(as_of_date)
    if end <= start:
        raise ValueError("campaign end_date must be after start_date")

    if as_of < start:
        status = "not_started"
    elif as_of >= end:
        status = "complete"
    else:
        status = "active"

    baseline = dict(config["baseline"])
    current = {
        "win_rate": current_standard.get("win_rate"),
        "profit_factor": current_standard.get("profit_factor"),
        "average_return": current_standard.get("average_return"),
    }
    win_rate_above = (
        current["win_rate"] is not None
        and baseline.get("win_rate") is not None
        and float(current["win_rate"]) > float(baseline["win_rate"])
    )
    pf_above = _pf_above(current["profit_factor"], baseline.get("profit_factor"))
    observed_dates = sorted(set(unique_signal_dates or []))
    minimum_days = int(config.get("minimum_new_unique_signal_days", 20))
    objective_met = win_rate_above and pf_above
    candidate_days = max(0, int(candidate_forward_signal_days))
    # Forward outcome approval is intentionally fail-closed.  The operational
    # day counter alone is not candidate-specific settled performance evidence.
    ready_for_review = False

    return {
        "id": config["id"],
        "status": status,
        "start_date": config["start_date"],
        "end_date": config["end_date"],
        "as_of_date": as_of_date,
        "objective": config["objective"],
        "baseline": baseline,
        "current": current,
        "metric_checks": {
            "win_rate_above_baseline": win_rate_above,
            "profit_factor_above_baseline": pf_above,
        },
        "objective_met": objective_met,
        "candidate_found": bool(candidate_found),
        "candidate_proposed_on": candidate_proposed_on,
        "candidate_forward_signal_days": candidate_days,
        "manual_review_recommended": bool(candidate_found),
        "forward_outcome_validated": False,
        "unique_signal_dates": observed_dates,
        "unique_signal_days": len(observed_dates),
        "minimum_unique_signal_days": minimum_days,
        "minimum_unique_signal_days_met": len(observed_dates) >= minimum_days,
        "ready_for_manual_review": ready_for_review,
        "eligible_for_activation": False,
        "auto_activation": False,
        "guaranteed_improvement": False,
        "execution_model": config.get("execution_model", "next_day_intraday_forced_close_v1"),
        "notice": (
            "自動研究只能尋找及驗證可能改善勝率與PF的候選；"
            "前瞻日數僅為觀察進度，不代表績效核准；"
            "不保證未來績效，候選不會自動啟用。"
        ),
    }
