import math

from postanalysis.calibration import analyze_calibration


def case(variant, label="A", dataset="nm_main", scenario="s1", labels=None):
    return {"variant_id": variant, "dataset_id": dataset, "scenario_id": scenario,
            "acceptable_labels": [label] if labels is None else labels}


def result(probs, status="success", triage="A"):
    return {"status": status, "parsed": {"triage": triage, "probabilities": probs}}


def test_brier_is_sum_over_four_and_nll_is_natural_log():
    probs = {"A": .5, "B": .2, "C": .2, "D": .1}
    out = analyze_calibration({"v": case("v")}, {(1, "jev", "v"): result(probs)})
    metric = out["primary"]["layers"]["main_clear"]
    assert metric["brier"] == sum((probs[x] - (x == "A")) ** 2 for x in "ABCD")
    assert metric["mean_nll"] == -math.log(.5)


def test_zero_true_probability_reports_infinity_without_clipping():
    probs = {"A": 0.0, "B": .4, "C": .3, "D": .3}
    out = analyze_calibration({"v": case("v")}, {(1, "jev", "v"): result(probs, triage="B")})
    metric = out["primary"]["layers"]["main_clear"]
    assert metric["mean_nll"] == "infinity"


def test_confidence_one_is_in_final_bin_and_high_confidence_error_counted():
    probs = {"A": 0.0, "B": 0.0, "C": 0.0, "D": 1.0}
    out = analyze_calibration({"v": case("v")}, {(1, "jev", "v"): result(probs, triage="D")})
    metric = out["primary"]["layers"]["main_clear"]
    assert metric["bins"][-1]["count"] == 1
    assert metric["high_confidence_errors"] == 1
    assert metric["d_to_non_d"] == 0


def test_bins_have_lower_bound_and_account_for_every_valid_prediction():
    probs = {"A": .55, "B": .2, "C": .15, "D": .1}
    out = analyze_calibration({"v": case("v")}, {(1, "jev", "v"): result(probs)})
    bins = out["primary"]["layers"]["main_clear"]["bins"]
    assert bins[5]["count"] == 1
    assert bins[9]["count"] == 0
    assert sum(item["count"] for item in bins) == 1


def test_failed_status_does_not_rescue_residual_parsed_probabilities():
    probs = {"A": 1.0, "B": 0.0, "C": 0.0, "D": 0.0}
    out = analyze_calibration({"v": case("v")}, {(1, "jev", "v"): result(probs, status="parse_error")})
    metric = out["primary"]["layers"]["main_clear"]
    assert metric["invalid"] == 1 and metric["valid"] == 0


def test_multilabel_edge_is_excluded_and_later_round_is_separate():
    cases = {"edge": case("edge", labels=["C", "D"]), "clear": case("clear", scenario="s2")}
    probs = {"A": 1.0, "B": 0.0, "C": 0.0, "D": 0.0}
    results = {(1, "jev", "edge"): result(probs), (1, "jev", "clear"): result(probs),
               (2, "jev", "clear"): result(probs)}
    out = analyze_calibration(cases, results)
    first = out["primary"]["layers"]["main_clear"]
    assert (first["planned"], first["excluded_edge"], first["valid"]) == (2, 1, 1)
    assert out["later_rounds"]["2"]["layers"]["main_clear"]["valid"] == 1
