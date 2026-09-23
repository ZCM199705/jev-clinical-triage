import unittest

from postanalysis.factors import analyze_factors


def case(vid, **kw):
    factors = {"anchor_type": "-", "barrier_type": "-", "gender": "man", "race": "White"}
    factors.update(kw.pop("factors", {}))
    return {"variant_id": vid, "scenario_id": kw.pop("scenario_id", "s1"),
            "dataset_id": kw.pop("dataset_id", "nm_main"),
            "information_level": kw.pop("information_level", "objective"),
            "acceptable_labels": kw.pop("acceptable_labels", ["D"]), "factors": factors, **kw}


def result(pred="D", status="success"):
    return {"status": status, "parsed": {"triage": pred} if pred else None}


class OfflineFactorsTest(unittest.TestCase):
    def test_single_factor_and_variant_code_ignored(self):
        cases = {"b": case("b", factors={"variant_code": "X"}),
                 "t": case("t", factors={"anchor_type": "support", "variant_code": "Y"})}
        results = {(1, "jev", "b"): result("D"), (1, "jev", "t"): result("C")}
        out = analyze_factors(cases, results)
        rows = [x for x in out["pairs"] if x["factor"] == "anchor_type"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["correct_to_wrong"])

    def test_sensitivity_excludes_incomplete_pair(self):
        cases = {"b": case("b", ai_source_review_flag=True), "t": case("t", factors={"race": "Black"})}
        out = analyze_factors(cases, {(1, "jev", "b"): result(), (1, "jev", "t"): result()})
        row = next(x for x in out["summary"] if x["factor"] == "race" and x["mask"] == "exclude_ai_flags")
        self.assertEqual((row["planned_pairs"], row["lost_selection_pairs"], row["selected_pairs"]), (1, 1, 0))

    def test_invalid_answers_are_invalid_pairs(self):
        cases = {"b": case("b"), "t": case("t", factors={"gender": "woman"})}
        out = analyze_factors(cases, {(1, "luna", "b"): result("D"), (1, "luna", "t"): result("X")})
        row = next(x for x in out["summary"] if x["factor"] == "gender")
        self.assertEqual(row["invalid_pairs"], 1)

    def test_duplicate_match_rejected(self):
        cases = {"b": case("b"), "t1": case("t1", factors={"race": "Black"}),
                 "t2": case("t2", factors={"race": "Black"})}
        out = analyze_factors(cases, {})
        self.assertTrue(any(x["type"] == "nonunique_match" and x["factor"] == "race" for x in out["issues"]))
        self.assertFalse(any(x["factor"] == "race" for x in out["pairs"]))

    def test_masks_levels_and_invalid_contrast(self):
        cases = {"b": case("b"),
                 "a": case("a", factors={"anchor_type": "alarm"}),
                 "p": case("p", factors={"anchor_type": "reassurance"})}
        results = {(1, "jev", "b"): result("D"),
                   (1, "jev", "a"): result("X"),
                   (1, "jev", "p"): result("C")}
        out = analyze_factors(cases, results)
        rows = [x for x in out["summary"] if x["factor"] == "anchor_type"]
        self.assertEqual({x["changed_level"] for x in rows}, {"alarm", "reassurance"})
        self.assertEqual({x["mask"] for x in rows}, {"all", "exclude_ai_flags"})
        pair = next(x for x in out["pairs"] if x["changed_id"] == "a")
        self.assertFalse(pair["valid_pair"])
        self.assertIsNone(pair["correct_to_wrong"])

    def test_extra_factor_prevents_confounded_match(self):
        cases = {"b": case("b", factors={"extra": "x"}),
                 "t": case("t", factors={"gender": "woman", "extra": "y"})}
        out = analyze_factors(cases, {(1, "jev", "b"): result(), (1, "jev", "t"): result()})
        self.assertFalse(any(x["factor"] == "gender" for x in out["pairs"]))

    def test_information_uses_own_gold_and_sensitivity_full_pairs(self):
        cases={"s":case("s",information_level="subjective",acceptable_labels=["A"],ai_source_review_flag=True),
               "o":case("o",information_level="objective",acceptable_labels=["D"])}
        results={(1,"jev","s"):result("A"),(1,"jev","o"):result("D")}
        out=analyze_factors(cases,results)
        full=next(x for x in out['summary'] if x['factor']=='information_level' and x['mask']=='all')
        sens=next(x for x in out['summary'] if x['factor']=='information_level' and x['mask']=='exclude_ai_flags')
        self.assertEqual(full['baseline_accuracy'],1)
        self.assertEqual(full['changed_accuracy'],1)
        self.assertEqual(full['accuracy_difference'],0)
        self.assertEqual(full['prediction_changed'],1)
        self.assertEqual(sens['lost_selection_pairs'],1)
        self.assertEqual(sens['valid_pairs'],0)

    def test_duplicate_stops_entire_factor_with_other_valid_pairs(self):
        cases={'b':case('b'),'t':case('t',factors={'race':'Black'}),
               'dup':case('dup',factors={'race':'Black'}),
               'b2':case('b2',scenario_id='s2'),'t2':case('t2',scenario_id='s2',factors={'race':'Black'})}
        out=analyze_factors(cases,{(1,'jev',v):result() for v in cases})
        self.assertFalse(any(p['factor']=='race' for p in out['pairs']))


if __name__ == "__main__":
    unittest.main()
