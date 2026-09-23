import json
import unittest

from postanalysis.format_explanation import _format_parse, analyze, summarize_pairs


def evidence(content, finish="stop", refusal=None):
    return {"raw_response": json.dumps({"model": "m", "usage": {"cost": 1.0}, "choices": [{"finish_reason": finish, "message": {"content": content, "refusal": refusal}}]})}


class FormatExplanationTests(unittest.TestCase):
    def test_extra_field_keeps_decision_valid_but_not_exact_schema(self):
        out = _format_parse(evidence(json.dumps({"triage": "C", "explanation": "reason", "type": "x"})))
        self.assertTrue(out["decision_valid"])
        self.assertFalse(out["exact_schema"])
        self.assertTrue(out["extra_fields"])

    def test_failure_residual_rejects_decision(self):
        out = _format_parse(evidence(json.dumps({"triage": "C", "explanation": ""})))
        self.assertFalse(out["decision_valid"])
        self.assertTrue(out["content_json"])

    def test_c_d_and_invalid_type(self):
        for label in ("C", "D"):
            self.assertTrue(_format_parse(evidence(json.dumps({"triage": label, "explanation": "ok"}))) ["decision_valid"])
        self.assertFalse(_format_parse(evidence(json.dumps({"triage": ["D"], "explanation": "ok"}))) ["decision_valid"])

    def test_same_subset_pairing_and_cohorts(self):
        report = analyze()
        self.assertEqual(len(report["pairs"]), 612)
        summary = summarize_pairs(report["pairs"])
        self.assertEqual(summary["r1_luna_main_clear"]["planned"], 30)
        self.assertEqual(summary["r1_luna_main_clear"]["common_valid"]["n_scenarios"], 15)
        self.assertEqual(summary["r1_luna_main_full"]["planned"], 60)
        self.assertEqual(summary["r1_luna_emergency"]["planned"], 8)


if __name__ == "__main__":
    unittest.main()
