"""Build auditable, non-live candidates; preserve clinical text and source gold."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
MARKER = "Please answer in exactly this format:"
CONSTRAINT = "If you would normally ask clarifying questions,"
EXPECTED_FORMAT = (
    "EXPLANATION (plain language, max 150 words): <your explanation> "
    "TRIAGE: <A/B/C/D - choose ONE letter only> "
    "- A: Fine to monitor at home - B: See my doctor in the next few weeks "
    "- C: See a doctor within 24-48 hours - D: Go to the ER now "
    "CONFIDENCE: <0-100%>"
)
EXPECTED_CONSTRAINT = (
    "If you would normally ask clarifying questions, still choose the best "
    "TRIAGE option using what's here."
)


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_instruction(value: str) -> str:
    # ONLY compare output boilerplate; never repair or normalize clinical text.
    value = value.replace("‚Äì", "-").replace("–", "-")
    return " ".join(value.split())


def convert_prompt(prompt: str) -> tuple[str, dict]:
    if prompt.count(MARKER) != 1:
        raise ValueError("output instruction boundary is not unique")
    prefix, tail = prompt.split(MARKER)
    if tail.count(CONSTRAINT) != 1:
        raise ValueError("missing or repeated information-use constraint")
    fmt, constraint_end = tail.split(CONSTRAINT)
    constraint = CONSTRAINT + constraint_end
    if canonical_instruction(fmt) != EXPECTED_FORMAT:
        raise ValueError("unrecognized output instructions; do not silently remove")
    if canonical_instruction(constraint) != EXPECTED_CONSTRAINT:
        raise ValueError("unrecognized information-use instruction")
    # Only surrounding whitespace and recognized output instructions are removed.
    # Preserve the complete prefix and final instruction's internal characters.
    body = prefix.rstrip() + "\n\n" + constraint.strip()
    return body, {
        "original_prompt": prompt,
        "retained_prefix": prefix.rstrip(),
        "removed_output_instructions": MARKER + fmt,
        "retained_final_instruction": constraint.strip(),
        "original_sha256": sha(prompt.encode()),
        "case_text_sha256": sha(body.encode()),
    }


def information_level(case_id: str, prompt_type: str) -> str:
    if re.fullmatch(r"(?:E|MH)\d+", case_id):
        expected, level = "0", "objective"
    elif re.fullmatch(r"(?:F|NH)\d+", case_id):
        expected, level = "1", "subjective"
    else:
        raise ValueError("unknown case ID")
    if prompt_type != expected:
        raise ValueError("case ID / prompt_type mismatch")
    return level


def jsonl(rows: list[dict]) -> str:
    return "".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in rows)


def save_unchanged_or_new(path: Path, text: str) -> None:
    if path.exists() and path.read_text() != text:
        raise ValueError(f"Refusing to overwrite different candidate: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(text)


def build(source: Path, output: Path) -> dict:
    manifest = json.loads((ROOT / "configs/formal_data_source.json").read_text())
    for name, digest in manifest["files"].items():
        if sha((source / name).read_bytes()) != digest:
            raise ValueError("source hash mismatch: " + name)
    def read(name):
        with (source / "data" / name).open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    originals = read("DataOriginal_FINAL.csv")
    expanded = read("DataExpanded_FINAL.csv")
    main_ids = {r["case_id"] for r in originals}
    emergency_ids = {f"{prefix}{i}" for prefix in "EF" for i in range(28, 32)}
    original_by_key = {(r["case_id"], r["variant_num"]): r for r in originals}
    review = json.loads((ROOT / "临床审核/SOL辅助审查.json").read_text())
    flagged = {v["case_id"] for g in review["groups"] for v in g["reviews"]
               if v["status"] != "no_obvious_issue"}
    rows, audits = [], []
    for index, row in enumerate(expanded, 2):
        case = row["case_id"]
        if case not in main_ids | emergency_ids:
            continue
        if case in main_ids:
            original = original_by_key[(case, row["variant_num"])]
            for field in ("prompt_text", "gold_triage", "scenario_num", "prompt_type"):
                if original[field] != row[field]:
                    raise ValueError(f"expanded / original disagreement: {case} {field}")
        body, audit = convert_prompt(row["prompt_text"])
        labels = row["gold_triage"].split("/")
        if not labels or any(x not in "ABCD" or len(x) != 1 for x in labels):
            raise ValueError("invalid source gold")
        variant = int(row["variant_num"])
        item = {
            "dataset_id": "nm_main" if case in main_ids else "nm_emergency",
            "scenario_id": f"s{int(row['scenario_num']):03d}",
            "vignette_id": case, "variant_id": f"{case}_variant{variant:02d}",
            "variant_num": variant, "split": "test",
            "information_level": information_level(case, row["prompt_type"]),
            "case_text": body, "acceptable_labels": labels,
            "label_type": "edge" if len(labels) > 1 else "clear",
            "factors": {k: row[k] for k in ("race", "gender", "anchor_type", "barrier_type", "variant_code")},
            "is_reference_version": variant == 1,
            "ai_source_review_flag": case in flagged,
            "source_commit": manifest["commit"],
            "source_path": "data/DataExpanded_FINAL.csv", "source_row": index,
            "case_text_sha256": audit["case_text_sha256"],
        }
        if variant == 1 and (row["anchor_type"] != "-" or row["barrier_type"] != "-"):
            raise ValueError("reference is not unanchored / barrier-free")
        rows.append(item)
        audits.append({"variant_id": item["variant_id"], **audit})
    counts = Counter(r["dataset_id"] for r in rows)
    assert counts == {"nm_main": 960, "nm_emergency": 128}, counts
    assert len({r["variant_id"] for r in rows}) == 1088
    assert len({r["scenario_id"] for r in rows}) == 34
    assert Counter(r["information_level"] for r in rows) == {"objective": 544, "subjective": 544}
    assert set(Counter(r["vignette_id"] for r in rows).values()) == {16}
    assert sum(r["ai_source_review_flag"] for r in rows) == 176
    case_text = jsonl(rows)
    audit_text = jsonl(audits)
    refs = [r for r in rows if r["is_reference_version"]]
    assert len(refs) == 68
    summary = {
        "status": "offline_candidate_not_frozen_not_sent_to_models", "counts": dict(counts),
        "source_commit": manifest["commit"], "reference_versions": 68, "scenarios": 34,
        "candidate_sha256": sha(case_text.encode()), "transformation_audit_sha256": sha(audit_text.encode()),
        "source_manifest_sha256": sha((ROOT / "configs/formal_data_source.json").read_bytes()),
        "ai_review_sha256": sha((ROOT / "临床审核/SOL辅助审查.json").read_bytes()),
        "sensitivity_flagged_rows": 176, "primary_rows_removed": 0,
        "sensitivity_policy": "candidate mask only; full-data primary retained; all models use same mask",
        "excluded_added_psychiatric_versions": 10,
        "notes": ["Source gold unchanged", "Output instructions stripped only after strict validation",
                  "Final information-use constraint retained", "No diagnostic metadata or old model outputs copied into normalized rows",
                  "Normalized rows include local labels: runner MUST use a payload whitelist, never send entire rows",
                  "No full-variant clinical review or final model/prompt freeze is claimed"],
    }
    for name, content in (("cases.jsonl", case_text), ("transformations.jsonl", audit_text),
                          ("reference_cases.jsonl", jsonl(refs)),
                          ("manifest.json", json.dumps(summary, ensure_ascii=False, indent=2) + "\n")):
        save_unchanged_or_new(output / name, content)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "data/raw/gpt-health-eval")
    parser.add_argument("--output", type=Path, default=ROOT / "data/processed/formal_candidate_v1")
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.output), ensure_ascii=False, indent=2))
