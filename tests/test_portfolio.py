import unittest

from build_software_manifest import RELEASE_FILES
from taistock.portfolio import simulate_portfolio


def trade(code="2330", direction="long", entry_date="2026-01-02", exit_date="2026-01-05",
          entry=100.0, exit=110.0, stop=95.0, score=90.0, volume_rank=1):
    return {
        "signal_date": "2026-01-01", "code": code, "direction": direction,
        "status": "settled", "triggered": True, "entry_date": entry_date,
        "exit_date": exit_date, "entry": entry, "exit": exit, "stop_loss": stop,
        "score": score, "volume_rank": volume_rank,
    }


class PortfolioSimulationTests(unittest.TestCase):
    def test_desktop_release_includes_portfolio_module(self):
        self.assertIn("taistock/portfolio.py", RELEASE_FILES)

    def test_sizes_whole_lots_and_realizes_on_exit_date_with_round_trip_cost(self):
        result = simulate_portfolio([trade()])

        self.assertEqual(result["realized_trades"][0]["shares"], 1000)
        self.assertAlmostEqual(result["gross_pnl"], 10_000)
        self.assertAlmostEqual(result["costs"], 435)
        self.assertAlmostEqual(result["net_pnl"], 9_565)
        self.assertAlmostEqual(result["final_capital"], 1_009_565)
        self.assertEqual([point["date"] for point in result["equity_curve"]],
                         ["2026-01-02", "2026-01-05"])
        self.assertEqual(result["equity_curve"][0]["equity"], 1_000_000)
        self.assertAlmostEqual(result["equity_curve"][1]["equity"], 1_009_565)
        self.assertTrue(result["realized_only_equity"])
        self.assertEqual(result["model"], {
            "risk_per_trade": 0.005, "max_positions": 10,
            "max_new_per_direction_per_day": 5, "max_notional_per_position": 0.20,
            "cost_rate": 0.00435, "board_lot_shares": 1000,
        })

    def test_existing_exit_frees_slot_before_new_entries_and_duplicate_open_code_is_skipped(self):
        rows = [
            trade("1111", exit_date="2026-01-03"),
            trade("2222", entry_date="2026-01-03", exit_date="2026-01-04", score=80),
            trade("2222", entry_date="2026-01-03", exit_date="2026-01-05", score=70),
        ]
        result = simulate_portfolio(rows, max_positions=1)

        self.assertEqual([row["code"] for row in result["realized_trades"]], ["1111", "2222"])
        self.assertEqual(result["skipped"][0]["reason"], "duplicate_code")

    def test_enforces_position_and_daily_direction_limits_in_priority_order(self):
        rows = [trade(str(code), score=score, volume_rank=rank)
                for code, score, rank in [("1001", 90, 2), ("1002", 90, 1), ("1003", 80, 3)]]
        result = simulate_portfolio(rows, max_positions=2, max_new_per_direction_per_day=1)

        self.assertEqual([row["code"] for row in result["realized_trades"]], ["1002"])
        self.assertEqual([row["reason"] for row in result["skipped"]],
                         ["daily_direction_limit", "daily_direction_limit"])

    def test_rejects_less_than_one_board_lot_and_calculates_short_pnl(self):
        too_small = trade("1001", entry=1000, exit=1010, stop=990)
        short = trade("1002", direction="short", entry=50, exit=45, stop=55,
                      entry_date="2026-01-06", exit_date="2026-01-07")
        result = simulate_portfolio([too_small, short])

        self.assertEqual(result["skipped"][0]["reason"], "insufficient_capital")
        self.assertEqual(result["realized_trades"][0]["shares"], 1000)
        self.assertEqual(result["realized_trades"][0]["gross_pnl"], 5000)
        self.assertFalse(result["borrow_fees_included"])
        self.assertEqual(result["short_availability_mode"], "assumed_available")

    def test_same_day_trades_settle_after_existing_exits_and_are_deterministic(self):
        rows = [
            trade("1000", exit_date="2026-01-03", score=1),
            trade("2002", entry_date="2026-01-03", exit_date="2026-01-03", score=80),
            trade("2001", entry_date="2026-01-03", exit_date="2026-01-03", score=90),
        ]
        first = simulate_portfolio(rows, max_positions=1)
        second = simulate_portfolio(list(reversed(rows)), max_positions=1)

        self.assertEqual(first, second)
        self.assertEqual([row["code"] for row in first["realized_trades"]],
                         ["1000", "2001", "2002"])
        self.assertEqual(first["event_order"],
                         "existing_exits_then_priority_entries_then_same_day_exits")

    def test_reports_peak_exposure_by_direction(self):
        rows = [trade("1001"), trade("1002", direction="short", stop=105)]
        result = simulate_portfolio(rows)

        self.assertEqual(result["peak_positions"], 2)
        self.assertEqual(result["exposure"]["peak_notional"], 200_000)
        self.assertEqual(result["exposure"]["by_direction"]["long"]["peak_notional"], 100_000)
        self.assertEqual(result["exposure"]["by_direction"]["short"]["peak_notional"], 100_000)

    def test_max_positions_rejects_lower_priority_overlapping_trade(self):
        rows = [trade("1001", score=90), trade("1002", score=80)]
        result = simulate_portfolio(rows, max_positions=1)

        self.assertEqual([row["code"] for row in result["realized_trades"]], ["1001"])
        self.assertEqual(result["skipped"][0]["reason"], "max_positions")

    def test_zero_stop_distance_is_skipped_as_invalid_risk(self):
        result = simulate_portfolio([trade(stop=100)])

        self.assertEqual(result["realized_trades"], [])
        self.assertEqual(result["skipped"][0]["reason"], "invalid_risk")


if __name__ == "__main__":
    unittest.main()
