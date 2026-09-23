"""Bounded, deterministic offline calibration summaries for Jev results.

This module consumes already materialised case and result mappings.  It does
not read the ledger, infer missing responses, or alter frozen probabilities.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

LABELS = ("A", "B", "C", "D")
BIN_COUNT = 10


def _case_label(case: dict[str, Any]) -> str | None:
    labels = case.get("acceptable_labels")
    if isinstance(labels, (list, tuple)) and len(labels) == 1 and labels[0] in LABELS:
        return labels[0]
    return None


def _probabilities(result: Any) -> dict[str, float] | None:
    if not isinstance(result, dict) or result.get("status") != "success":
        return None
    # Parsed data is intentionally consulted only after terminal success.
    parsed = result.get("parsed")
    if not isinstance(parsed, dict):
        return None
    probs = parsed.get("probabilities")
    if not isinstance(probs, dict) or set(probs) != set(LABELS):
        return None
    out: dict[str, float] = {}
    for label in LABELS:
        value = probs[label]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if not math.isfinite(float(value)) or not 0 <= float(value) <= 1:
            return None
        out[label] = float(value)
    if abs(sum(out.values()) - 1.0) > 0.002:
        return None
    return out


def _empty_metric(planned: int, edge: int, invalid: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = len(rows)
    if not valid:
        return {
            "planned": planned, "excluded_edge": edge, "invalid": invalid, "valid": 0,
            "brier": None, "mean_nll": None, "prediction_weighted": None,
            "scenario_equal": None, "probability_sum_min": None,
            "probability_sum_max": None, "probability_sum_note": "approximate probabilities retained",
            "bins": _bins([]), "zero_probability_true_label_count": 0,
            "high_confidence_valid": 0, "high_confidence_errors": 0,
            "high_confidence_d_to_non_d": 0, "d_to_non_d": 0,
        }
    sums = [sum(r["probabilities"].values()) for r in rows]
    brier = sum(sum((r["probabilities"][label] - (label == r["true_label"])) ** 2 for label in LABELS) for r in rows) / valid
    zero_nll = sum(r["probabilities"][r["true_label"]] == 0 for r in rows)
    nll_values = [(-math.log(r["probabilities"][r["true_label"]]) if r["probabilities"][r["true_label"]] > 0 else math.inf) for r in rows]
    mean_nll: float | str = "infinity" if zero_nll else sum(nll_values) / valid
    pred_weighted = {"brier": brier, "mean_nll": mean_nll}
    by_scenario: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_scenario[str(row["scenario_id"])].append(row)
    scenario_briers = [sum(sum((r["probabilities"][label] - (label == r["true_label"])) ** 2 for label in LABELS) for r in group) / len(group) for group in by_scenario.values()]
    scenario_nlls: list[float] = []
    scenario_nll_infinity = False
    for group in by_scenario.values():
        vals = [r["probabilities"][r["true_label"]] for r in group]
        if any(v == 0 for v in vals):
            scenario_nll_infinity = True
        else:
            scenario_nlls.append(sum(-math.log(v) for v in vals) / len(vals))
    scenario_equal = {
        "brier": sum(scenario_briers) / len(scenario_briers),
        "mean_nll": "infinity" if scenario_nll_infinity else sum(scenario_nlls) / len(scenario_nlls),
        "scenarios": len(by_scenario),
    }
    return {
        "planned": planned, "excluded_edge": edge, "invalid": invalid, "valid": valid,
        "brier": brier, "mean_nll": mean_nll, "prediction_weighted": pred_weighted,
        "scenario_equal": scenario_equal, "probability_sum_min": min(sums),
        "probability_sum_max": max(sums), "probability_sum_note": "approximate probabilities retained",
        "bins": _bins(rows),
        "zero_probability_true_label_count": zero_nll,
        "high_confidence_valid": sum(r["confidence"] >= 0.9 for r in rows),
        "high_confidence_errors": sum(r["confidence"] >= 0.9 and r["predicted"] != r["true_label"] for r in rows),
        "high_confidence_d_to_non_d": sum(r["confidence"] >= 0.9 and r["true_label"] == "D" and r["predicted"] != "D" for r in rows),
        "d_to_non_d": sum(r["true_label"] == "D" and r["predicted"] != "D" for r in rows),
    }


def _bins(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for index in range(BIN_COUNT):
        lower, upper = index / BIN_COUNT, (index + 1) / BIN_COUNT
        members = [r for r in rows if lower <= r["confidence"] < upper or (index == BIN_COUNT - 1 and lower <= r["confidence"] <= upper)]
        result.append({"bin": index, "lower": lower, "upper": upper,
                       "count": len(members),
                       "mean_confidence": (sum(r["confidence"] for r in members) / len(members) if members else None),
                       "accuracy": (sum(r["predicted"] == r["true_label"] for r in members) / len(members) if members else None)})
    return result


def analyze_calibration(cases: dict[str, dict[str, Any]], results: dict[tuple[int, str, str], dict[str, Any]]) -> dict[str, Any]:
    """Return calibration metrics grouped by round and clinical case layer.

    Round one is exposed as ``primary``; subsequent rounds are retained under
    ``later_rounds`` and are never pooled with it.
    """
    rounds = sorted({key[0] for key in results if isinstance(key, tuple) and len(key) == 3 and isinstance(key[0], int)})
    report: dict[str, Any] = {"primary_round": 1, "primary": None, "later_rounds": {}}
    for round_number in rounds:
        layers: dict[str, Any] = {}
        for layer, predicate in (("main_clear", lambda c: c.get("dataset_id") == "nm_main"),
                                 ("emergency", lambda c: c.get("dataset_id") == "nm_emergency")):
            planned = edge = invalid = 0
            rows: list[dict[str, Any]] = []
            for variant_id, case in sorted(cases.items()):
                if not predicate(case):
                    continue
                key = (round_number, "jev", variant_id)
                planned += 1
                true_label = _case_label(case)
                if true_label is None:
                    edge += 1
                    continue
                probs = _probabilities(results.get(key))
                if probs is None:
                    invalid += 1
                    continue
                parsed = results[key].get("parsed")
                predicted = parsed.get("triage") if isinstance(parsed, dict) else None
                if predicted not in LABELS or probs[predicted] + 1e-9 < max(probs.values()):
                    invalid += 1
                    continue
                rows.append({"probabilities": probs, "true_label": true_label, "predicted": predicted,
                             "confidence": probs[predicted], "scenario_id": case.get("scenario_id", variant_id)})
            layers[layer] = _empty_metric(planned, edge, invalid, rows)
        block = {"round": round_number, "layers": layers}
        if round_number == 1:
            report["primary"] = block
        else:
            report["later_rounds"][str(round_number)] = block
    return report
