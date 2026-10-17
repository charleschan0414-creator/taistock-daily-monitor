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
            backtest = {"period": {}, "metrics": {}, "metrics_scenarios": {"gross": {}},
                        "costs": {"standard": .00435}, "portfolio_standard": {"net_return": .01},
                        "candidate_universe_summary": {"total_rows": 100},
                        "trades": [{"secret": 1}]}
            (data / "backtest_100d.json").write_text(json.dumps(backtest), encoding="utf-8")
            (data / "portfolio_100d.json").write_text(json.dumps({"final_capital": 101}), encoding="utf-8")
            (data / "backtest_100d.csv").write_text("return\n1\n", encoding="utf-8")
            for name in ("optimizer.json", "strategy_optimization.json", "strategy_registry.json",
                         "v2_optimization.json", "daily_status.json", "market_regime.json"):
                (data / name).write_text("{}", encoding="utf-8")
            (data / "global_market_history.jsonl").write_text('{"private_history": true}\n', encoding="utf-8")
            (data / "scanner.db").write_bytes(b"PRIVATE")
            publish_public_data.publish(data, public)
            summary = json.loads((public / "backtest_100d.json").read_text(encoding="utf-8"))
            names = {path.name for path in public.iterdir()}
        self.assertNotIn("trades", summary)
        self.assertEqual(summary["portfolio_standard"], {"net_return": .01})
        self.assertEqual(summary["costs"], {"standard": .00435})
        self.assertEqual(summary["metrics_scenarios"], {"gross": {}})
        self.assertEqual(summary["candidate_universe_summary"], {"total_rows": 100})
        self.assertEqual(names, {"latest.json", "latest.csv", "backtest_100d.json", "backtest_100d.csv",
                                 "portfolio_100d.json", "optimizer.json", "strategy_optimization.json", "v2_optimization.json",
                                 "strategy_registry.json", "daily_status.json", "market_regime.json"})
        self.assertNotIn("scanner.db", names)
        self.assertNotIn("global_market_history.jsonl", names)


if __name__ == "__main__":
    unittest.main()
