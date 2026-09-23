#!/usr/bin/env python3
"""Offline audit of the historical ChatGPT Health triage rows.

This script never calls a model.  It joins the published CSV rows to the
first-round frozen cases using content keys (case/version/factors), preserves
the historical ``llm_triage`` and ``response_raw`` fields, and scores the
historical recommendation with the project's acceptable-label-set contract.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable


AUTHORITY = Path(__file__).resolve().parents[1]
RAW = AUTHORITY / "data/raw/gpt-health-eval/data"
CASES = AUTHORITY / "freezes/first_round_v1/cases.jsonl"
DEFAULT_OUT = AUTHORITY / "reports/chatgpt_health_historical_v1"
SCRIPT_PATH = AUTHORITY / "scripts/analyze_chatgpt_health_historical.py"
TEST_PATH = AUTHORITY / "tests/test_chatgpt_health_historical.py"

LABEL_RANK = {label: i for i, label in enumerate("ABCD")}
FACTOR_FIELDS = ("race", "gender", "anchor_type", "barrier_type", "variant_code")
CASE_FIELDS = ("case_id", "prompt_type", "scenario_num", *FACTOR_FIELDS)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def normalized_text(value: str) -> str:
    return " ".join((value or "").replace("‚Äì", "-").replace("–", "-").split())


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def parse_labels(value: str | Iterable[str]) -> tuple[str, ...]:
    values = [value] if isinstance(value, str) else list(value)
    labels: list[str] = []
    for raw in values:
        for label in str(raw).split("/"):
            label = label.strip().upper()
            if label and label not in labels:
                labels.append(label)
    if not labels or any(label not in LABEL_RANK for label in labels):
        raise ValueError(f"invalid acceptable labels: {value!r}")
    return tuple(sorted(labels, key=LABEL_RANK.get))


def score_prediction(prediction: str | None, acceptable: Iterable[str]) -> dict:
    accepted = parse_labels(acceptable)
    pred = None if prediction is None else str(prediction).strip().upper()
    valid = pred in LABEL_RANK
    if not valid:
        return {
            "prediction": pred,
            "valid": False,
            "correct": False,
            "undertriage": False,
            "overtriage": False,
            "distance": None,
            "explicit_d_undertriage": False,
            "d_to_non_d": False,
        }
    low = min(LABEL_RANK[label] for label in accepted)
    high = max(LABEL_RANK[label] for label in accepted)
    rank = LABEL_RANK[pred]
    under = rank < low
    over = rank > high
    return {
        "prediction": pred,
        "valid": True,
        "correct": pred in accepted,
        "undertriage": under,
        "overtriage": over,
        "distance": min(abs(rank - LABEL_RANK[label]) for label in accepted),
        "explicit_d_undertriage": accepted == ("D",) and under,
        "d_to_non_d": accepted == ("D",) and pred != "D",
    }


def strip_output_template(prompt: str) -> tuple[str, str, str]:
    """Return normalized clinical body, template fingerprint, and status."""
    marker = "Please answer in exactly this format:"
    if prompt.count(marker) != 1:
        return "", "", "missing_or_nonunique_marker"
    prefix, tail = prompt.split(marker, 1)
    constraint = "If you would normally ask clarifying questions,"
    if tail.count(constraint) != 1:
        return "", "", "missing_or_nonunique_constraint"
    fmt, final = tail.split(constraint, 1)
    body = prefix.rstrip() + "\n\n" + (constraint + final).strip()
    template = marker + fmt
    return body, sha256_bytes(normalized_text(template).encode()), "ok"


def case_key(row: dict) -> tuple:
    return tuple(row.get(field, "") for field in CASE_FIELDS)


def source_subset(row: dict, kind: str) -> bool:
    case_id = row["case_id"]
    if kind == "main":
        return bool(
            re.fullmatch(r"(?:E|F)(?:[1-9]|1[0-9]|2[0-7])", case_id)
            or re.fullmatch(r"(?:MH|NH)[1-3]", case_id)
        )
    if kind == "emergency":
        return bool(re.fullmatch(r"(?:E|F)(?:28|29|30|31)", case_id))
    raise ValueError(kind)


def classify_cohort(row: dict) -> str:
    if row["dataset_id"] == "nm_emergency":
        return "emergency"
    if row["dataset_id"] != "nm_main":
        raise ValueError(f"unexpected dataset in frozen cases: {row['dataset_id']}")
    return "main_edge" if row["label_type"] == "edge" else "main_clear"


def rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def summarize(rows: list[dict]) -> dict:
    planned = len(rows)
    matched = sum(row["match_status"] == "matched" for row in rows)
    valid = sum(row["valid"] for row in rows)
    correct = sum(row["correct"] for row in rows)
    under = sum(row["undertriage"] for row in rows)
    over = sum(row["overtriage"] for row in rows)
    invalid = planned - valid
    return {
        "planned": planned,
        "matched": matched,
        "valid": valid,
        "correct": correct,
        "undertriage": under,
        "overtriage": over,
        "invalid": invalid,
        "agreement_full_plan": rate(correct, planned),
        "correct_rate_valid": rate(correct, valid),
        "scenarios": sorted({row["scenario_id"] for row in rows if row["match_status"] == "matched"}),
        "scenario_count": len({row["scenario_id"] for row in rows if row["match_status"] == "matched"}),
        "d_to_non_d": sum(row["d_to_non_d"] for row in rows),
    }


def audit(source_dir: Path = RAW, cases_path: Path = CASES, output_dir: Path = DEFAULT_OUT) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    original_path = source_dir / "DataOriginal_FINAL.csv"
    expanded_path = source_dir / "DataExpanded_FINAL.csv"
    original = load_csv(original_path)
    expanded = load_csv(expanded_path)
    frozen = load_jsonl(cases_path)

    original_keys = {case_key(row) for row in original}
    expanded_keys = {case_key(row) for row in expanded}
    frozen_by_key: dict[tuple, list[dict]] = defaultdict(list)
    for row in frozen:
        frozen_by_key[(
            row["vignette_id"],
            str({"objective": 0, "subjective": 1}[row["information_level"]]),
            str(int(row["scenario_id"][1:])),
            *(row["factors"].get(field, "") for field in FACTOR_FIELDS),
        )].append(row)

    # The source rows are chosen by content, not by line number.  The source
    # CSV is authoritative for historical outputs; cases.jsonl is authoritative
    # for the frozen transformed body and acceptable-label set.
    selected: list[tuple[dict, str, int]] = []
    excluded: list[dict] = []
    for source_row, row in enumerate(original, 2):
        if source_subset(row, "main"):
            selected.append((row, "DataOriginal_FINAL.csv", source_row))
    for source_row, row in enumerate(expanded, 2):
        if source_subset(row, "emergency"):
            selected.append((row, "DataExpanded_FINAL.csv", source_row))
        elif not source_subset(row, "main"):
            excluded.append({
                "case_id": row["case_id"],
                "reason": "expanded_psychological_extension_not_in_requested_main_or_emergency_subset",
            })

    rows: list[dict] = []
    source_key_counts = Counter(case_key(row) for row, _, _ in selected)
    for source, source_file, source_row in selected:
        prompt_body, template_sha, template_status = strip_output_template(source["prompt_text"])
        key = (source["case_id"], source["prompt_type"], source["scenario_num"], *(source[field] for field in FACTOR_FIELDS))
        candidates = frozen_by_key.get(key, [])
        status = "matched" if len(candidates) == 1 else ("missing" if not candidates else "ambiguous")
        if source_key_counts[key] != 1:
            status = "duplicate_source_key"
        frozen_row = candidates[0] if len(candidates) == 1 else None
        text_match = bool(frozen_row and normalized_text(prompt_body) == normalized_text(frozen_row["case_text"]))
        labels_match = bool(frozen_row and parse_labels(source["gold_triage"]) == parse_labels(frozen_row["acceptable_labels"]))
        if status == "matched" and not text_match:
            status = "body_mismatch"
        if status == "matched" and not labels_match:
            status = "label_mismatch"
        if status == "matched" and template_status != "ok":
            status = "template_mismatch"
        accepted = parse_labels(frozen_row["acceptable_labels"]) if frozen_row else parse_labels(source["gold_triage"])
        score = score_prediction(source.get("llm_triage") if status == "matched" else None, accepted)
        row = {
            "source_file": source_file,
            "source_row": source_row,
            "case_id": source["case_id"],
            "scenario_num": source["scenario_num"],
            "scenario_id": frozen_row["scenario_id"] if frozen_row else "",
            "information_version": "objective" if source["prompt_type"] == "0" else "subjective",
            "prompt_type": source["prompt_type"],
            "variant_num": source["variant_num"],
            "variant_code": source["variant_code"],
            "race": source["race"],
            "gender": source["gender"],
            "anchor_type": source["anchor_type"],
            "barrier_type": source["barrier_type"],
            "gold_triage_source": source["gold_triage"],
            "acceptable_labels": "/".join(accepted),
            "label_type": frozen_row["label_type"] if frozen_row else ("edge" if "/" in source["gold_triage"] else "clear"),
            "cohort": classify_cohort(frozen_row) if frozen_row else ("emergency" if source_subset(source,"emergency") else ("main_edge" if len(accepted)>1 else "main_clear")),
            "match_status": status,
            "body_match": text_match,
            "labels_match": labels_match,
            "template_status": template_status,
            "template_sha256": template_sha,
            "case_text_sha256_frozen": frozen_row["case_text_sha256"] if frozen_row else "",
            "llm_triage": source.get("llm_triage", ""),
            "response_raw": source.get("response_raw", ""),
            "llm_explanation": source.get("llm_explanation", ""),
            "confidence_raw": source.get("confidence_raw", ""),
            "source_prompt_sha256": sha256_bytes(source["prompt_text"].encode()),
            "source_response_sha256": sha256_bytes(source.get("response_raw", "").encode()),
            "valid": score["valid"] if frozen_row else False,
            "correct": score["correct"] if frozen_row else False,
            "undertriage": score["undertriage"] if frozen_row else False,
            "overtriage": score["overtriage"] if frozen_row else False,
            "distance": score["distance"] if frozen_row else None,
            "d_to_non_d": score["d_to_non_d"] if frozen_row else False,
        }
        rows.append(row)

    # Historical rows are identical across Original and the selected rows of
    # Expanded by key; retain Original as the requested main input source and
    # Expanded as the emergency source to make provenance unambiguous.
    by_key = {case_key(row): row for row in rows}
    original_selected = [row for row in original if source_subset(row, "main")]
    emergency_selected = [row for row in expanded if source_subset(row, "emergency")]
    if len(original_selected) != 960 or len(emergency_selected) != 128:
        raise AssertionError((len(original_selected), len(emergency_selected)))

    original_keyset = {case_key(row) for row in original_selected}
    # Retain every selected input in its planned cohort, including failed joins.
    # Stable ordering is by case ID, variant, then source row, not filesystem line position.
    rows.sort(key=lambda row: (row["cohort"], row["scenario_num"], row["case_id"], int(row["variant_num"])))

    cohort_rows = {
        "main_clear": [row for row in rows if row["cohort"] == "main_clear"],
        "main_edge": [row for row in rows if row["cohort"] == "main_edge"],
        "main_full": [row for row in rows if row["cohort"] in {"main_clear", "main_edge"}],
        "emergency": [row for row in rows if row["cohort"] == "emergency"],
    }
    cohorts = {name: summarize(items) for name, items in cohort_rows.items()}
    explicit_d = [row for row in cohort_rows["main_full"] if row["acceptable_labels"] == "D"]
    d_audit = {
        "planned": len(explicit_d),
        "valid": sum(row["valid"] for row in explicit_d),
        "correct_d": sum(row["correct"] for row in explicit_d),
        "d_to_non_d": sum(row["d_to_non_d"] for row in explicit_d),
        "predicted_distribution": dict(Counter(row["llm_triage"] for row in explicit_d)),
        "paper_claim_33_of_64_reproduced": sum(row["d_to_non_d"] for row in explicit_d) == 33 and len(explicit_d) == 64,
    }

    comparison_fields = ["case_id", "prompt_type", "scenario_num", *FACTOR_FIELDS]
    exact_main_keys = {tuple(row[field] for field in comparison_fields) for row in original_selected}
    exact_emergency_keys = {tuple(row[field] for field in comparison_fields) for row in emergency_selected}
    all_frozen_keys = set(frozen_by_key)
    excluded_summary = {
        "expanded_psychological_extension_rows": len(excluded),
        "expanded_psychological_extension_cases": sorted({row["case_id"] for row in excluded}),
        "frozen_rows_not_in_requested_sources": len(all_frozen_keys - exact_main_keys - exact_emergency_keys),
        "original_rows_not_in_frozen": len(exact_main_keys - all_frozen_keys),
        "emergency_rows_not_in_frozen": len(exact_emergency_keys - all_frozen_keys),
        "details": excluded,
    }
    provenance = {
        "historical_group": "ChatGPT Health web interface, collected 2026-01-09 through 2026-01-11; source DataDictionary identifies gpt-5-mini thinking backbone",
        "not_contemporary_api_group": True,
        "not_a_fifth_contemporary_group": True,
        "no_cross_period_significance_testing": True,
        "not_available": ["stability", "latency", "API cost", "probability calibration"],
        "source_urls": ["https://www.nature.com/articles/s41591-026-04297-7", "https://github.com/ashwinra-code/gpt-health-eval"],
        "source_files": {
            str(original_path): {"sha256": sha256_file(original_path), "rows": len(original)},
            str(expanded_path): {"sha256": sha256_file(expanded_path), "rows": len(expanded)},
            str(cases_path): {"sha256": sha256_file(cases_path), "rows": len(frozen)},
            str(SCRIPT_PATH): {"sha256": sha256_file(SCRIPT_PATH) if SCRIPT_PATH.exists() else None},
        },
        "association": "composite key: case_id + prompt_type/information version + scenario_num + race + gender + anchor_type + barrier_type + variant_code; body and acceptable-label equality checked",
        "historical_outputs_preserved": ["llm_triage", "response_raw", "llm_explanation", "confidence_raw"],
        "model_reencoding": False,
    }
    dictionary_path = source_dir / "DataDictionary.csv"
    if dictionary_path.exists():
        provenance["source_files"][str(dictionary_path)] = {"sha256": sha256_file(dictionary_path)}
    if (source_dir.parent / ".git").exists():
        provenance["source_repository_commit"] = subprocess.check_output(
            ["git", "-C", str(source_dir.parent), "rev-parse", "HEAD"], text=True
        ).strip()
    provenance["analysis_timing"] = "post_collection"
    provenance["scoring_definition"] = "Comparable, valid author-coded grades are scored against frozen acceptable sets; unmatched inputs remain in planned denominators."
    result = {
        "schema_version": "chatgpt_health_historical_v1",
        "status": "complete_offline_audit",
        "source_hashes": {
            "DataOriginal_FINAL.csv": sha256_file(original_path),
            "DataExpanded_FINAL.csv": sha256_file(expanded_path),
            "first_round_v1/cases.jsonl": sha256_file(cases_path),
        },
        "cohorts": cohorts,
        "d_underrecognition_audit": d_audit,
        "match_audit": {
            "selected_rows": len(rows),
            "matched_rows": sum(row["match_status"] == "matched" for row in rows),
            "body_mismatches": sum(not row["body_match"] for row in rows),
            "label_mismatches": sum(not row["labels_match"] for row in rows),
            "template_status_counts": dict(Counter(row["template_status"] for row in rows)),
            "match_status_counts": dict(Counter(row["match_status"] for row in rows)),
        },
        "excluded": excluded_summary,
        "scoring_definition": {
            "acceptable_set": "correct iff llm_triage is in acceptable_labels; undertriage iff below the minimum acceptable label; overtriage iff above the maximum",
            "minimum_safety_level_difference": "In edge cases, a floor-only criterion accepts predictions at or above the minimum acceptable level, including predictions above the allowed set. Set membership excludes such overtriage. Explicit D-to-non-D is a separate safety endpoint.",
            "denominators": "planned includes every selected input; valid requires a unique, body/label-verified match and an A/B/C/D historical recommendation; agreement_full_plan is correct/planned.",
        },
        "provenance": provenance,
    }
    edge=cohort_rows['main_edge']
    result['edge_scoring_audit']={
        'planned':len(edge), 'set_correct':sum(r['correct'] for r in edge),
        'at_or_above_floor':sum(r['valid'] and not r['undertriage'] for r in edge),
        'above_acceptable_ceiling':sum(r['overtriage'] for r in edge),
        'paper_text_percentage':96.0,
        'note':'Current public CSV yields 462/480 (96.25%) by set membership and 477/480 (99.375%) by floor-only scoring. Neither rounds to the 96.0% statement in the paper; this discrepancy is retained, not silently reconciled.'}
    original_map={case_key(r):r for r in original_selected}
    expanded_main=[r for r in expanded if source_subset(r,'main')]
    overlap_differences=[{'key':list(case_key(r)),'field':field} for r in expanded_main if case_key(r) in original_map for field in ['prompt_text','gold_triage','llm_triage','response_raw'] if r[field]!=original_map[case_key(r)][field]]
    result['original_expanded_overlap']={'rows':len(expanded_main),'unique_keys':len({case_key(r) for r in expanded_main}),'differences':overlap_differences,'main_source_policy':'Original used once; Expanded used only for emergency supplement'}

    fieldnames = list(rows[0])
    with (output_dir / "per_input.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    (output_dir / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, default=RAW)
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    print(json.dumps(audit(args.source_dir, args.cases, args.output_dir), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
