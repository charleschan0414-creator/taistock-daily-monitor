import unittest

from taistock.strategy_campaign import build_campaign_status, update_unique_signal_dates


CONFIG = {
    "id": "one_month_winrate_pf_20261020",
    "start_date": "2026-10-20",
    "end_date": "2026-11-20",
    "objective": "improve_standard_win_rate_and_profit_factor",
    "baseline": {
        "data_date": "2026-10-19",
        "win_rate": 0.357,
        "profit_factor": 0.492,
        "average_return": -0.0103,
    },
}


class StrategyCampaignTests(unittest.TestCase):
    def test_signal_dates_are_bounded_and_same_day_reruns_do_not_duplicate(self):
        dates = update_unique_signal_dates(
            CONFIG, ["bad-date", "2026-10-19", "2026-10-21", "2026-11-21"], "2026-10-21")
        dates = update_unique_signal_dates(CONFIG, dates, "2026-10-22")
        dates = update_unique_signal_dates(CONFIG, dates, "2026-11-21")
        self.assertEqual(dates, ["2026-10-21", "2026-10-22"])

        boundaries = update_unique_signal_dates(CONFIG, [], "2026-10-20")
        boundaries = update_unique_signal_dates(CONFIG, boundaries, "2026-11-20")
        self.assertEqual(boundaries, ["2026-10-20", "2026-11-20"])

    def test_late_candidate_cannot_inherit_earlier_campaign_days(self):
        status = build_campaign_status(
            CONFIG, "2026-11-20",
            {"win_rate": 0.42, "profit_factor": 1.10, "average_return": 0.001},
            candidate_found=True,
            unique_signal_dates=[f"2026-11-{day:02d}" for day in range(1, 21)],
            candidate_proposed_on="2026-11-19",
            candidate_forward_signal_days=1,
        )
        self.assertFalse(status["ready_for_manual_review"])
        self.assertEqual(status["candidate_forward_signal_days"], 1)

    def test_active_campaign_tracks_both_metrics_without_promising_improvement(self):
        status = build_campaign_status(CONFIG, "2026-10-31", {
            "win_rate": 0.40, "profit_factor": 0.60, "average_return": -0.008,
        }, candidate_found=False)

        self.assertEqual(status["status"], "active")
        self.assertFalse(status["guaranteed_improvement"])
        self.assertEqual(status["metric_checks"], {
            "win_rate_above_baseline": True,
            "profit_factor_above_baseline": True,
        })
        self.assertFalse(status["candidate_found"])
        self.assertFalse(status["ready_for_manual_review"])
        self.assertFalse(status["eligible_for_activation"])

    def test_campaign_completion_never_claims_forward_outcome_approval(self):
        status = build_campaign_status(CONFIG, "2026-11-20", {
            "win_rate": 0.42, "profit_factor": 1.10, "average_return": 0.001,
        }, candidate_found=True,
            unique_signal_dates=[f"2026-11-{day:02d}" for day in range(1, 21)],
            candidate_proposed_on="2026-10-20", candidate_forward_signal_days=20)

        self.assertEqual(status["status"], "complete")
        self.assertTrue(status["objective_met"])
        self.assertFalse(status["auto_activation"])
        self.assertFalse(status["ready_for_manual_review"])
        self.assertTrue(status["manual_review_recommended"])
        self.assertFalse(status["forward_outcome_validated"])
        self.assertFalse(status["eligible_for_activation"])

    def test_improving_only_win_rate_does_not_meet_joint_objective(self):
        status = build_campaign_status(CONFIG, "2026-11-20", {
            "win_rate": 0.50, "profit_factor": 0.40, "average_return": -0.01,
        }, candidate_found=True)

        self.assertFalse(status["objective_met"])
        self.assertFalse(status["ready_for_manual_review"])


if __name__ == "__main__":
    unittest.main()
