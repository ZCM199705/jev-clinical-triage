"""Offline audit and paired analysis for the explanation JSON condition.

This module deliberately reads frozen evidence only.  It does not change a
freeze, the project ledger, or any existing report.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from collections import Counter
from decimal import Decimal
from pathlib import Path

from analysis.paired import paired_cluster_bootstrap
from analysis.scoring import score_prediction
from runtime.format_control import payload, reservation
from runtime.formal_plan import load_freeze
from runtime.adapters import response_cost, validate_identity

ROOT = Path(__file__).resolve().parents[1]
ROLES = ("luna", "gemini", "deepseek")
ROUND_NAMES = ("baseline_round1", "baseline_round2", "baseline_round3")
FORMAT_CONDITION = "explain_options"
BOOTSTRAP_ITERATIONS = 5000
SEED = 20260922


def _canon(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _digest(value):
    return hashlib.sha256(_canon(value)).hexdigest()


def _raw_json(evidence):
    raw = evidence.get("raw_response")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _format_parse(evidence):
    """Parse the provider evidence while keeping schema and decision separate."""
    raw = _raw_json(evidence)
    out = {
        "raw_json": raw is not None,
        "finish_stop": False,
        "refusal_absent": False,
        "content_nonempty": False,
        "content_json": False,
        "decision_valid": False,
        "exact_schema": False,
        "extra_fields": False,
        "prediction": None,
        "explanation_valid": False,
        "identity_present": False,
        "cost_present": False,
    }
    if not raw:
        return out
    out["identity_present"] = isinstance(raw.get("model"), str) and bool(raw.get("model"))
    usage = raw.get("usage")
    out["cost_present"] = isinstance(usage, dict) and isinstance(usage.get("cost"), (int, float))
    choices = raw.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
    message = choice.get("message") if choice else None
    message = message if isinstance(message, dict) else {}
    out["finish_stop"] = choice.get("finish_reason") == "stop" if choice else False
    out["refusal_absent"] = message.get("refusal") in (None, "")
    content = message.get("content")
    out["content_nonempty"] = isinstance(content, str) and bool(content.strip())
    parsed = None
    if out["content_nonempty"]:
        try:
            parsed = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            parsed = None
    out["content_json"] = isinstance(parsed, dict)
    if isinstance(parsed, dict):
        keys = set(parsed)
        out["exact_schema"] = keys == {"triage", "explanation"}
        out["extra_fields"] = bool(keys - {"triage", "explanation"})
        triage = parsed.get("triage")
        explanation = parsed.get("explanation")
        out["prediction"] = triage if isinstance(triage, str) else None
        out["explanation_valid"] = isinstance(explanation, str) and bool(explanation.strip())
        out["decision_valid"] = (
            isinstance(triage, str) and triage in {"A", "B", "C", "D"} and out["explanation_valid"]
        )
    return out


def _baseline_parse(evidence, result):
    """Structured rounds have a provider-specific raw format; use raw+ledger."""
    raw = _raw_json(evidence) or {}
    answer = ((raw.get("answers") or {}).get("triage") or {})
    triage = answer.get("choice") if isinstance(answer, dict) else None
    if triage is None:
        triage = ((result.get("parsed") or {}).get("triage"))
    return {"decision_valid": result.get("status") == "success" and isinstance(triage, str) and triage in {"A", "B", "C", "D"},
            "prediction": triage,
            "raw_json": bool(raw),
            "identity_present": bool(raw.get("model") and raw.get("provider")),
            "cost_present": isinstance((raw.get("usage") or {}).get("cost"), (int, float)) or result.get("cost_usd") is not None}


def verify_format_freeze(freeze_dir=ROOT / "freezes" / "format_control_v1"):
    """Verify all immutable format-freeze files and all 408 job calculations."""
    d = Path(freeze_dir)
    manifest = json.loads((d / "manifest.json").read_text())
    check = dict(manifest); digest = check.pop("freeze_digest", None)
    if digest != _digest(check):
        raise ValueError("format_freeze_digest_mismatch")
    for name, expected in manifest.get("file_hashes", {}).items():
        if _sha(d / name) != expected:
            raise ValueError("format_freeze_file_hash_mismatch")
    models = {x["role"]: x for x in json.loads((d / "models.json").read_text())}
    cases = {json.loads(x)["variant_id"]: json.loads(x) for x in (d / "cases.jsonl").read_text().splitlines() if x}
    jobs = [json.loads(x) for x in (d / "jobs.jsonl").read_text().splitlines() if x]
    if set(manifest.get("file_hashes", {})) != {"models.json", "cases.jsonl", "jobs.jsonl", "rules.json", "protocol.md"} or manifest.get("n_jobs") != 408 or len(jobs) != 408 or len(cases) != 68 or set(models) != set(ROLES):
        raise ValueError("format_freeze_count_mismatch")
    if len({j["key"] for j in jobs}) != 408:
        raise ValueError("format_freeze_duplicate_job")
    if len([json.loads(x) for x in (d / "cases.jsonl").read_text().splitlines() if x]) != len(cases):
        raise ValueError("format_freeze_duplicate_case")
    if {j.get("condition") for j in jobs} != {"explain_options", "natural_language"} or {j.get("role") for j in jobs} != set(ROLES):
        raise ValueError("format_freeze_factor_roster")
    expected_grid = {(role, variant, condition, 1) for role in ROLES for variant in cases
                     for condition in ("explain_options", "natural_language")}
    if {(j['role'], j['variant_id'], j['condition'], j['repeat_id']) for j in jobs} != expected_grid:
        raise ValueError("format_freeze_cartesian_mismatch")
    for case in cases.values():
        if hashlib.sha256(case['case_text'].encode()).hexdigest() != case['case_text_sha256']:
            raise ValueError("format_case_text_hash_mismatch")
    for job in jobs:
        body = payload(models[job["role"]], cases[job["variant_id"]], job["condition"])
        if _digest(body) != job["request_hash"]:
            raise ValueError("format_request_hash_mismatch")
        if reservation(models[job["role"]], body) != Decimal(job["reserved_usd"]):
            raise ValueError("format_reservation_mismatch")
        expected = _digest({k: job[k] for k in ("role", "variant_id", "condition", "repeat_id", "request_hash")})
        if expected != job["key"]:
            raise ValueError("format_job_key_mismatch")
    return manifest, models, cases, jobs


def _read_jobs_ledger(ledger_path, jobs, manifest, models):
    expected = {j["key"]: j for j in jobs}; found = {}
    con = sqlite3.connect("file:" + str(Path(ledger_path).resolve()) + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("ledger_integrity_error")
        for row in con.execute("select key,amount_usd,metadata_json,status,result_json from reservations"):
            if row["key"] not in expected:
                continue
            job = expected[row["key"]]
            if row["status"] != "terminal": raise ValueError("format_ledger_incomplete")
            meta = json.loads(row["metadata_json"]); result = json.loads(row["result_json"] or "null")
            for key in ("key", "role", "variant_id", "condition", "repeat_id", "request_hash", "reserved_usd"):
                if meta.get(key) != job.get(key): raise ValueError("format_ledger_metadata_mismatch")
            if meta.get("freeze_digest") != manifest["freeze_digest"] or str(row["amount_usd"]) != str(job["reserved_usd"]):
                raise ValueError("format_ledger_amount_or_freeze_mismatch")
            if meta.get("model_requested") != models[job["role"]]["model_requested"] or meta.get("endpoint") != models[job["role"]]["endpoint"]:
                raise ValueError("format_ledger_model_mismatch")
            if not isinstance(result, dict) or any(result.get(k) != job.get(k) for k in ("role", "variant_id", "condition", "repeat_id")):
                raise ValueError("format_ledger_result_job_mismatch")
            response_file = result.get("response_file")
            if not response_file or not result.get("response_file_sha256"):
                raise ValueError("format_response_missing")
            path = Path(response_file)
            if not path.exists() or _sha(path) != result["response_file_sha256"]:
                raise ValueError("format_response_hash_mismatch")
            evidence = json.loads(path.read_text())
            if evidence.get("job") != job:
                raise ValueError("format_evidence_job_mismatch")
            if _digest(evidence.get("request_payload")) != job["request_hash"]:
                raise ValueError("format_evidence_request_hash_mismatch")
            stripped = {k: v for k, v in result.items() if k not in {"response_file", "response_file_sha256"}}
            if evidence.get("result") != stripped:
                raise ValueError("format_evidence_result_mismatch")
            raw = _raw_json(evidence)
            if job["condition"] in {"explain_options", "natural_language"} and (result.get("status") != "success" or result.get("http_status") != 200):
                raise ValueError("format_terminal_status_mismatch")
            usage = (raw.get("usage") or {}) if isinstance(raw, dict) else {}
            has_cost = isinstance(usage.get("cost"), (int, float)) or (
                isinstance(usage.get("prompt_tokens"), int) and isinstance(usage.get("completion_tokens"), int)
                and isinstance(result.get("cost_usd"), str) and result.get("cost_status") == "estimated_upper_peak_uncached"
            )
            if job["condition"] in {"explain_options", "natural_language"} and (not raw or not isinstance(raw.get("model"), str) or not has_cost):
                raise ValueError("format_raw_identity_or_cost_missing")
            if job["condition"] in {"explain_options", "natural_language"}:
                try:
                    validate_identity(models[job["role"]], raw)
                    expected_cost, expected_status = response_cost(models[job["role"]], raw)
                except (ValueError, TypeError, KeyError):
                    raise ValueError("format_raw_identity_mismatch")
                if expected_cost is None or str(result.get("cost_usd")) != str(expected_cost) or result.get("cost_status") != expected_status:
                    raise ValueError("format_raw_cost_mismatch")
                if Decimal(expected_cost) > Decimal(job['reserved_usd']):
                    raise ValueError("format_cost_exceeds_reservation")
            if job["condition"] in {"explain_options", "natural_language"}:
                choices = raw.get("choices")
                if not isinstance(choices, list) or not choices or choices[0].get("finish_reason") != "stop":
                    raise ValueError("format_finish_reason_mismatch")
                message = choices[0].get('message') or {}
                if message.get('refusal') or not isinstance(message.get('content'), str) or not message['content'].strip():
                    raise ValueError("format_refusal_or_empty")
                if result.get('assistant_text') != message['content']:
                    raise ValueError("format_stored_answer_mismatch")
            found[job["key"]] = (job, result, evidence)
    finally:
        con.close()
    if set(found) != set(expected):
        raise ValueError("format_ledger_job_count_mismatch")
    return found


def _baseline_records(root, ledger, freeze_name):
    manifest, models, cases, jobs = load_freeze(root / "freezes" / freeze_name)
    refs = {k: v for k, v in cases.items() if v.get("is_reference_version")}
    allrows = _read_jobs_ledger(ledger, jobs, manifest, models)
    rows = {}
    for key, (job, result, evidence) in allrows.items():
        if job["variant_id"] in refs and job["role"] in ROLES:
            parsed = _baseline_parse(evidence, result)
            score = score_prediction(parsed["prediction"] if parsed["decision_valid"] else None, refs[job["variant_id"]]["acceptable_labels"])
            rows[(job["role"], job["variant_id"])] = {**parsed, **score, "scenario_id": refs[job["variant_id"]]["scenario_id"]}
    if len(rows) != 68 * 3:
        raise ValueError("baseline_reference_count_mismatch")
    return refs, rows


def analyze(root=ROOT, ledger=ROOT / "runs" / "project_budget.sqlite"):
    root = Path(root); ledger = Path(ledger)
    fm, models, cases, jobs = verify_format_freeze(root / "freezes" / "format_control_v1")
    format_rows = _read_jobs_ledger(ledger, jobs, fm, models)
    refs = cases
    baseline = {}
    for i, freeze in enumerate(("first_round_v1", "second_round_v1", "third_round_v1"), 1):
        ref, rows = _baseline_records(root, ledger, freeze); 
        if set(ref) != set(refs): raise ValueError("baseline_reference_identity_mismatch")
        for variant in refs:
            if ref[variant]["case_text_sha256"] != refs[variant]["case_text_sha256"]:
                raise ValueError("baseline_reference_text_mismatch")
        baseline[i] = rows
    pairs = []
    for key, (job, result, evidence) in format_rows.items():
        if job["condition"] != FORMAT_CONDITION: continue
        parsed = _format_parse(evidence)
        score = score_prediction(parsed["prediction"] if parsed["decision_valid"] else None, refs[job["variant_id"]]["acceptable_labels"])
        for round_no in (1, 2, 3):
            b = baseline[round_no][(job["role"], job["variant_id"])]
            pairs.append({"round": round_no, "role": job["role"], "variant_id": job["variant_id"], "scenario_id": refs[job["variant_id"]]["scenario_id"], "cohort": "emergency" if refs[job["variant_id"]]["dataset_id"] == "nm_emergency" else "main", "label_type": refs[job["variant_id"]]["label_type"], "baseline_valid": int(b["valid_decision"]), "baseline_correct": int(b["correct"]), "explanation_raw_schema": int(parsed["content_json"] and parsed["explanation_valid"]), "explanation_exact_schema": int(parsed["exact_schema"]), "explanation_extra_fields": int(parsed["extra_fields"]), "explanation_finish_stop": int(parsed["finish_stop"]), "explanation_refusal_absent": int(parsed["refusal_absent"]), "explanation_nonempty": int(parsed["content_nonempty"]), "explanation_valid": int(parsed["decision_valid"]), "explanation_correct": int(score["correct"]), "gain": int(score["correct"]) - int(b["correct"]), "loss": int(b["correct"]) - int(score["correct"]), "baseline_prediction": b["prediction"], "explanation_prediction": parsed["prediction"]})
    return {"pairs": pairs, "audit": {"format_freeze_digest": fm["freeze_digest"], "format_jobs": len(jobs), "format_explain_jobs": sum(j["condition"] == FORMAT_CONDITION for j in jobs), "format_natural_language_jobs": sum(j["condition"] == "natural_language" for j in jobs), "reference_inputs": len(refs), "baseline_rounds": 3, "baseline_roles": list(ROLES), "bootstrap_replicates": BOOTSTRAP_ITERATIONS, "seed": SEED}}


def summarize_pairs(pairs):
    """Return descriptive cohort metrics and paired cluster bootstrap results."""
    out = {}
    for round_no in (1, 2, 3):
        for role in ROLES:
            rows = [r for r in pairs if r["round"] == round_no and r["role"] == role]
            for cohort, subset in (("main_clear", [r for r in rows if r["cohort"] == "main" and r["label_type"] == "clear"]), ("main_full", [r for r in rows if r["cohort"] == "main"]), ("emergency", [r for r in rows if r["cohort"] == "emergency"])):
                rec = [{"scenario_id": r["scenario_id"], "model_a": {"valid_decision": bool(r["explanation_valid"]), "correct": bool(r["explanation_correct"])}, "model_b": {"valid_decision": bool(r["baseline_valid"]), "correct": bool(r["baseline_correct"])}} for r in subset]
                common = paired_cluster_bootstrap(rec, seed=SEED, iterations=BOOTSTRAP_ITERATIONS, common_only=True)
                full = paired_cluster_bootstrap(rec, seed=SEED, iterations=BOOTSTRAP_ITERATIONS, common_only=False)
                out[f"r{round_no}_{role}_{cohort}"] = {"planned": len(subset), "explanation_valid": sum(r["explanation_valid"] for r in subset), "baseline_valid": sum(r["baseline_valid"] for r in subset), "explanation_correct": sum(r["explanation_correct"] for r in subset), "baseline_correct": sum(r["baseline_correct"] for r in subset), "common_valid": common, "full_plan": full, "exact_schema": sum(r["explanation_exact_schema"] for r in subset), "extra_fields": sum(r["explanation_extra_fields"] for r in subset)}
    return out


def write_csv(path, pairs):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(pairs[0]) if pairs else [])
        writer.writeheader(); writer.writerows(pairs)
