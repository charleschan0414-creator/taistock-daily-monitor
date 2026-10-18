"""Build a resilient local health report and anonymous diagnostics export."""

from __future__ import annotations

import copy
import json
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path


TAIPEI = timezone(timedelta(hours=8))
DAILY_UPDATE_TIME = time(6, 30)
SOURCE_FILES = {
    "daily_status": "daily_status.json",
    "latest": "latest.json",
    "backtest_100d": "backtest_100d.json",
    "strategy_registry": "strategy_registry.json",
    "market_regime": "market_regime.json",
}


def _load_source(data_dir, key):
    path = Path(data_dir) / SOURCE_FILES[key]
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("top level is not an object")
    except FileNotFoundError:
        return None, {"status": "warning", "message": "檔案尚未建立", "date": None}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None, {"status": "warning", "message": "檔案無法讀取或格式損壞", "date": None}
    date_value = _source_date(key, value)
    return value, {"status": "ok", "message": "可正常讀取", "date": date_value}


def _source_date(key, value):
    if key == "daily_status":
        return value.get("data_date") or value.get("completed_at")
    if key == "latest":
        return value.get("data_date")
    if key == "backtest_100d":
        return value.get("period", {}).get("to") if isinstance(value.get("period"), dict) else None
    if key == "strategy_registry":
        return value.get("last_observed_date") or value.get("activated_on")
    return value.get("updated_at") or value.get("last_snapshot_at") or value.get("last") or value.get("market_date")


def _parse_moment(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _next_update(now):
    local = now.astimezone(TAIPEI)
    candidate = datetime.combine(local.date(), DAILY_UPDATE_TIME, TAIPEI)
    if local >= candidate:
        candidate += timedelta(days=1)
    return candidate.isoformat(timespec="seconds")


def _integer(value, default=0):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else default


def build_health_report(data_dir, app_version, now):
    """Read local JSON state without raising for absent or malformed inputs."""
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("now must be a timezone-aware datetime")
    documents = {}
    sources = {}
    for key in SOURCE_FILES:
        documents[key], sources[key] = _load_source(data_dir, key)

    daily = documents["daily_status"] or {}
    latest = documents["latest"] or {}
    registry = documents["strategy_registry"] or {}
    regime = documents["market_regime"] or {}

    pipeline_status = daily.get("status") or "unknown"
    completed_at = daily.get("completed_at")
    freshness = "unknown"
    completed = _parse_moment(completed_at)
    if pipeline_status == "success" and completed is not None:
        freshness = "stale" if now.astimezone(timezone.utc) - completed.astimezone(timezone.utc) > timedelta(hours=48) else "fresh"

    if pipeline_status == "error":
        sources["daily_status"]["status"] = "error"
        sources["daily_status"]["message"] = "最近一次每日流程失敗"

    statuses = {source["status"] for source in sources.values()}
    overall = "error" if pipeline_status == "error" or "error" in statuses else "warning" if "warning" in statuses or freshness == "stale" else "ok"

    forward = _integer(registry.get("forward_signal_days"))
    minimum = _integer(registry.get("minimum_forward_signal_days"))
    universe_rows = latest.get("universe") if isinstance(latest.get("universe"), list) else []
    signal_rows = latest.get("signals") if isinstance(latest.get("signals"), list) else []
    total = _integer(latest.get("top100_count"), len(universe_rows))
    if "long_count" in latest or "short_count" in latest:
        selected = _integer(latest.get("long_count")) + _integer(latest.get("short_count"))
    else:
        selected = len(signal_rows)
    snapshots = _integer(regime.get("snapshot_count"))

    return {
        "overall": overall,
        "generated_at": now.astimezone(TAIPEI).isoformat(timespec="seconds"),
        "sources": sources,
        "latest_data_date": latest.get("data_date") or daily.get("data_date"),
        "pipeline": {
            "status": pipeline_status,
            "completed_at": completed_at,
            "data_date": daily.get("data_date"),
            "freshness": freshness,
        },
        "strategy": {
            "version": registry.get("strategy_version"),
            "activated_on": registry.get("activated_on"),
            "forward_signal_days": forward,
            "minimum_forward_signal_days": minimum,
            "progress": f"{forward}/{minimum}",
            "status": registry.get("status") or "unknown",
        },
        "universe": {"total": total, "selected": selected},
        "night_market": {"count": snapshots, "threshold": 60, "remaining": max(0, 60 - snapshots)},
        "desktop_version": str(app_version),
        "next_update": _next_update(now),
        "recent_error": daily.get("error") if pipeline_status == "error" else None,
    }


def _safe_summaries(documents, sources):
    whitelists = {
        "daily_status": ("status", "started_at", "completed_at", "data_date"),
        "latest": ("data_date", "top100_count", "history_success_count", "long_count", "short_count"),
        "backtest_100d": ("period", "completed_at"),
        "strategy_registry": ("strategy_version", "activated_on", "minimum_forward_signal_days", "forward_signal_days", "last_observed_date", "status"),
        "market_regime": ("snapshot_count", "insufficient_history", "affects_stock_scores", "updated_at", "last_snapshot_at"),
    }
    summaries = {}
    for key in SOURCE_FILES:
        document = documents[key]
        summary = {"status": sources[key]["status"], "kind": "object" if document is not None else None}
        if document is not None:
            summary["field_count"] = len(document)
            summary["safe_fields"] = {name: copy.deepcopy(document[name]) for name in whitelists[key] if name in document}
        summaries[key] = summary
    return summaries


def export_diagnostics(data_dir, app_version, destination, now):
    """Atomically export health and explicitly allowlisted structural summaries."""
    data_dir = Path(data_dir)
    health = build_health_report(data_dir, app_version, now)
    documents, sources = {}, {}
    for key in SOURCE_FILES:
        documents[key], sources[key] = _load_source(data_dir, key)
    anonymous_health = copy.deepcopy(health)
    if anonymous_health.get("recent_error"):
        anonymous_health["recent_error"] = "已記錄每日流程錯誤；詳細內容僅留在本機"
    payload = {"health": anonymous_health, "json_summaries": _safe_summaries(documents, sources)}
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
