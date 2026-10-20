import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from taistock.notify import notify, build_summary


class NotifyTests(unittest.TestCase):
    def test_summary_lists_both_directions_and_learning_state(self):
        result = {"data_date":"2026-10-15",
                  "long_top10":[{"code":"1111","name":"甲"}],
                  "short_top10":[{"code":"2222","name":"乙"}],
                  "optimization":{"status":"學習中 8/30"}}
        text = build_summary(result)
        self.assertIn("偏多：1111甲", text)
        self.assertIn("偏空：2222乙", text)
        self.assertIn("學習中 8/30", text)
        self.assertIn("非投資建議", text)

    def test_summary_surfaces_broker_risk_warning(self):
        result = {"data_date": "2026-10-19", "optimization": {"status": "研究中"},
                  "long_top10": [{"code": "6269", "name": "台郡", "broker_risk_research": {
                      "warning": True, "risk_level": "high", "watched_buyers": [{"branch": "國泰-敦南"}],
                      "watched_sellers": []}}], "short_top10": []}
        text = build_summary(result)
        self.assertIn("分點風險：6269台郡", text)
        self.assertIn("國泰-敦南", text)
        self.assertIn("買超", text)

    @patch("taistock.notify.subprocess.run", side_effect=OSError("no powershell"))
    def test_writes_fallback_notification_when_toast_fails(self, _run):
        with tempfile.TemporaryDirectory() as directory:
            path = notify("測試", Path(directory))
            self.assertEqual(path.name, "notification.txt")
            self.assertIn("測試", path.read_text(encoding="utf-8"))


if __name__ == "__main__": unittest.main()
