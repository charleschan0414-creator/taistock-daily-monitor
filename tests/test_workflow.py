import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "daily-optimize.yml"


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_daily_workflow_has_both_utc_schedules_and_safe_explicit_adds(self):
        self.assertIn("10 8 * * 1-5", self.text)
        self.assertIn("40 23 * * 0-4", self.text)
        self.assertIn("contents: write", self.text)
        self.assertIn("python daily_pipeline.py", self.text)
        self.assertIn("data/v2_optimization.json", self.text)
        self.assertIn("public_data/v2_optimization.json", self.text)
        self.assertIn("data/global_market_history.jsonl", self.text)
        self.assertIn("data/market_regime.json", self.text)
        self.assertIn("public_data/market_regime.json", self.text)
        for artifact in (
            "data/candidate_universe_100d.csv.gz",
            "data/portfolio_100d.json",
            "data/strategy_registry.json",
            "public_data/portfolio_100d.json",
            "public_data/strategy_registry.json",
        ):
            with self.subTest(artifact=artifact):
                self.assertIn(artifact, self.text)
        self.assertNotIn("git add .", self.text)
        self.assertNotIn("scanner.db", self.text)

    def test_daily_job_uses_pinned_runner_and_overlap_protection(self):
        self.assertIn("runs-on: ubuntu-24.04", self.text)
        self.assertNotIn("runs-on: ubuntu-latest", self.text)
        self.assertRegex(
            self.text,
            r"concurrency:\s+group: taistock-daily-monitor\s+cancel-in-progress: false",
        )
        self.assertIn("timeout-minutes: 45", self.text)

    def test_each_critical_step_has_a_timeout(self):
        critical_steps = (
            "Checkout repository",
            "Set up Python",
            "Run tests",
            "Compile Python",
            "Run daily pipeline",
            "Build public GitHub Raw artifacts",
            "Read verified data date",
            "Commit only approved generated artifacts",
        )
        for name in critical_steps:
            marker = f"- name: {name}"
            with self.subTest(step=name):
                self.assertIn(marker, self.text)
                start = self.text.index(marker)
                next_step = self.text.find("\n      - ", start + 1)
                block = self.text[start : next_step if next_step != -1 else None]
                self.assertRegex(block, r"(?m)^\s+timeout-minutes: \d+$")

    def test_failure_diagnostics_upload_only_daily_status(self):
        match = re.search(
            r"- name: Upload daily status diagnostics(?P<block>.*?)(?=\n      - |\Z)",
            self.text,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        block = match.group("block")
        self.assertIn("if: always()", block)
        self.assertIn("uses: actions/upload-artifact@v6", block)
        self.assertNotIn("uses: actions/upload-artifact@v5", block)
        self.assertIn("path: data/daily_status.json", block)
        self.assertRegex(block, r"retention-days: [1-7]\b")
        self.assertNotIn("cache", block.lower())
        self.assertNotIn("credential", block.lower())
        self.assertNotIn(".db", block.lower())

    def test_summary_always_reports_data_date_and_status(self):
        match = re.search(
            r"- name: Write run summary(?P<block>.*?)(?=\n      - |\Z)",
            self.text,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        block = match.group("block")
        self.assertIn("if: always()", block)
        self.assertIn("GITHUB_STEP_SUMMARY", block)
        self.assertIn("data_date", block)
        self.assertIn("status", block)
        self.assertNotRegex(block.lower(), r"secret|token|password|credential")


if __name__ == "__main__":
    unittest.main()
