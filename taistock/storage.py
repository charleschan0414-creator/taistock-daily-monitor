import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .core import BASELINE_WEIGHTS

MIN_SAMPLES = 30
CANDIDATE_WEIGHTS = [
    BASELINE_WEIGHTS,
    {"volume_growth_pct": 0.45, "ma5_distance_pct": 0.25, "institutional_lots": 0.20, "volume_rank": 0.10},
    {"volume_growth_pct": 0.25, "ma5_distance_pct": 0.40, "institutional_lots": 0.25, "volume_rank": 0.10},
    {"volume_growth_pct": 0.25, "ma5_distance_pct": 0.20, "institutional_lots": 0.45, "volume_rank": 0.10},
]
CSV_FIELDS = ["date", "code", "name", "market", "signal_direction", "action_text", "close", "ma5",
              "entry_trigger", "stop_loss", "target1", "target2", "risk_pct", "score", "reason",
              "volume_growth_pct", "ma5_distance_pct", "institutional_lots", "volume_rank"]


def _connect(directory: Path):
    db = sqlite3.connect(directory / "scanner.db")
    db.row_factory = sqlite3.Row
    db.execute("CREATE TABLE IF NOT EXISTS runs (data_date TEXT PRIMARY KEY, updated_at TEXT, payload TEXT)")
    db.execute("""CREATE TABLE IF NOT EXISTS signals (
        signal_date TEXT NOT NULL, code TEXT NOT NULL, direction TEXT NOT NULL,
        prior_close REAL NOT NULL, next_close REAL, settled_date TEXT, directional_return REAL,
        score REAL, volume_growth_pct REAL, ma5_distance_pct REAL, institutional_abs_lots REAL,
        volume_rank INTEGER, payload TEXT NOT NULL, settlement_basis TEXT,
        PRIMARY KEY (signal_date, code, direction))""")
    columns = {row[1] for row in db.execute("PRAGMA table_info(signals)")}
    if "settlement_basis" not in columns:
        db.execute("ALTER TABLE signals ADD COLUMN settlement_basis TEXT")
    db.execute("""CREATE TABLE IF NOT EXISTS universe_prices (
        data_date TEXT NOT NULL, code TEXT NOT NULL, close REAL NOT NULL,
        payload TEXT NOT NULL, PRIMARY KEY (data_date, code))""")
    return db


def load_optimizer_state(directory: Path):
    weights = dict(BASELINE_WEIGHTS)
    version = 1
    audit_path = directory / "optimizer.json"
    audit = None
    if audit_path.exists():
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            version = int(audit.get("version", version))
        except (OSError, ValueError, TypeError):
            audit = None
    settled = 0
    db_path = directory / "scanner.db"
    if db_path.exists():
        try:
            db = sqlite3.connect(db_path)
            table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='signals'").fetchone()
            if table:
                settled = db.execute("SELECT COUNT(*) FROM signals WHERE directional_return IS NOT NULL").fetchone()[0]
            db.close()
        except sqlite3.Error:
            settled = 0
    backtest_samples = 0
    if audit and str(audit.get("source", "")).startswith("walk_forward_backtest"):
        backtest_samples = int(audit.get("sample_count", 0) or 0)
    status = "舊權重優化僅供研究｜未通過成本與前瞻驗證｜維持基準權重"
    return {"version": version, "status": status, "settled_count": settled,
            "backtest_sample_count": backtest_samples,
            "minimum_samples": MIN_SAMPLES, "weights": weights, "audit": audit,
            "approved": False, "cost_validated": False}


def _settle_prior_signals(db, data_date, universe):
    closes = {str(row.get("code")): float(row["close"]) for row in universe if row.get("code") and row.get("close") is not None}
    if not closes:
        return
    previous = db.execute("SELECT MAX(data_date) FROM runs WHERE data_date < ?", (data_date,)).fetchone()[0]
    if not previous:
        return
    pending = db.execute("SELECT signal_date,code,direction,prior_close FROM signals "
                         "WHERE directional_return IS NULL AND signal_date = ?", (previous,)).fetchall()
    for row in pending:
        if row["code"] not in closes or not row["prior_close"]:
            continue
        next_close = closes[row["code"]]
        raw_return = next_close / row["prior_close"] - 1
        directional = raw_return if row["direction"] == "long" else -raw_return
        db.execute("UPDATE signals SET next_close=?, settled_date=?, directional_return=?, "
                   "settlement_basis='next_successful_update_trading_day' "
                   "WHERE signal_date=? AND code=? AND direction=?",
                   (next_close, data_date, directional, row["signal_date"], row["code"], row["direction"]))


