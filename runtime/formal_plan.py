"""Offline construction and verification of the first formal-round freeze."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import random
from typing import Any

from .adapters import payload, reservation

ROOT = Path(__file__).resolve().parents[1]
STUDY_ID = "jev_triage_20260922"
MODELS_CONFIG = ROOT / "configs" / "pilot_candidate.json"
CANDIDATE_DIR = ROOT / "data" / "processed" / "formal_candidate_v1"
CANDIDATE_MANIFEST = ROOT / "reports" / "formal_data_candidate_manifest.json"
PROTOCOL = ROOT / "研究方案.md"
ROLES = ("jev", "luna", "gemini", "deepseek")
FREEZE_FILES = ("models.json", "cases.jsonl", "jobs.jsonl", "rules.json", "protocol.md")


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonl(rows: list[dict]) -> str:
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)


def _load_candidate(root: Path) -> tuple[list[dict], dict]:
    candidate_dir = root / "data" / "processed" / "formal_candidate_v1"
    candidate_manifest = root / "reports" / "formal_data_candidate_manifest.json"
    rows = [json.loads(line) for line in (candidate_dir / "cases.jsonl").read_text().splitlines() if line]
    summary = json.loads(candidate_manifest.read_text())
    if file_hash(candidate_dir / "cases.jsonl") != summary.get("candidate_sha256"):
        raise ValueError("candidate_sha256_mismatch")
    if len(rows) != 1088 or len({r.get("variant_id") for r in rows}) != 1088:
        raise ValueError("candidate_count_or_uniqueness_mismatch")
    counts = {dataset: sum(r.get("dataset_id") == dataset for r in rows) for dataset in ("nm_main", "nm_emergency")}
    if counts != {"nm_main": 960, "nm_emergency": 128}:
        raise ValueError("candidate_cohort_counts_mismatch")
    if len({r.get("scenario_id") for r in rows}) != 34 or sum(bool(r.get("is_reference_version")) for r in rows) != 68:
        raise ValueError("candidate_reference_counts_mismatch")
    if any(r.get("split") != "test" or not isinstance(r.get("case_text"), str) or not r["case_text"] for r in rows):
        raise ValueError("candidate_row_schema_mismatch")
    return rows, summary


def _load_models(root: Path) -> list[dict]:
    config = json.loads((root / "configs" / "pilot_candidate.json").read_text())
    models = config.get("models")
    if not isinstance(models, list) or {m.get("role") for m in models} != set(ROLES) or len(models) != 4:
        raise ValueError("model_roster_mismatch")
    expected = {
        "jev": ("typesafe/jev-1.13", "https://openrouter.ai/api/v1/systemone"),
        "luna": ("openai/gpt-5.6-luna", "https://openrouter.ai/api/v1/chat/completions"),
        "gemini": ("google/gemini-3.1-flash-lite", "https://openrouter.ai/api/v1/chat/completions"),
        "deepseek": ("deepseek-flash", "https://api.deepseek.com/chat/completions"),
    }
    for model in models:
        role = model.get("role")
        if (model.get("model_requested"), model.get("endpoint")) != expected[role]:
            raise ValueError("endpoint_or_model_not_authorized")
    return sorted(models, key=lambda m: ROLES.index(m["role"]))


def _rules() -> dict:
    return {
        "condition": "structured",
        "primary_subset": {"dataset_id": "nm_main", "label_type": "clear", "repeat_id": 1},
        "main_comparison": "Jev vs Luna",
        "secondary_comparisons": ["Jev vs Gemini", "Jev vs DeepSeek"],
        "scenario_bootstrap": {"unit": "scenario_id", "replicates": 5000, "seed": 20260922},
        "emergency": {"dataset_id": "nm_emergency", "analysis": "independent_supplement"},
        "sensitivity": {"flagged_rows": 176, "mask_field": "ai_source_review_flag",
                        "mask": "same frozen mask for all models", "primary": "retain full data",
                        "interpretation": "exploratory AI-flagged exclusion, not new gold or clinical validation"},
        "paired_comparison": "common-valid model pairs for reference-label accuracy; all planned requests for valid-correct yield",
        "information_version_contrast": "only complete objective/subjective pairs; report lost-pair denominators",
        "scenario_weighting": "equal scenario weights after averaging within each scenario",
        "invalid_decisions": "retain in planned denominator; never invent a clinical grade",
        "source_labels": "unchanged acceptable sets; C/D accepts either; explicit D-to-nonD separate",
        "secondary_multiplicity": "Holm correction for the two secondary model comparisons",
        "first_attempt_policy": "one attempt per job; no automatic retries or reruns to obtain a valid answer",
        "clinical_validation": "not_human_validated",
        "no_post_result_prompt_or_model_changes": True,
    }


def build_freeze(destination: Path, root: Path = ROOT) -> dict:
    """Create an immutable offline freeze directory and return its manifest."""
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError("freeze_destination_not_empty")
    destination.mkdir(parents=True, exist_ok=True)
    rows, source_summary = _load_candidate(root)
    models = _load_models(root)
    models_by_role = {m["role"]: m for m in models}
    cases_by_id = {r["variant_id"]: r for r in rows}
    model_rows = [{k: v for k, v in m.items() if k != "credential_env"} for m in models]
    (destination / "models.json").write_text(json.dumps(model_rows, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    (destination / "cases.jsonl").write_text(_jsonl(rows))
    rules = _rules()
    (destination / "rules.json").write_text(json.dumps(rules, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    (destination / "protocol.md").write_text((root / "研究方案.md").read_text())

    jobs = []
    for row in rows:
        for role in ROLES:
            body = payload(role, row)
            request_hash = digest(body)
            job_key = digest({"role": role, "variant_id": row["variant_id"], "condition": "structured",
                              "repeat_id": 1, "request_hash": request_hash})
            jobs.append({"key": job_key, "role": role, "variant_id": row["variant_id"],
                         "condition": "structured", "repeat_id": 1, "request_hash": request_hash,
                         "reserved_usd": str(reservation(role, body))})
    random.Random(20260922).shuffle(jobs)
    (destination / "jobs.jsonl").write_text(_jsonl(jobs))
    total = sum((Decimal(j["reserved_usd"]) for j in jobs), Decimal(0))
    manifest = {
        "study_id": STUDY_ID, "schema_version": 1, "n_jobs": len(jobs),
        "max_output_tokens": 4096, "total_timeout_seconds": 75,
        "socket_timeout_seconds": 50, "connect_timeout_seconds": 10,
        "per_model_concurrency": 1, "max_attempts_per_job": 1,
        "schedule_seed": 20260922,
        "source_candidate_sha256": source_summary["candidate_sha256"],
        "source_counts": {"rows": 1088, "scenarios": 34, "reference_versions": 68},
        "file_hashes": {}, "total_reservation_usd": str(total),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "software_hashes": {str(p.relative_to(root)): file_hash(p)
                            for pattern in ("runtime/*.py", "analysis/*.py") for p in root.glob(pattern)},
    }
    manifest["file_hashes"] = {name: file_hash(destination / name) for name in FREEZE_FILES}
    manifest["freeze_digest"] = digest(manifest)
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    return manifest


def load_freeze(path: Path):
    path = Path(path)
    manifest = json.loads((path / "manifest.json").read_text())
    if manifest.get("study_id") != STUDY_ID or manifest.get("schema_version") != 1:
        raise ValueError("freeze_manifest_identity_mismatch")
    expected_hashes = manifest.get("file_hashes")
    if set(expected_hashes or {}) != set(FREEZE_FILES):
        raise ValueError("freeze_file_hash_manifest_mismatch")
    for name, expected in expected_hashes.items():
        if file_hash(path / name) != expected:
            raise ValueError("freeze_file_hash_mismatch")
    check = dict(manifest)
    check.pop("freeze_digest", None)
    if manifest.get("freeze_digest") != digest(check):
        raise ValueError("freeze_digest_mismatch")
    models = json.loads((path / "models.json").read_text())
    cases = [json.loads(line) for line in (path / "cases.jsonl").read_text().splitlines() if line]
    jobs = [json.loads(line) for line in (path / "jobs.jsonl").read_text().splitlines() if line]
    if len(models) != 4 or {m.get("role") for m in models} != set(ROLES):
        raise ValueError("frozen_model_count_mismatch")
    repeat_id = manifest.get("repeat_id", 1)
    expected_jobs = manifest.get("n_jobs", 4352)
    if len(cases) != 1088 or len(jobs) != expected_jobs:
        raise ValueError("freeze_count_mismatch")
    cases_by_id = {r["variant_id"]: r for r in cases}
    if len(cases_by_id) != 1088 or len({j.get("key") for j in jobs}) != expected_jobs:
        raise ValueError("freeze_uniqueness_mismatch")
    model_by_role = {m["role"]: m for m in models}
    for job in jobs:
        if job.get("role") not in model_by_role or job.get("variant_id") not in cases_by_id or job.get("condition") != "structured" or job.get("repeat_id") != repeat_id:
            raise ValueError("invalid_frozen_job")
        body = payload(job["role"], cases_by_id[job["variant_id"]])
        if job.get("request_hash") != digest(body) or Decimal(job["reserved_usd"]) != reservation(job["role"], body):
            raise ValueError("frozen_job_recalculation_mismatch")
        expected_key = digest({"role": job["role"], "variant_id": job["variant_id"], "condition": "structured",
                               "repeat_id": repeat_id, "request_hash": job["request_hash"]})
        if job.get("key") != expected_key:
            raise ValueError("frozen_job_key_mismatch")
    return manifest, model_by_role, cases_by_id, jobs
