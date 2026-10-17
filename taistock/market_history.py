"""Persistent TAIFEX snapshots and score-isolated market-regime summaries."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

SYMBOLS = ("TX", "ZEF", "CDF", "UNF", "SXF")
MAX_SNAPSHOTS = 250
MINIMUM_HISTORY = 60
TAIPEI = timezone(timedelta(hours=8))


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_history(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError(f"corrupt market history JSONL at line {line_number}: {exc}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"corrupt market history JSONL at line {line_number}: object required")
        records.append(record)
    return records


def _read_old_regime(path: Path) -> dict | None:
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("market regime must be a JSON object")
    return value


def _identity(record: dict) -> dict:
    return {
        "snapshot_id": record.get("snapshot_id"),
        "fetched_at": record.get("fetched_at"),
        "market_date": record.get("market_date"),
        "phase": record.get("phase"),
    }


def summarize(records: list[dict]) -> dict:
    counts = {"偏多": 0, "中性": 0, "偏空": 0}
    for record in records:
        bias = record.get("scenario", {}).get("bias")
        if bias in counts:
            counts[bias] += 1
    first = records[0] if records else None
    last = records[-1] if records else None
    return {
        "snapshot_count": len(records),
        "first": first.get("fetched_at") if first else None,
        "last": last.get("fetched_at") if last else None,
        "first_snapshot": _identity(first) if first else None,
        "last_snapshot": _identity(last) if last else None,
        "bias_counts": counts,
        "latest": last,
        "minimum_history": MINIMUM_HISTORY,
        "insufficient_history": len(records) < MINIMUM_HISTORY,
        "affects_stock_scores": False,
    }


def _phase(now: datetime) -> str:
    local = now.astimezone(TAIPEI)
    return "post_close" if local.hour >= 16 else "morning_backup"


def _snapshot(payload: dict, now: datetime) -> dict:
    local = now.astimezone(TAIPEI)
    phase = _phase(local)
    by_symbol = {item.get("symbol"): item for item in payload.get("items", [])}
    missing = [symbol for symbol in SYMBOLS if symbol not in by_symbol]
    if missing:
        raise ValueError("TAIFEX snapshot missing products: " + ", ".join(missing))
    products = {}
    for symbol in SYMBOLS:
        item = by_symbol[symbol]
        products[symbol] = {
            "price": item.get("price"),
            "updown": item.get("updown"),
            "change_pct": item.get("change_pct"),
            "volume": item.get("volume"),
        }
    market_date = local.date().isoformat()
    return {
        "snapshot_id": f"{market_date}:{phase}",
        "fetched_at": payload.get("fetched_at"),
        "market_date": market_date,
        "phase": phase,
        "scenario": payload.get("scenario"),
        "products": products,
    }


def capture_market_history(data_dir, client, clock=None) -> dict:
    """Fetch and persist one idempotent snapshot; fetch errors preserve old files."""
    data_dir = Path(data_dir)
    history_path = data_dir / "global_market_history.jsonl"
    regime_path = data_dir / "market_regime.json"
    now = (clock or (lambda: datetime.now(TAIPEI)))()
    payload = client.fetch()
    if payload.get("status") != "ok":
        regime = _read_old_regime(regime_path)
        if regime is None:
            regime = summarize(_read_history(history_path))
            _atomic_write(
                regime_path,
                json.dumps(regime, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            )
        return {
            "status": "error",
            "error": str(payload.get("message") or "TAIFEX fetch failed"),
            "regime": regime,
            "affects_stock_scores": False,
        }

    records = _read_history(history_path)
    snapshot = _snapshot(payload, now)
    key = (snapshot["market_date"], snapshot["phase"])
    matching = [index for index, record in enumerate(records)
                if (record.get("market_date"), record.get("phase")) == key]
    if len(matching) > 1:
        raise ValueError("corrupt market history JSONL: duplicate market_date and phase")
    if matching:
        records[matching[0]] = snapshot
    else:
        records.append(snapshot)
    records = records[-MAX_SNAPSHOTS:]
    regime = summarize(records)
    history_text = "".join(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n" for record in records)
    regime_text = json.dumps(regime, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    _atomic_write(history_path, history_text)
    _atomic_write(regime_path, regime_text)
    return {
        "status": "ok",
        "snapshot_id": snapshot["snapshot_id"],
        "regime": regime,
        "affects_stock_scores": False,
    }