def _insert_current(db, payload):
    data_date = payload["data_date"]
    for row in payload.get("universe", []):
        if row.get("code") and row.get("close") is not None:
            db.execute("INSERT OR REPLACE INTO universe_prices VALUES (?,?,?,?)",
                       (data_date, str(row["code"]), float(row["close"]), json.dumps(row, ensure_ascii=False)))
    signals = payload.get("signals")
    if signals is None:
        signals = payload.get("qualified", [])
    for row in signals:
        direction = row.get("signal_direction", "long")
        close = float(row.get("close", 0) or 0)
        db.execute("""INSERT INTO signals
            (signal_date,code,direction,prior_close,next_close,settled_date,directional_return,score,
             volume_growth_pct,ma5_distance_pct,institutional_abs_lots,volume_rank,payload)
            VALUES (?,?,?,?,NULL,NULL,NULL,?,?,?,?,?,?)
            ON CONFLICT(signal_date,code,direction) DO UPDATE SET
              prior_close=excluded.prior_close, score=excluded.score,
              volume_growth_pct=excluded.volume_growth_pct,
              ma5_distance_pct=excluded.ma5_distance_pct,
              institutional_abs_lots=excluded.institutional_abs_lots,
              volume_rank=excluded.volume_rank, payload=excluded.payload""",
            (data_date, str(row.get("code", "")), direction, close, float(row.get("score", 0) or 0),
             float(row.get("volume_growth_pct", 0) or 0), float(row.get("ma5_distance_pct", 0) or 0),
             float(row.get("institutional_abs_lots", abs(row.get("institutional_lots", 0) or 0)) or 0),
             int(row.get("volume_rank", 999) or 999), json.dumps(row, ensure_ascii=False)))


def _metric(rows, weights):
    groups = {}
    for row in rows:
        groups.setdefault((row["signal_date"], row["direction"]), []).append(row)
    daily = []
    for group in groups.values():
        n = len(group)
        scored = []
        for row in group:
            def pct(key):
                return sum(1 for other in group if other[key] <= row[key]) / n * 100
            score = (weights["volume_growth_pct"] * pct("volume_growth_pct")
                     + weights["ma5_distance_pct"] * pct("ma5_distance_pct")
                     + weights["institutional_lots"] * pct("institutional_abs_lots")
                     + weights["volume_rank"] * max(0, 101 - row["volume_rank"]))
            scored.append((score, row["volume_rank"], row["code"], row["directional_return"]))
        top = sorted(scored, key=lambda item: (-item[0], item[1], item[2]))[:3]
        daily.append(sum(item[3] for item in top) / len(top))
    return sum(daily) / len(daily) if daily else 0.0


