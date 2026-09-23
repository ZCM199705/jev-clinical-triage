import unittest

from analysis.paired import holm_adjust, paired_cluster_bootstrap, paired_denominators, paired_sign_flip_pvalue
from analysis.scoring import score_prediction, summarize_predictions


class TestToyScoring(unittest.TestCase):
    """All data here are synthetic toy records, never study results."""

    def test_edge_labels_and_two_level_undertriage(self):
        self.assertTrue(score_prediction("C", ["C", "D"])["correct"])
        self.assertTrue(score_prediction("A", ["C", "D"])["cross_two_levels_under"])
        self.assertTrue(score_prediction("C", ["D"])["explicit_d_undertriage"])
        self.assertTrue(score_prediction("C", ["D"])["d_to_non_d"])
        self.assertTrue(score_prediction("D", ["D"])["d_to_non_d"] is False)
        self.assertFalse(score_prediction("B", ["C", "D"])["cross_two_levels_under"])

    def test_acceptable_label_order_is_semantic(self):
        self.assertTrue(score_prediction("C", ["D", "C"])["correct"])
        self.assertTrue(score_prediction("A", ["D", "C"])["cross_two_levels_under"])

    def test_invalid_is_not_a_clinical_grade_and_keeps_full_denominator(self):
        result = summarize_predictions([
            {"prediction": "D", "acceptable_labels": ["D"], "terminal_status": "success"},
            {"prediction": None, "acceptable_labels": ["C", "D"], "terminal_status": "parse_error"},
        ])
        self.assertEqual(result["planned_requests"], 2)
        self.assertEqual(result["valid_decisions"], 1)
        self.assertEqual(result["correct_rate_valid"], 1.0)
        self.assertEqual(result["correct_rate_full_plan"], 0.5)
        self.assertEqual(result["explicit_d_undertriage_valid"], 0)

    def test_failed_terminal_status_cannot_be_rescued_by_residual_label(self):
        result = summarize_predictions([
            {"prediction": "D", "acceptable_labels": ["D"], "terminal_status": "parse_error"},
        ])
        self.assertEqual(result["valid_decisions"], 0)
        self.assertEqual(result["correct_rate_full_plan"], 0.0)


class TestToyPairedInference(unittest.TestCase):
    def _records(self):
        return [
            {"scenario_id": "s1", "variant_id": "v1",
             "model_a": {"valid_decision": True, "correct": True},
             "model_b": {"valid_decision": True, "correct": False}},
            {"scenario_id": "s1", "variant_id": "v2",
             "model_a": {"valid_decision": True, "correct": False},
             "model_b": {"valid_decision": True, "correct": False}},
            {"scenario_id": "s2", "variant_id": "v1",
             "model_a": {"valid_decision": True, "correct": True},
             "model_b": {"valid_decision": False, "correct": False}},
        ]

    def test_common_pair_excludes_missing_pair_but_retains_scenario_versions(self):
        result = paired_cluster_bootstrap(self._records(), iterations=100)
        self.assertEqual(result["n_scenarios"], 1)
        self.assertEqual(result["estimate"], 0.5)  # s1: (1/2) - (0/2)
        self.assertEqual(result["seed"], 20260922)
        denominators = paired_denominators(self._records())
        self.assertEqual(denominators["planned_requests"], 3)
        self.assertEqual(denominators["common_valid_requests"], 2)
        self.assertEqual(denominators["missing_or_invalid_pairs"], 1)
        self.assertEqual(result["excluded_scenarios"], 1)

    def test_full_plan_bootstrap_counts_invalid_as_zero(self):
        result = paired_cluster_bootstrap(self._records(), iterations=100, common_only=False)
        self.assertEqual(result["n_scenarios"], 2)
        self.assertAlmostEqual(result["estimate"], 0.75)

    def test_empty_scenario_is_explicit(self):
        result = paired_cluster_bootstrap([])
        self.assertEqual(result["status"], "empty")
        self.assertIsNone(result["estimate"])

    def test_secondary_pvalue_and_holm(self):
        self.assertEqual(paired_sign_flip_pvalue([1.0, 1.0]), 0.5)
        self.assertEqual(holm_adjust([0.01, 0.04, 0.03]), [0.03, 0.06, 0.06])


if __name__ == "__main__":
    unittest.main()
