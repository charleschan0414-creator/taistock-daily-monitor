import unittest

from taistock.broker_risk import (
    WATCHED_SHORT_TERM_BRANCHES,
    analyze_broker_risk,
    enrich_broker_risk,
    parse_broker_report,
)


HTML = """
<html><head><title>主力賣買超-6269</title></head><body>
<div class="t11">單位：張　最後更新日：2026/10/19</div>
<table>
<tr><td class="t4t1"><a>國泰-敦南</a></td><td class="t3n1">8,362</td><td class="t3n1">7,357</td><td class="t3n1">1,005</td><td class="t3n1">0.56%</td>
<td class="t4t1"><a>元大證券</a></td><td class="t3n1">13,923</td><td class="t3n1">24,095</td><td class="t3n1">10,172</td><td class="t3n1">5.65%</td></tr>
<tr><td class="t4t1"><a>美商高盛</a></td><td class="t3n1">6,163</td><td class="t3n1">2,234</td><td class="t3n1">3,929</td><td class="t3n1">2.18%</td>
<td class="t4t1"><a>凱基-城中</a></td><td class="t3n1">919</td><td class="t3n1">1,946</td><td class="t3n1">1,027</td><td class="t3n1">0.57%</td></tr>
</table></body></html>
"""


class BrokerRiskTests(unittest.TestCase):
    def test_parses_paired_buy_and_sell_leaders(self):
        report = parse_broker_report(HTML, "6269")
        self.assertEqual(report["data_date"], "2026-10-19")
        self.assertEqual(report["buy_leaders"][0]["branch"], "國泰-敦南")
        self.assertEqual(report["buy_leaders"][0]["net_lots"], 1005)
        self.assertEqual(report["sell_leaders"][1]["branch"], "凱基-城中")
        self.assertEqual(report["sell_leaders"][1]["net_lots"], -1027)

    def test_rejects_report_for_another_stock_code(self):
        with self.assertRaises(ValueError):
            parse_broker_report(HTML, "3231")

    def test_flags_watched_top_buyer_without_affecting_score(self):
        report = parse_broker_report(HTML, "6269")
        risk = analyze_broker_risk(report, total_volume_shares=180_081_775,
                                   watched=WATCHED_SHORT_TERM_BRANCHES)
        self.assertEqual(risk["status"], "available")
        self.assertEqual(risk["score_effect"], "none")
        self.assertTrue(risk["warning"])
        self.assertIn("國泰-敦南", [row["branch"] for row in risk["watched_buyers"]])
        self.assertGreater(risk["watched_buyers"][0]["turnover_share_pct"], 8)
        self.assertIn("國泰-敦南", risk["summary"])

    def test_enrichment_rejects_stale_source_and_never_filters_signal(self):
        result = {"data_date": "2026-10-20", "signals": [{
            "code": "6269", "volume": 180_081_775, "score": 88.87,
        }]}
        enriched = enrich_broker_risk(result, fetch_text=lambda _url: HTML)
        self.assertEqual(len(enriched["signals"]), 1)
        self.assertEqual(enriched["signals"][0]["score"], 88.87)
        research = enriched["signals"][0]["broker_risk_research"]
        self.assertEqual(research["status"], "stale")
        self.assertFalse(research["warning"])
        self.assertEqual(research["score_effect"], "none")

    def test_available_enrichment_changes_only_broker_research(self):
        expected = {"data_date": "2026-10-19", "strategy_gate": {"enabled": False}, "signals": [{
            "code": "6269", "name": "台郡", "volume": 180_081_775, "score": 88.0,
        }]}
        result = {"data_date": expected["data_date"], "strategy_gate": dict(expected["strategy_gate"]),
                  "signals": [dict(expected["signals"][0])]}
        enriched = enrich_broker_risk(result, fetch_text=lambda _url: HTML)
        research = enriched["signals"][0].pop("broker_risk_research")
        enriched.pop("broker_risk_research")
        self.assertEqual(research["status"], "available")
        self.assertEqual(enriched, expected)

    def test_enrichment_fails_open_as_unavailable_research(self):
        result = {"data_date": "2026-10-19", "signals": [{"code": "6269", "volume": 1000}]}
        enriched = enrich_broker_risk(result, fetch_text=lambda _url: (_ for _ in ()).throw(RuntimeError("offline")))
        research = enriched["signals"][0]["broker_risk_research"]
        self.assertEqual(research["status"], "unavailable")
        self.assertFalse(research["warning"])
        self.assertNotIn("offline", str(research))


if __name__ == "__main__":
    unittest.main()
