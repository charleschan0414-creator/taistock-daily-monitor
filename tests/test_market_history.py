import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from taistock.market_history import capture_market_history


TAIPEI = timezone(timedelta(hours=8))


def quote(fetched_at="2026-10-17T16:10:00+08:00", bias="偏多"):
    items = []
    for index, symbol in enumerate(("TX", "ZEF", "CDF", "UNF", "SXF"), 1):
        items.append({
            "symbol": symbol,
            "price": 20000.0 + index,
            "updown": float(index),
            "change_pct": index / 10,
            "volume": 100 * index,
            "contract": symbol + "202610",
            "contract_name": symbol,
        })
    return {
        "status": "ok",
        "fetched_at": fetched_at,
        "items": items,
        "scenario": {"bias": bias, "positive": 3, "negative": 0, "basis": "test"},
    }


class FakeClient:
    def __init__(self, result):
        self.result = result

    def fetch(self):
        return self.result


class MarketHistoryTests(unittest.TestCase):
    def test_capture_writes_snapshot_and_insufficient_regime_without_advice(self):
        now = datetime(2026, 10, 17, 16, 10, tzinfo=TAIPEI)
        with tempfile.TemporaryDirectory() as tmp:
            result = capture_market_history(Path(tmp), FakeClient(quote()), clock=lambda: now)
            rows = [json.loads(line) for line in (Path(tmp) / "global_market_history.jsonl").read_text(encoding="utf-8").splitlines()]
            regime = json.loads((Path(tmp) / "market_regime.json").read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(rows[0]["snapshot_id"], "2026-10-17:post_close")
        self.assertEqual(rows[0]["market_date"], "2026-10-17")
        self.assertEqual(rows[0]["phase"], "post_close")
        self.assertEqual(set(rows[0]["products"]), {"TX", "ZEF", "CDF", "UNF", "SXF"})
        self.assertEqual(set(rows[0]["products"]["TX"]), {"price", "updown", "change_pct", "volume"})
        self.assertEqual(regime["snapshot_count"], 1)
        self.assertEqual(regime["first"], "2026-10-17T16:10:00+08:00")
        self.assertEqual(regime["last"], "2026-10-17T16:10:00+08:00")
        self.assertEqual(regime["first_snapshot"]["snapshot_id"], "2026-10-17:post_close")
        self.assertEqual(regime["last_snapshot"]["snapshot_id"], "2026-10-17:post_close")
        self.assertTrue(regime["insufficient_history"])
        self.assertNotIn("strategy_recommendation", regime)
        self.assertFalse(regime["affects_stock_scores"])
        self.assertEqual(regime["bias_counts"], {"偏多": 1, "中性": 0, "偏空": 0})

    def test_same_market_date_and_phase_replaces_instead_of_duplicating(self):
        now = datetime(2026, 10, 17, 16, 10, tzinfo=TAIPEI)
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            capture_market_history(data, FakeClient(quote()), clock=lambda: now)
            changed = quote(fetched_at="2026-10-17T16:20:00+08:00", bias="偏空")
            capture_market_history(data, FakeClient(changed), clock=lambda: now)
            rows = [json.loads(line) for line in (data / "global_market_history.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["fetched_at"], "2026-10-17T16:20:00+08:00")
        self.assertEqual(rows[0]["scenario"]["bias"], "偏空")

    def test_morning_backup_phase_is_derived_from_injected_taipei_clock(self):
        now = datetime(2026, 10, 19, 7, 40, tzinfo=TAIPEI)
        with tempfile.TemporaryDirectory() as tmp:
            capture_market_history(Path(tmp), FakeClient(quote()), clock=lambda: now)
            row = json.loads((Path(tmp) / "global_market_history.jsonl").read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["market_date"], "2026-10-19")
        self.assertEqual(row["phase"], "morning_backup")

    def test_retains_only_latest_250_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            path = data / "global_market_history.jsonl"
            old = []
            for day in range(250):
                old.append({
                    "snapshot_id": f"old-{day}", "fetched_at": f"2025-01-01T00:00:{day % 60:02d}+08:00",
                    "market_date": f"2025-{day // 28 + 1:02d}-{day % 28 + 1:02d}", "phase": "post_close",
                    "scenario": {"bias": "中性"}, "products": {},
                })
            path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in old), encoding="utf-8")
            now = datetime(2026, 10, 17, 16, 10, tzinfo=TAIPEI)
            capture_market_history(data, FakeClient(quote()), clock=lambda: now)
            rows = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(rows), 250)
        self.assertNotIn('"snapshot_id": "old-0"', rows[0])

    def test_corrupt_jsonl_raises_without_overwriting_history_or_regime(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            history = data / "global_market_history.jsonl"
            regime = data / "market_regime.json"
            history.write_text('{"valid": true}\nnot-json\n', encoding="utf-8")
            regime.write_text('{"old": true}', encoding="utf-8")
            before_history = history.read_bytes()
            before_regime = regime.read_bytes()
            now = datetime(2026, 10, 17, 16, 10, tzinfo=TAIPEI)
            with self.assertRaisesRegex(ValueError, "corrupt"):
                capture_market_history(data, FakeClient(quote()), clock=lambda: now)
            self.assertEqual(history.read_bytes(), before_history)
            self.assertEqual(regime.read_bytes(), before_regime)

    def test_fetch_failure_returns_old_regime_and_does_not_change_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            old = {"snapshot_count": 7, "insufficient_history": True, "affects_stock_scores": False}
            (data / "market_regime.json").write_text(json.dumps(old), encoding="utf-8")
            result = capture_market_history(
                data,
                FakeClient({"status": "error", "message": "offline", "items": [], "fetched_at": "x"}),
                clock=lambda: datetime(2026, 10, 17, 16, 10, tzinfo=TAIPEI),
            )
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "offline")
        self.assertEqual(result["regime"], old)

    def test_first_fetch_failure_creates_publishable_empty_regime(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            result = capture_market_history(
                data,
                FakeClient({"status": "error", "message": "offline", "items": [], "fetched_at": "x"}),
                clock=lambda: datetime(2026, 10, 17, 16, 10, tzinfo=TAIPEI),
            )
            regime = json.loads((data / "market_regime.json").read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "error")
        self.assertEqual(regime["snapshot_count"], 0)
        self.assertTrue(regime["insufficient_history"])
        self.assertFalse(regime["affects_stock_scores"])


if __name__ == "__main__":
    unittest.main()
