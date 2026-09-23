"""Bounded, read-only factor-pair analysis.

This module intentionally works on normalized case/result dictionaries and never
includes case text in its output.  Pair construction is exact: one factor must
change, while the scenario, cohort and every other matching field agree.
"""

from collections import defaultdict

LABELS = ("A", "B", "C", "D")
FACTORS = ("anchor_type", "barrier_type", "gender", "race")
ROLES = ("jev", "luna", "gemini", "deepseek")


def _field(case, name, default=None):
    factors = case.get("factors") or {}
    return factors.get(name, case.get(name, default))


def _cohort(case):
    value = case.get("dataset_id", case.get("cohort"))
    text = str(value or "").lower()
    if "emergency" in text or text in {"em", "ed"}:
        return "emergency"
    if "main" in text:
        return "main"
    return value or "unknown"


def _scenario(case):
    return case.get("scenario_id", case.get("scenario", case.get("vignette_id")))


def _level(factor, value):
    value = "" if value is None else str(value)
    if factor in ("anchor_type", "barrier_type"):
        return value
    if factor == "gender":
        return {"man": "man", "woman": "woman"}.get(value.lower(), value)
    if factor == "race":
        return {"white": "White", "black": "Black"}.get(value.lower(), value)
    return value


def _orient(factor, value):
    """Return (is_baseline, oriented target label), or None."""
    level = _level(factor, value)
    if factor in ("anchor_type", "barrier_type"):
        return (level == "-", None)
    if factor == "gender":
        return (level == "man", "woman")
    if factor == "race":
        return (level == "White", "Black")
    return (level == "subjective", "objective")


def _factor_values(case):
    return {f: _level(f, _field(case, f)) for f in FACTORS}


def _all_factor_values(case):
    values = dict(case.get("factors") or {})
    for key in ("anchor_type", "barrier_type", "gender", "race", "variant_code"):
        if key in case:
            values.setdefault(key, case[key])
    return {key: _level(key, value) for key, value in values.items() if key != "variant_code"}


def _same_except(a, b, omitted):
    if _scenario(a) != _scenario(b) or _cohort(a) != _cohort(b):
        return False
    if omitted != "information_level" and a.get("information_level") != b.get("information_level"):
        return False
    left, right = _all_factor_values(a), _all_factor_values(b)
    for factor in set(left) | set(right):
        if factor != omitted and left.get(factor) != right.get(factor):
            return False
    # variant_code is deliberately absent: it is an implementation code, not a factor.
    return True


def _prediction(result):
    if not isinstance(result, dict) or result.get("status") != "success":
        return None, "invalid"
    parsed = result.get("parsed")
    value = parsed.get("triage") if isinstance(parsed, dict) else None
    value = value.strip() if isinstance(value, str) else value
    return (value, "valid") if value in LABELS else (None, "invalid")


def _endpoint(case, result):
    prediction, status = _prediction(result)
    gold = case.get("acceptable_labels", case.get("gold", []))
    if isinstance(gold, str):
        gold = [x.strip() for x in gold.split("/") if x.strip()]
    gold = list(gold or [])
    correct = status == "valid" and prediction in gold
    return {"id": case.get("variant_id"), "status": status,
            "terminal_status": result.get("status") if isinstance(result, dict) else None,
            "prediction": prediction,
            "gold": gold, "correct": correct}


def _candidate_pairs(cases, factor, issues):
    """Construct oriented baseline -> changed pairs, rejecting ambiguity."""
    rows = list(cases.values())
    if factor == "information_level":
        values = lambda c: str(c.get("information_level", ""))
    else:
        values = lambda c: _level(factor, _field(c, factor))
    pairs, ambiguous = [], False
    for base in rows:
        oriented = _orient(factor, values(base))
        if not oriented or not oriented[0]:
            continue
        if factor in ("anchor_type", "barrier_type"):
            target_levels = sorted({values(c) for c in rows
                                    if values(c) != "-" and _scenario(c) == _scenario(base)
                                    and _cohort(c) == _cohort(base)
                                    and c.get("information_level") == base.get("information_level")})
        else:
            target_levels = [oriented[1]]
        for target_level in target_levels:
            candidates = [c for c in rows if c is not base and values(c) == target_level
                          and _same_except(base, c, factor)]
            if len(candidates) > 1:
                issues.append({"type": "nonunique_match", "factor": factor,
                               "baseline_id": base.get("variant_id"),
                               "target_level": target_level,
                               "candidate_ids": sorted(c.get("variant_id") for c in candidates)})
                ambiguous = True
                continue
            if not candidates:
                issues.append({"type": "unmatched_baseline", "factor": factor,
                               "baseline_id": base.get("variant_id"),
                               "target_level": target_level, "scenario_id": _scenario(base)})
                continue
            target = candidates[0]
            pairs.append({"factor": factor, "cohort": _cohort(base), "scenario_id": _scenario(base),
                          "baseline": base, "changed": target,
                          "baseline_level": _level(factor, values(base)),
                          "changed_level": target_level})
    # A target reused by multiple baseline slots is equally ambiguous, even if
    # each individual baseline had only one candidate.
    targets = defaultdict(list)
    for pair in pairs:
        targets[pair["changed"].get("variant_id")].append(pair)
    for target_id, using in targets.items():
        if len(using) > 1:
            issues.append({"type": "nonunique_match", "factor": factor,
                           "target_id": target_id,
                           "baseline_ids": sorted(p["baseline"].get("variant_id") for p in using)})
            ambiguous = True
    if ambiguous:
        return []
    # Record target-side gaps only where a matching baseline stratum exists.
    for target in rows:
        if factor in ("anchor_type", "barrier_type"):
            is_target = values(target) != "-"
        else:
            is_target = values(target) == ("objective" if factor == "information_level" else
                                           "woman" if factor == "gender" else "Black")
        if not is_target:
            continue
        baselines = [b for b in rows if _same_except(b, target, factor)
                     and _orient(factor, values(b))[0]]
        if not baselines:
            issues.append({"type": "unmatched_target", "factor": factor,
                           "target_id": target.get("variant_id"), "scenario_id": _scenario(target)})
    return pairs


