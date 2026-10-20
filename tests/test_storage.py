import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import os
import taistock.storage as storage
from taistock.storage import save_result, load_result, load_optimizer_state, seed_backtest_history
from taistock.core import BASELINE_WEIGHTS


class StorageTests(unittest.TestCase):
    def test_output_replace_failure_rolls_back_database_and_both_latest_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            save_result({"data_date": "2026-10-19", "signals": [], "universe": []}, path)
            before_json = (path / "latest.json").read_bytes()
            before_csv = (path / "latest.csv").read_bytes()
            db = sqlite3.connect(path / "scanner.db")
            before_runs = db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            db.close()
            real_replace = os.replace
            calls = 0

            def fail_second_replace(source, destination):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected replace failure")
                return real_replace(source, destination)

            with patch.object(storage.os, "replace", side_effect=fail_second_replace):
                with self.assertRaisesRegex(OSError, "injected replace failure"):
                    save_result({"data_date": "2026-10-20", "signals": [], "universe": []}, path)
            self.assertEqual((path / "latest.json").read_bytes(), before_json)
            self.assertEqual((path / "latest.csv").read_bytes(), before_csv)
            db = sqlite3.connect(path / "scanner.db")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM runs").fetchone()[0], before_runs)
            db.close()

    def test_persistent_csv_replace_failure_restores_json_and_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            save_result({"data_date": "2026-10-19", "signals": [], "universe": []}, path)
            before_json = (path / "latest.json").read_bytes()
            before_csv = (path / "latest.csv").read_bytes()
            real_replace = os.replace

            def fail_csv_destination(source, destination):
                if Path(destination).name == "latest.csv":
                    raise OSError("persistent csv failure")
                return real_replace(source, destination)

            with patch.object(storage.os, "replace", side_effect=fail_csv_destination):
                with self.assertRaisesRegex(OSError, "persistent csv failure"):
                    save_result({"data_date": "2026-10-20", "signals": [], "universe": []}, path)
            self.assertEqual((path / "latest.json").read_bytes(), before_json)
            self.assertEqual((path / "latest.csv").read_bytes(), before_csv)
            db = sqlite3.connect(path / "scanner.db")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
            db.close()

    def test_successful_result_is_available_as_json_csv_and_sqlite(self):
        payload = {"data_date":"2026-10-15","top100_count":100,"qualified":[{"code":"2330","name":"台積電","score":88.5}]}
        with tempfile.TemporaryDirectory() as directory:
            save_result(payload, Path(directory))
            self.assertEqual(load_result(Path(directory))["qualified"][0]["code"], "2330")
            self.assertTrue((Path(directory) / "latest.csv").exists())
            self.assertTrue((Path(directory) / "scanner.db").exists())

    def test_settles_only_prior_day_signal_and_exports_signal_fields(self):
        signal = {"code":"2330","name":"台積電","market":"上市","date":"2026-10-14","close":100.0,
                  "signal_direction":"long","action_text":"規則式研究觀察；非投資建議","entry_trigger":101.0,
                  "stop_loss":98.0,"target1":105.5,"target2":107.0,"risk_pct":3.0,"score":80.0,
                  "reason":"測試","volume_growth_pct":30.0,"ma5_distance_pct":2.0,
                  "institutional_abs_lots":100.0,"volume_rank":1}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            day1 = {"data_date":"2026-10-14","signals":[signal],"qualified":[signal],
                    "universe":[{"code":"2330","close":100.0}],"optimization":{}}
            saved1 = save_result(day1, path)
            self.assertIn("研究累積 0 筆", saved1["optimization"]["status"])
            self.assertFalse(saved1["optimization"]["approved"])
            db = sqlite3.connect(path / "scanner.db")
            self.assertIsNone(db.execute("SELECT directional_return FROM signals").fetchone()[0])
            db.close()
            day2 = {"data_date":"2026-10-15","signals":[],"qualified":[],
                    "universe":[{"code":"2330","close":110.0}],"optimization":{}}
            saved2 = save_result(day2, path)
            self.assertIn("研究累積 1 筆", saved2["optimization"]["status"])
            db = sqlite3.connect(path / "scanner.db")
            settled = db.execute("SELECT settled_date, directional_return FROM signals").fetchone()
            db.close()
            self.assertEqual(settled[0], "2026-10-15")
            self.assertAlmostEqual(settled[1], 0.10)
            header = (path / "latest.csv").read_text(encoding="utf-8-sig").splitlines()[0]
            self.assertIn("signal_direction", header)
            self.assertIn("entry_trigger", header)

    def test_skipped_signal_is_not_mislabelled_as_next_successful_update(self):
        signal = {"code":"2330", "close":100, "signal_direction":"long"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            save_result({"data_date":"2026-01-01", "signals":[signal], "universe":[]}, path)
            save_result({"data_date":"2026-01-02", "signals":[], "universe":[]}, path)
            save_result({"data_date":"2026-01-05", "signals":[],
                         "universe":[{"code":"2330", "close":110}]}, path)
            db = sqlite3.connect(path / "scanner.db")
            row = db.execute("SELECT directional_return,settlement_basis FROM signals").fetchone()
            db.close()
            self.assertIsNone(row[0])
            self.assertIsNone(row[1])

    def test_migrates_existing_runs_database_without_deleting_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory); path.mkdir(exist_ok=True)
            db = sqlite3.connect(path / "scanner.db")
            db.execute("CREATE TABLE runs (data_date TEXT PRIMARY KEY, updated_at TEXT, payload TEXT)")
            db.execute("INSERT INTO runs VALUES ('2026-10-01','old','{}')"); db.commit(); db.close()
            save_result({"data_date":"2026-10-15","signals":[],"qualified":[],"universe":[]}, path)
            db = sqlite3.connect(path / "scanner.db")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 2)
            self.assertTrue(db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='signals'").fetchone())
            db.close()
            state = load_optimizer_state(path)
            self.assertEqual(state["weights"]["volume_growth_pct"], 0.35)

    def test_legacy_optimizer_is_research_only_and_cannot_change_active_weights(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            save_result({"data_date":"2026-01-01","signals":[],"qualified":[],"universe":[]}, path)
            db = sqlite3.connect(path / "scanner.db")
            for day in range(1, 11):
                date = f"2026-02-{day:02d}"
                features = [("A",100,1,1,1,0.10),("B",1,100,100,2,-0.10),
                            ("C",2,90,90,3,-0.10),("D",3,80,80,4,-0.10)]
                for code, volume, ma, inst, rank, outcome in features:
                    db.execute("""INSERT INTO signals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)""",
                               (date, code, "long", 100, 100 * (1 + outcome), "2026-03-01", outcome,
                                0, volume, ma, inst, rank, "{}"))
            db.commit(); db.close()
            saved = save_result({"data_date":"2026-03-02","signals":[],"qualified":[],"universe":[]}, path)
            self.assertEqual(saved["optimization"]["settled_count"], 40)
            self.assertEqual(saved["optimization"]["weights"], BASELINE_WEIGHTS)
            self.assertFalse(saved["optimization"]["approved"])
            self.assertIn("研究", saved["optimization"]["status"])
            audit = json.loads((path / "optimizer.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["sample_count"], 40)
            self.assertIn("evaluation_period", audit)
            self.assertIn("old_weights", audit)
            self.assertIn("new_weights", audit)
            self.assertGreater(audit["performance"]["new_daily_top3_mean_directional_return"],
                               audit["performance"]["old_daily_top3_mean_directional_return"])

    def test_repeated_current_save_does_not_erase_existing_settlement(self):
        signal = {"code": "2330", "close": 100, "signal_direction": "long", "score": 1,
                  "volume_growth_pct": 20, "ma5_distance_pct": 1,
                  "institutional_abs_lots": 1, "volume_rank": 1}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            payload = {"data_date": "2026-10-15", "signals": [signal], "universe": []}
            save_result(payload, path)
            db = sqlite3.connect(path / "scanner.db")
            db.execute("UPDATE signals SET next_close=110,settled_date='2026-10-16',directional_return=.1")
            db.commit(); db.close()
            save_result(payload, path)
            db = sqlite3.connect(path / "scanner.db")
            value = db.execute("SELECT directional_return FROM signals").fetchone()[0]
            db.close()
            self.assertAlmostEqual(value, .1)

    def test_unapproved_backtest_optimizer_cannot_be_loaded_as_active(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            audit = {
                "source": "walk_forward_backtest_100d",
                "sample_count": 1455,
                "changed": True,
                "new_weights": {"volume_growth_pct": 0.25, "ma5_distance_pct": 0.20,
                                "institutional_lots": 0.45, "volume_rank": 0.10},
            }
            (path / "optimizer.json").write_text(json.dumps(audit), encoding="utf-8")
            state = load_optimizer_state(path)
            self.assertEqual(state["settled_count"], 0)
            self.assertEqual(state["backtest_sample_count"], 1455)
            self.assertEqual(state["weights"], BASELINE_WEIGHTS)
            self.assertFalse(state["approved"])
            self.assertIn("未通過成本與前瞻驗證", state["status"])
            saved = save_result({"data_date": "2026-10-15", "signals": [], "universe": []}, path)
            self.assertEqual(saved["optimization"]["weights"], BASELINE_WEIGHTS)
            self.assertIn("研究", saved["optimization"]["status"])

    def test_backtest_seed_is_idempotent_settled_and_produces_optimizer_audit(self):
        signals = []
        for index in range(40):
            direction = "long" if index % 2 == 0 else "short"
            signals.append({"signal_date": f"2026-08-{index // 2 + 1:02d}", "next_date": "2026-09-01",
                            "code": f"{1000 + index}", "name": "測試", "signal_direction": direction,
                            "close": 100, "next_close": 101 if direction == "long" else 99,
                            "score": index, "volume_growth_pct": 20 + index,
                            "ma5_distance_pct": 1, "institutional_abs_lots": index,
                            "volume_rank": index + 1})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            first = seed_backtest_history(signals, path)
            second = seed_backtest_history(signals, path)
            self.assertEqual(first["settled_count"], 40)
            self.assertEqual(second["settled_count"], 40)
            db = sqlite3.connect(path / "scanner.db")
            count, minimum = db.execute("SELECT COUNT(*), MIN(directional_return) FROM signals").fetchone()
            db.close()
            self.assertEqual(count, 40)
            self.assertAlmostEqual(minimum, .01)
            audit = json.loads((path / "optimizer.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["source"], "official_backtest")
            self.assertEqual(audit["sample_count"], 40)


if __name__ == "__main__": unittest.main()
