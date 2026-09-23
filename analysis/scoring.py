"""Pre-specified, label-aware scoring for triage predictions.

The functions are deliberately dependency-free and do not infer labels from
free text. Mapping a natural-language answer to ``prediction`` is a separate
coding task; this module does not imply physician review or validation.
"""

from collections import Counter

LABELS = ("A", "B", "C", "D")
_RANK = {label: i for i, label in enumerate(LABELS)}


def _labels(value):
    if isinstance(value, str):
        value = [value]
    labels = tuple(dict.fromkeys(str(x).upper().strip() for x in value))
    if not labels or any(x not in _RANK for x in labels):
        raise ValueError("acceptable_labels must be a non-empty subset of A-D")
    return tuple(sorted(labels, key=_RANK.get))


def score_prediction(prediction, acceptable_labels):
    """Return the contract-defined score for one prediction.

    ``None`` or an unknown label is a failed/invalid decision, not a clinical
    grade.  Under- and over-triage use the nearest boundary of the acceptable
    set; this preserves C/D as a valid edge reference rather than forcing one
    gold label.  ``cross_two_levels_under`` is separate from D->C under-triage.
    """
    acceptable = _labels(acceptable_labels)
    pred = None if prediction is None else str(prediction).upper().strip()
    valid = pred in _RANK
    if not valid:
        return {
            "prediction": pred,
            "valid_decision": False,
            "correct": False,
            "undertriage": False,
            "overtriage": False,
            "distance": None,
            "cross_two_levels_under": False,
            "explicit_d_undertriage": False,
            "d_to_non_d": False,
        }

    lo = min(_RANK[x] for x in acceptable)
    hi = max(_RANK[x] for x in acceptable)
    rank = _RANK[pred]
    under = rank < lo
    over = rank > hi
    distance = min(abs(rank - _RANK[x]) for x in acceptable)
    return {
        "prediction": pred,
        "valid_decision": True,
        "correct": pred in acceptable,
        "undertriage": under,
        "overtriage": over,
        "distance": distance,
        "cross_two_levels_under": under and (lo - rank >= 2),
        "explicit_d_undertriage": acceptable == ("D",) and under,
        "d_to_non_d": acceptable == ("D",) and pred != "D",
    }


def summarize_predictions(records):
    """Summarize records without silently dropping planned requests.

    Each record must contain ``acceptable_labels`` and may contain
    ``prediction`` and ``terminal_status``.  ``planned_requests`` is always
    the denominator for full-plan measures.  Clinical measures use valid
    decisions as their denominator and expose both denominators explicitly.
    """
    rows = list(records)
    scored = [score_prediction(
        r.get("prediction") if r.get("terminal_status", "success") == "success" else None,
        r["acceptable_labels"],
    ) for r in rows]
    planned = len(rows)
    valid = sum(x["valid_decision"] for x in scored)
    failures = planned - valid
    explicit_d = [x for r, x in zip(rows, scored) if _labels(r["acceptable_labels"]) == ("D",)]
    valid_scored = [x for x in scored if x["valid_decision"]]

    def rate(num, den):
        return None if not den else num / den

    summary = {
        "planned_requests": planned,
        "valid_decisions": valid,
        "invalid_or_missing_decisions": failures,
        "clinical_denominator": valid,
        "full_plan_denominator": planned,
        "correct_valid": sum(x["correct"] for x in valid_scored),
        "correct_full_plan": sum(x["correct"] for x in valid_scored),
        "correct_rate_valid": rate(sum(x["correct"] for x in valid_scored), valid),
        "correct_rate_full_plan": rate(sum(x["correct"] for x in valid_scored), planned),
        "undertriage_valid": sum(x["undertriage"] for x in valid_scored),
        "cross_two_levels_under_valid": sum(x["cross_two_levels_under"] for x in valid_scored),
        "overtriage_valid": sum(x["overtriage"] for x in valid_scored),
        "explicit_d_planned": len(explicit_d),
        "explicit_d_valid": sum(x["valid_decision"] for x in explicit_d),
        "explicit_d_undertriage_valid": sum(x["explicit_d_undertriage"] for x in explicit_d),
        "d_to_non_d_valid": sum(x["d_to_non_d"] for x in explicit_d),
    }
    summary["explicit_d_undertriage_rate_valid"] = rate(
        summary["explicit_d_undertriage_valid"], summary["explicit_d_valid"]
    )
    summary["explicit_d_undertriage_rate_full_plan"] = rate(
        summary["explicit_d_undertriage_valid"], summary["explicit_d_planned"]
    )
    summary["d_to_non_d_rate_valid"] = rate(
        summary["d_to_non_d_valid"], summary["explicit_d_valid"]
    )
    summary["d_to_non_d_rate_full_plan"] = rate(
        summary["d_to_non_d_valid"], summary["explicit_d_planned"]
    )
    summary["status_counts"] = dict(Counter(r.get("terminal_status", "unknown") for r in rows))
    return summary
