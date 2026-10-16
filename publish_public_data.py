#!/usr/bin/env python
"""Publish a fixed, safe subset of generated data for GitHub Raw."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
PUBLIC = ROOT / "public_data"
JSON_COPIES = ("latest.json", "optimizer.json", "strategy_optimization.json", "daily_status.json")
CSV_COPIES = ("latest.csv", "backtest_100d.csv")
BACKTEST_KEYS = ("period", "signal_dates", "metrics", "walk_forward", "optimization", "cost_rate",
                 "cost_assumption", "completed_at", "data_collection")


def atomic_bytes(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def strict_json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def publish(data_dir=DATA, public_dir=PUBLIC):
    data_dir, public_dir = Path(data_dir), Path(public_dir)
    for name in JSON_COPIES:
        atomic_bytes(public_dir / name, strict_json_bytes(read_json(data_dir / name)))
    source = read_json(data_dir / "backtest_100d.json")
    summary = {key: source[key] for key in BACKTEST_KEYS if key in source}
    atomic_bytes(public_dir / "backtest_100d.json", strict_json_bytes(summary))
    for name in CSV_COPIES:
        atomic_bytes(public_dir / name, (data_dir / name).read_bytes())
    return sorted(path.name for path in public_dir.iterdir() if path.is_file())


def main():
    files = publish()
    print("Published: " + ", ".join(files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
