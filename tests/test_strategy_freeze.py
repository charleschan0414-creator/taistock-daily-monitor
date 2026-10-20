import json
import tempfile
import unittest
from pathlib import Path

from taistock.strategy_freeze import (
    EXECUTION_MODEL,
    MINIMUM_FORWARD_SIGNAL_DAYS,
    PROTECTIVE_BASELINE_GATE,
    apply_active_gate_to_report,
    build_registry_update,
    load_registry,
    write_registry,
)


GATE_A = {"long_score_min": 55, "short_score_min": 70, "short_enabled": True, "volume_growth_min": 20}
GATE_B = {"long_score_min": 70, "short_score_min": 70, "short_enabled": False, "volume_growth_min": 50}


def approved_report(gate, baseline_gate=None):
    return {
        "version": 2,
        "baseline_gate": dict(baseline_gate or gate),
        "decision": {
            "upgrade_approved": True,
            "cost_validated": True,
            "applied_mode": "candidate",
            "applied_gate": dict(gate),
            "status": "approved",
        },
    }


class StrategyFreezeTests(unittest.TestCase):
    def test_missing_baseline_gate_uses_immutable_protective_gate(self):
        report = approved_report(GATE_B)
        report.pop("baseline_gate")
        registry = build_registry_update(None, report, "2026-10-15", campaign_id="campaign-a")

        self.assertEqual(registry["active_gate"], PROTECTIVE_BASELINE_GATE)
        self.assertEqual(registry["pending_candidate"]["gate"], GATE_B)
        self.assertFalse(registry["cost_validated"])

    def test_missing_registry_keeps_baseline_active_and_new_candidate_pending(self):
        registry = build_registry_update(
            None, approved_report(GATE_B, baseline_gate=GATE_A), "2026-10-15")

        self.assertEqual(registry["active_gate"], GATE_A)
        self.assertFalse(registry["cost_validated"])
        self.assertEqual(registry["pending_candidate"]["gate"], GATE_B)
        self.assertEqual(registry["pending_candidate"]["forward_signal_days"], 0)
        self.assertFalse(registry["pending_candidate"]["auto_promotion_allowed"])

    def test_first_adoption_starts_zero_day_forward_observation(self):
        registry = build_registry_update(None, approved_report(GATE_A), "2026-10-15")

        self.assertEqual(registry["active_gate"], GATE_A)
        self.assertEqual(registry["strategy_version"], 1)
        self.assertEqual(registry["activated_on"], "2026-10-15")
        self.assertEqual(registry["minimum_forward_signal_days"], 20)
        self.assertEqual(registry["forward_signal_days"], 0)
        self.assertEqual(registry["last_observed_date"], "2026-10-15")
        self.assertEqual(registry["status"], "forward_observation")
        self.assertEqual(registry["execution_model"], EXECUTION_MODEL)
        self.assertTrue(registry["forward_observation"]["not_holdout_reuse"])

    def test_execution_model_change_starts_new_strategy_version_and_forward_window(self):
        legacy = build_registry_update(None, approved_report(GATE_A), "2026-10-15")
        legacy.pop("execution_model")
        legacy["forward_signal_days"] = 12

        migrated = build_registry_update(legacy, approved_report(GATE_B), "2026-10-16")

        self.assertEqual(migrated["strategy_version"], 2)
        self.assertEqual(migrated["execution_model"], EXECUTION_MODEL)
        self.assertEqual(migrated["active_gate"], GATE_A)
        self.assertFalse(migrated["cost_validated"])
        self.assertEqual(migrated["activated_on"], "2026-10-16")
        self.assertEqual(migrated["forward_signal_days"], 0)
        self.assertEqual(migrated["last_observed_date"], "2026-10-16")

    def test_same_gate_counts_only_newer_unique_dates_without_resetting_activation(self):
        original = build_registry_update(None, approved_report(GATE_A), "2026-10-15")
        next_day = build_registry_update(original, approved_report(GATE_A), "2026-10-16")
        rerun = build_registry_update(next_day, approved_report(GATE_A), "2026-10-16")

        self.assertEqual(next_day["activated_on"], "2026-10-15")
        self.assertEqual(next_day["forward_signal_days"], 1)
        self.assertEqual(rerun, next_day)

    def test_approved_different_gate_is_pending_during_observation(self):
        current = build_registry_update(None, approved_report(GATE_A), "2026-09-01")
        for day in range(2, 20):
            current = build_registry_update(current, approved_report(GATE_A), f"2026-09-{day:02d}")

        pending = build_registry_update(current, approved_report(GATE_B), "2026-09-20")

        self.assertEqual(pending["active_gate"], GATE_A)
        self.assertEqual(pending["strategy_version"], 1)
        self.assertEqual(pending["forward_signal_days"], 19)
        self.assertEqual(pending["pending_candidate"]["gate"], GATE_B)
        self.assertEqual(pending["status"], "pending_candidate")

    def test_new_gate_never_inherits_incumbent_days_or_auto_activates(self):
        current = build_registry_update(None, approved_report(GATE_A), "2026-09-01")
        for day in range(2, 21):
            current = build_registry_update(current, approved_report(GATE_A), f"2026-09-{day:02d}")

        pending = build_registry_update(current, approved_report(GATE_B), "2026-09-21")
        rerun = build_registry_update(pending, approved_report(GATE_B), "2026-09-21")

        self.assertEqual(current["forward_signal_days"], MINIMUM_FORWARD_SIGNAL_DAYS - 1)
        self.assertEqual(pending["active_gate"], GATE_A)
        self.assertEqual(pending["strategy_version"], 1)
        self.assertEqual(pending["pending_candidate"]["gate"], GATE_B)
        self.assertEqual(pending["pending_candidate"]["forward_signal_days"], 0)
        self.assertFalse(pending["pending_candidate"]["auto_promotion_allowed"])
        self.assertEqual(rerun, pending)

    def test_pending_candidate_resets_when_campaign_changes(self):
        current = build_registry_update(None, approved_report(GATE_A), "2026-09-01")
        pending = build_registry_update(
            current, approved_report(GATE_B), "2026-09-02", campaign_id="campaign-a")
        observed = build_registry_update(
            pending, approved_report(GATE_B), "2026-09-03", campaign_id="campaign-a")
        reset = build_registry_update(
            observed, approved_report(GATE_B), "2026-09-04", campaign_id="campaign-b")

        self.assertEqual(observed["pending_candidate"]["forward_signal_dates"], ["2026-09-03"])
        self.assertEqual(reset["pending_candidate"]["campaign_id"], "campaign-b")
        self.assertEqual(reset["pending_candidate"]["forward_signal_days"], 0)
        self.assertEqual(reset["pending_candidate"]["forward_signal_dates"], [])

    def test_effective_report_uses_frozen_active_gate_for_scanner(self):
        registry = build_registry_update(None, approved_report(GATE_A), "2026-10-15")
        report = apply_active_gate_to_report(approved_report(GATE_B), registry)

        self.assertEqual(report["decision"]["applied_gate"], GATE_A)
        self.assertFalse(report["decision"]["cost_validated"])
        self.assertEqual(report["strategy_freeze"]["strategy_version"], 1)

    def test_execution_model_migration_applies_protective_gate_without_false_cost_validation(self):
        legacy = build_registry_update(None, approved_report(GATE_A), "2026-10-15")
        legacy.pop("execution_model")
        migrated = build_registry_update(legacy, approved_report(GATE_B), "2026-10-16")

        report = apply_active_gate_to_report(approved_report(GATE_B), migrated)

        self.assertTrue(report["decision"]["enabled"])
        self.assertFalse(report["decision"]["upgrade_approved"])
        self.assertFalse(report["decision"]["cost_validated"])
        self.assertEqual(report["decision"]["applied_mode"], "incumbent_protective")
        self.assertEqual(report["decision"]["applied_gate"], GATE_A)
        self.assertIn("未經新當沖模式成本驗證", report["decision"]["status"])

    def test_atomic_write_failure_keeps_existing_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "strategy_registry.json"
            original = build_registry_update(None, approved_report(GATE_A), "2026-10-15")
            write_registry(path, original)
            with self.assertRaises(TypeError):
                write_registry(path, {**original, "bad": object()})
            self.assertEqual(load_registry(path), original)
            with self.assertRaises(ValueError):
                write_registry(path, {"bad": "serializable but invalid"})
            self.assertEqual(load_registry(path), original)


if __name__ == "__main__":
    unittest.main()
