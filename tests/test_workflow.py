import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowTests(unittest.TestCase):
    def test_daily_workflow_has_both_utc_schedules_and_safe_explicit_adds(self):
        text = (ROOT / ".github" / "workflows" / "daily-optimize.yml").read_text(encoding="utf-8")
        self.assertIn("10 8 * * 1-5", text)
        self.assertIn("40 23 * * 0-4", text)
        self.assertIn("contents: write", text)
        self.assertIn("python daily_pipeline.py", text)
        self.assertIn("data/v2_optimization.json", text)
        self.assertIn("public_data/v2_optimization.json", text)
        self.assertNotIn("git add .", text)
        self.assertNotIn("scanner.db", text)


if __name__ == "__main__":
    unittest.main()