def _optimize(db, directory, old_weights, source="live_update"):
    settled_count = db.execute("SELECT COUNT(*) FROM signals WHERE directional_return IS NOT NULL").fetchone()[0]
    if settled_count < MIN_SAMPLES:
        state = load_optimizer_state(directory)
        backtest_samples = state.get("backtest_sample_count", 0)
        audit = state.get("audit") or {}
        status = f"舊權重研究累積 {settled_count} 筆｜不得啟用"
        return {"version": state.get("version", 1), "status": status,
                "settled_count": settled_count,
                "backtest_sample_count": backtest_samples,
                "minimum_samples": MIN_SAMPLES, "weights": old_weights,
                "approved": False, "cost_validated": False}
    dates = [row[0] for row in db.execute("SELECT DISTINCT signal_date FROM signals WHERE directional_return IS NOT NULL "
                                          "ORDER BY signal_date DESC LIMIT 60").fetchall()]
    placeholders = ",".join("?" for _ in dates)
    rows = db.execute(f"SELECT * FROM signals WHERE directional_return IS NOT NULL AND signal_date IN ({placeholders})", dates).fetchall()
    candidates = [dict(x) for x in CANDIDATE_WEIGHTS]
    if old_weights not in candidates:
        candidates.append(dict(old_weights))
    metrics = [(weights, _metric(rows, weights)) for weights in candidates]
    current_metric = _metric(rows, old_weights)
    best_weights, best_metric = max(metrics, key=lambda item: item[1])
    state = load_optimizer_state(directory)
    version = state.get("version", 1)
    research_changed = best_weights != old_weights and best_metric > current_metric
    selected_weights = best_weights if research_changed else old_weights
    selected_metric = best_metric if research_changed else current_metric
    audit = {"version": version, "source": source, "changed": False,
             "research_changed": research_changed, "approved": False,
             "cost_validated": False, "research_only": True,
             "sample_count": settled_count,
             "evaluation_period": {"from": min(dates), "to": max(dates), "trading_days": len(dates)},
             "old_weights": old_weights, "new_weights": old_weights,
             "research_weights": selected_weights,
             "performance": {"old_daily_top3_mean_directional_return": round(current_metric, 8),
                             "new_daily_top3_mean_directional_return": round(selected_metric, 8)},
             "updated_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    (directory / "optimizer.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"version": version, "status": f"舊權重研究完成（{settled_count} 筆／{len(dates)}日）｜不得啟用",
            "settled_count": settled_count, "minimum_samples": MIN_SAMPLES, "weights": old_weights,
            "evaluation_days": len(dates), "current_metric": round(selected_metric, 8),
            "approved": False, "cost_validated": False}


def save_result(payload: dict, directory: Path):
    """先用本次 universe 結算舊訊號，再保存本次訊號，最後評估供下次掃描使用的權重。"""
    directory.mkdir(parents=True, exist_ok=True)
    current_state = load_optimizer_state(directory)
    db = _connect(directory)
    try:
        _settle_prior_signals(db, payload["data_date"], payload.get("universe", []))
        _insert_current(db, payload)
        optimization = _optimize(db, directory, current_state["weights"])
        payload["optimization"] = optimization
        now = datetime.now().isoformat(timespec="seconds")
        db.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?)",
                   (payload["data_date"], now, json.dumps(payload, ensure_ascii=False)))
        db.commit()
    finally:
        db.close()

    (directory / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = payload.get("signals")
    if rows is None:
        rows = payload.get("qualified", [])
    with (directory / "latest.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return payload


def seed_backtest_history(signals: list[dict], directory: Path) -> dict:
    """冪等回填已知次一交易日收盤；不刪除既有即時掃描資料。"""
    directory.mkdir(parents=True, exist_ok=True)
    state = load_optimizer_state(directory)
    db = _connect(directory)
    try:
        for row in signals:
            prior = float(row["close"])
            following = float(row["next_close"])
            direction = row.get("signal_direction", "long")
            raw = following / prior - 1
            directional = raw if direction == "long" else -raw
            db.execute("""INSERT INTO signals
                (signal_date,code,direction,prior_close,next_close,settled_date,directional_return,score,
                 volume_growth_pct,ma5_distance_pct,institutional_abs_lots,volume_rank,payload)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(signal_date,code,direction) DO UPDATE SET
                  prior_close=excluded.prior_close,next_close=excluded.next_close,
                  settled_date=excluded.settled_date,directional_return=excluded.directional_return,
                  score=excluded.score,volume_growth_pct=excluded.volume_growth_pct,
                  ma5_distance_pct=excluded.ma5_distance_pct,
                  institutional_abs_lots=excluded.institutional_abs_lots,
                  volume_rank=excluded.volume_rank,payload=excluded.payload""",
                       (row["signal_date"], str(row["code"]), direction, prior, following,
                        row["next_date"], directional, float(row.get("score", 0) or 0),
                        float(row.get("volume_growth_pct", 0) or 0),
                        float(row.get("ma5_distance_pct", 0) or 0),
                        float(row.get("institutional_abs_lots", 0) or 0),
                        int(row.get("volume_rank", 999) or 999), json.dumps(row, ensure_ascii=False)))
        optimization = _optimize(db, directory, state["weights"], source="official_backtest")
        db.commit()
        return optimization
    finally:
        db.close()


def load_result(directory: Path):
    path = directory / "latest.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
