import json
import tempfile
import unittest
from pathlib import Path

import publish_public_data


class PublishPublicDataTests(unittest.TestCase):
    def test_publish_copies_only_whitelist_and_summarizes_backtest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, public = root / "data", root / "public_data"
            data.mkdir()
            (data / "latest.json").write_text(json.dumps({"data_date": "2026-10-15"}), encoding="utf-8")
            (data / "latest.csv").write_text("code\n2330\n", encoding="utf-8")
            (data / "backtest_100d.json").write_text(json.dumps({"period": {}, "metrics": {}, "trades": [{"secret": 1}]}), encoding="utf-8")
            (data / "backtest_100d.csv").write_text("return\n1\n", encoding="utf-8")
            for name in ("optimizer.json", "strategy_optimization.json", "v2_optimization.json", "daily_status.json"):
                (data / name).write_text("{}", encoding="utf-8")
            (data / "scanner.db").write_bytes(b"PRIVATE")
            publish_public_data.publish(data, public)
            summary = json.loads((public / "backtest_100d.json").read_text(encoding="utf-8"))
            names = {path.name for path in public.iterdir()}
        self.assertNotIn("trades", summary)
        self.assertEqual(names, {"latest.json", "latest.csv", "backtest_100d.json", "backtest_100d.csv",
                                 "optimizer.json", "strategy_optimization.json", "v2_optimization.json",
                                 "daily_status.json"})
        self.assertNotIn("scanner.db", names)


if __name__ == "__main__":
    unittest.main()
