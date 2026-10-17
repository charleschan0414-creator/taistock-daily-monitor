import unittest

from taistock.models import Quote
from taistock.core import audit_candidates, is_common_stock, evaluate_candidates, round_tw_price


class CommonStockTests(unittest.TestCase):
    def test_only_four_digit_common_stock_codes_are_accepted(self):
        self.assertTrue(is_common_stock(2330, "台積電"))
        for code, name in [("0050", "元大台灣50"), ("00632R", "元大台灣50反1"), ("12345", "假股票"), ("12A4", "假股票")]:
            with self.subTest(code=code):
                self.assertFalse(is_common_stock(code, name))


class ScreeningTests(unittest.TestCase):
    def test_filters_thresholds_and_ranks_transparently(self):
        rows = [
            {"code":"1111","name":"甲","market":"上市","date":"2026-10-15","close":12,"closes":[8,9,10,11,12],"volume":2000,"prev_volume":1000,"institutional_net":3000,"volume_rank":1},
            {"code":"2222","name":"乙","market":"上櫃","date":"2026-10-15","close":10,"closes":[10,10,10,10,10],"volume":1300,"prev_volume":1000,"institutional_net":1000,"volume_rank":2},
            {"code":"3333","name":"丙","market":"上市","date":"2026-10-15","close":20,"closes":[19,19,19,19,20],"volume":1100,"prev_volume":1000,"institutional_net":5000,"volume_rank":3},
        ]
        got = evaluate_candidates(rows)
        self.assertEqual([x["code"] for x in got], ["1111"])
        self.assertEqual(got[0]["volume_growth_pct"], 100.0)
        self.assertEqual(got[0]["ma5"], 10.0)
        self.assertIn("量增100.0%", got[0]["reason"])

    def test_zero_previous_volume_is_not_eligible(self):
        rows = [{"code":"1111","name":"甲","market":"上市","date":"2026-10-15","close":12,"closes":[8,9,10,11,12],"volume":2000,"prev_volume":0,"institutional_net":3000,"volume_rank":1}]
        self.assertEqual(evaluate_candidates(rows), [])

    def test_audit_preserves_every_row_with_reasons_and_exact_eligible_values(self):
        rows = [
            {"code":"1111","name":"多","market":"上市","date":"2026-10-15","close":12,"closes":[10,10.5,11,11.5,12],"volume":2000,"prev_volume":1000,"institutional_net":3000,"volume_rank":1},
            {"code":"2222","name":"歷史不足","market":"上櫃","date":"2026-10-15","close":10,"closes":[9,10],"volume":1000,"prev_volume":0,"institutional_net":0,"volume_rank":2},
            {"code":"3333","name":"量與方向不合","market":"上市","date":"2026-10-15","close":10,"closes":[10,10,10,10,10],"volume":1100,"prev_volume":1000,"institutional_net":1000,"volume_rank":3},
        ]
        evaluated = evaluate_candidates(rows)
        audited = audit_candidates(rows)
        self.assertEqual(len(audited), 3)
        self.assertTrue(audited[0]["eligible"])
        self.assertEqual(audited[0]["signal_direction"], "long")
        for key in ("score", "entry_trigger", "stop_loss", "target1", "target2"):
            self.assertEqual(audited[0][key], evaluated[0][key])
        self.assertEqual(
            audited[1]["rejection_reasons"],
            ["insufficient_history", "zero_previous_volume"],
        )
        self.assertEqual(
            audited[2]["rejection_reasons"],
            ["volume_growth_below_20", "ma5_institutional_misaligned"],
        )
        self.assertFalse(audited[2]["eligible"])
        self.assertIsNone(audited[2]["signal_direction"])
        self.assertIsNone(audited[2]["score"])

    def test_audit_does_not_change_evaluate_candidates_output(self):
        rows = [{"code":"1111","name":"多","market":"上市","date":"2026-10-15","close":12,
                 "closes":[10,10.5,11,11.5,12],"volume":2000,"prev_volume":1000,
                 "institutional_net":3000,"volume_rank":1}]
        before = evaluate_candidates(rows)
        audit_candidates(rows)
        self.assertEqual(evaluate_candidates(rows), before)
        self.assertNotIn("eligible", before[0])
        self.assertNotIn("rejection_reasons", before[0])

    def test_long_and_short_are_symmetric_and_include_reproducible_trade_levels(self):
        rows = [
            {"code":"1111","name":"多","market":"上市","date":"2026-10-15","close":12,"closes":[10,10.5,11,11.5,12],"volume":2000,"prev_volume":1000,"institutional_net":3000,"volume_rank":1},
            {"code":"2222","name":"空","market":"上櫃","date":"2026-10-15","close":18,"closes":[20,19.5,19,18.5,18],"volume":3000,"prev_volume":1000,"institutional_net":-4000,"volume_rank":2},
            {"code":"3333","name":"量不足","market":"上市","date":"2026-10-15","close":8,"closes":[10,9.5,9,8.5,8],"volume":1199,"prev_volume":1000,"institutional_net":-3000,"volume_rank":3},
        ]
        got = evaluate_candidates(rows)
        self.assertEqual([x["signal_direction"] for x in got], ["long", "short"])
        long, short = got
        self.assertGreater(long["entry_trigger"], long["close"])
        self.assertLess(long["stop_loss"], long["entry_trigger"])
        self.assertGreater(long["target2"], long["target1"])
        self.assertLess(short["entry_trigger"], short["close"])
        self.assertGreater(short["stop_loss"], short["entry_trigger"])
        self.assertLess(short["target2"], short["target1"])
        self.assertIn("規則式", long["action_text"])
        self.assertGreaterEqual(long["risk_pct"], 1.5)
        self.assertLessEqual(long["risk_pct"], 5.0)


class TaiwanTickTests(unittest.TestCase):
    def test_rounds_half_up_at_every_price_band_boundary(self):
        cases = [(9.994, 9.99), (9.995, 10.00), (10.024, 10.00), (10.025, 10.05),
                 (49.974, 49.95), (49.975, 50.00), (50.049, 50.0), (50.05, 50.1),
                 (99.949, 99.9), (99.95, 100.0), (100.24, 100.0), (100.25, 100.5),
                 (499.74, 499.5), (499.75, 500.0), (500.49, 500.0), (500.5, 501.0),
                 (999.49, 999.0), (999.5, 1000.0), (1002.4, 1000.0), (1002.5, 1005.0)]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(round_tw_price(raw), expected)


if __name__ == "__main__":
    unittest.main()