def analyze_factors(cases: dict, results: dict) -> dict:
    """Analyze matched offline factor pairs.

    ``cases`` maps variant id to normalized case dictionaries. ``results`` maps
    ``(round, role, variant_id)`` to result dictionaries.  The return value has
    ``summary``, ``pairs`` and ``issues`` lists and contains no clinical text.
    """
    issues = []
    all_pairs = []
    for factor in (*FACTORS, "information_level"):
        all_pairs.extend(_candidate_pairs(cases, factor, issues))

    observed = {(key[0], key[1]) for key in results if isinstance(key, tuple) and len(key) == 3
                and key[0] in (1, 2, 3) and key[1] in ROLES}
    pairs_out, summary = [], []
    for rnd, role in sorted(observed):
        for spec in all_pairs:
            base, changed = spec["baseline"], spec["changed"]
            base_key = (rnd, role, base.get("variant_id"))
            changed_key = (rnd, role, changed.get("variant_id"))
            b_result, c_result = results.get(base_key), results.get(changed_key)
            b = _endpoint(base, b_result)
            c = _endpoint(changed, c_result)
            flagged = bool(base.get("ai_source_review_flag")) or bool(changed.get("ai_source_review_flag"))
            row = {"round": rnd, "role": role, "cohort": spec["cohort"],
                   "factor": spec["factor"], "scenario_id": spec["scenario_id"],
                   "baseline_id": b["id"], "changed_id": c["id"],
                   "baseline_variant_id": b["id"], "changed_variant_id": c["id"],
                   "baseline_level": spec["baseline_level"], "changed_level": spec["changed_level"],
                   "sensitivity_excluded": flagged, "selected": not flagged,
                   "baseline_status": b["status"], "changed_status": c["status"],
                   "baseline_terminal_status": b["terminal_status"],
                   "changed_terminal_status": c["terminal_status"],
                   "baseline_missing": b_result is None, "changed_missing": c_result is None,
                   "baseline_prediction": b["prediction"], "changed_prediction": c["prediction"],
                   "baseline_gold": b["gold"], "changed_gold": c["gold"],
                   "baseline_correct": b["correct"], "changed_correct": c["correct"]}
            row["valid_pair"] = b["status"] == c["status"] == "valid"
            row["changed_prediction"] = c["prediction"]
            row["prediction_changed"] = None
            row["lowered"] = None
            row["correct_to_wrong"] = None
            if row["valid_pair"]:
                row["prediction_changed"] = b["prediction"] != c["prediction"]
                row["lowered"] = LABELS.index(c["prediction"]) < LABELS.index(b["prediction"])
                row["correct_to_wrong"] = b["correct"] and not c["correct"]
            row["missing_pair"] = b_result is None or c_result is None
            pairs_out.append(row)

    groups = defaultdict(list)
    for row in pairs_out:
        groups[(row["round"], row["role"], row["cohort"], row["factor"],
                row["baseline_level"], row["changed_level"])].append(row)
    for key, rows in sorted(groups.items()):
        for mask in ("all", "exclude_ai_flags"):
            planned = len(rows)
            selected_rows = rows if mask == "all" else [r for r in rows if r["selected"]]
            valid_rows = [r for r in selected_rows if r["valid_pair"]]
            def rate(n, d): return n / d if d else None
            summary.append({"round": key[0], "role": key[1], "cohort": key[2], "factor": key[3],
                        "factor_label": ("race (descriptive: White/unspecified -> explicit Black)"
                                          if key[3] == "race" else key[3]),
                        "baseline_level": key[4], "changed_level": key[5],
                        "mask": mask, "planned_pairs": planned,
                        "lost_selection_pairs": planned - len(selected_rows),
                        "selected_pairs": len(selected_rows), "valid_pairs": len(valid_rows),
                        "invalid_pairs": len(selected_rows) - len(valid_rows),
                        "missing_pairs": sum(r["missing_pair"] for r in selected_rows),
                        "prediction_changed": sum(bool(r["prediction_changed"]) for r in valid_rows),
                        "lowered": sum(bool(r["lowered"]) for r in valid_rows),
                        "correct_to_wrong": sum(bool(r["correct_to_wrong"]) for r in valid_rows),
                        "changed_rate": rate(sum(bool(r["prediction_changed"]) for r in valid_rows), len(valid_rows)),
                        "lowered_rate": rate(sum(bool(r["lowered"]) for r in valid_rows), len(valid_rows)),
                        "correct_to_wrong_rate": rate(sum(bool(r["correct_to_wrong"]) for r in valid_rows), len(valid_rows)),
                        "baseline_correct": sum(r["baseline_correct"] for r in valid_rows),
                        "changed_correct": sum(r["changed_correct"] for r in valid_rows),
                        "baseline_accuracy": rate(sum(r["baseline_correct"] for r in valid_rows), len(valid_rows)),
                        "changed_accuracy": rate(sum(r["changed_correct"] for r in valid_rows), len(valid_rows)),
                        "accuracy_difference": rate(sum(r["changed_correct"] for r in valid_rows)
                                                     - sum(r["baseline_correct"] for r in valid_rows), len(valid_rows))})
    return {"summary": summary, "pairs": pairs_out, "issues": issues}
