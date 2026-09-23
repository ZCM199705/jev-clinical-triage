"""Build and verify the offline controlled-efficiency freeze.

This command never contacts a provider.  It imports the already verified
formal freeze through ``runtime.formal_plan.load_freeze`` and uses the
outcome-independent schedule in ``supplements.efficiency_plan``.
"""
from __future__ import annotations
import argparse, hashlib, json, sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from runtime.formal_plan import load_freeze, digest, file_hash
from supplements.efficiency_plan import make_schedule, summarize_schedule, ROLES

SOURCE = ROOT / "freezes/first_round_v1"
FILES = ("source.json", "models.json", "cases.jsonl", "jobs.jsonl", "cells.json", "protocol.md", "rules.json")

def _jsonl(rows):
    return "".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in rows)

def _software(root):
    paths = [root / "runtime/adapters.py", root / "runtime/formal_plan.py",
             root / "runtime/project_ledger.py", root / "runtime/formal_runner.py", root / "supplements/efficiency_plan.py"]
    paths += [root / "scripts/freeze_efficiency.py", root / "scripts/run_efficiency.py"]
    return {str(p.relative_to(root)): file_hash(p) for p in paths}

def build_freeze(destination: Path, root: Path = ROOT, source: Path = SOURCE):
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError("freeze_destination_not_empty")
    destination.mkdir(parents=True, exist_ok=True)
    source = Path(source)
    sm, models, cases, source_jobs = load_freeze(source)
    cells, jobs = make_schedule(cases, models)
    source_info = {"freeze_digest": sm["freeze_digest"], "manifest_sha256": file_hash(source / "manifest.json"),
                   "jobs": len(source_jobs), "cases": len(cases),
                   "file_hashes": {n: file_hash(source / n) for n in ("manifest.json", "models.json", "cases.jsonl", "jobs.jsonl", "rules.json", "protocol.md")}}
    (destination / "source.json").write_text(json.dumps(source_info, sort_keys=True, indent=2) + "\n")
    (destination / "models.json").write_text(json.dumps([models[r] for r in ROLES], sort_keys=True, indent=2) + "\n")
    (destination / "cases.jsonl").write_text(_jsonl([cases[k] for k in sorted(cases)]))
    (destination / "jobs.jsonl").write_text(_jsonl(jobs))
    (destination / "cells.json").write_text(json.dumps(cells, sort_keys=True, indent=2) + "\n")
    (destination / "protocol.md").write_text((root/'reports/后续实验执行说明.md').read_text())
    rules = {"stage": "efficiency_v1", "seed": 20260923, "levels": [1,4,8,16,32],
             "waves": 3, "warmups_per_cell": 5, "measured_per_cell": 200,
             "no_retry": True, "fatal_http": [402,429], "fatal_cost_or_identity": True,
             "three_consecutive_transport_errors": True}
    rules['admission_spacing_seconds']={'jev':0.06}
    rules['rate_limit_interpretation']='Jev <=16.67 admissions/s; conservative below documented1200RPM. Concurrency is a ceiling; record realized peak. Other providers halt on429.'
    (destination / "rules.json").write_text(json.dumps(rules, sort_keys=True, indent=2) + "\n")
    summary = summarize_schedule(cells, jobs)
    manifest = {"study_id": "jev_triage_20260922", "stage": "efficiency_v1", "schema_version": 1,
                "n_jobs": len(jobs), "n_cells": len(cells), "max_output_tokens": 4096,
                "total_timeout_seconds": 75, "socket_timeout_seconds": 50, "connect_timeout_seconds": 10,
                "admission_spacing_seconds": {"jev":0.06},
                "source_freeze_digest": sm["freeze_digest"], "schedule_seed": 20260923,
                "total_reservation_usd": summary["reservation_usd"], "file_hashes": {},
                "preflight_evidence": {p: file_hash(root / p) for p in (
                    "reports/preflight/efficiency_openrouter_public.json",
                    "reports/preflight/efficiency_deepseek_public.json","reports/preflight/efficiency_typesafe_limits.json","reports/preflight/efficiency_live_preflight.json")},
                "software_hashes": _software(root), "created_at": datetime.now(timezone.utc).isoformat()}
    manifest["file_hashes"] = {n: file_hash(destination / n) for n in FILES}
    manifest["freeze_digest"] = digest(manifest)
    (destination / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    return manifest

def load_efficiency_freeze(path: Path, root: Path = ROOT):
    path = Path(path); m = json.loads((path / "manifest.json").read_text())
    if m.get("stage") != "efficiency_v1" or m.get("schema_version") != 1 or m.get('study_id')!='jev_triage_20260922' or m.get('max_output_tokens')!=4096:
        raise ValueError("efficiency_manifest_identity_mismatch")
    if set(m.get("file_hashes", {})) != set(FILES): raise ValueError("efficiency_file_manifest_mismatch")
    for n,h in m["file_hashes"].items():
        if file_hash(path / n) != h: raise ValueError("efficiency_file_hash_mismatch")
    check=dict(m); check.pop("freeze_digest", None)
    if digest(check) != m.get("freeze_digest"): raise ValueError("efficiency_freeze_digest_mismatch")
    if set(m.get('software_hashes',{}))!=set(_software(root)): raise ValueError('efficiency_software_roster')
    for name, expected in m.get("software_hashes", {}).items():
        if file_hash(root / name) != expected: raise ValueError("efficiency_software_changed")
    source=json.loads((path/"source.json").read_text())
    if source.get("freeze_digest") != m.get("source_freeze_digest"): raise ValueError("efficiency_source_mismatch")
    source_path=root / "freezes/first_round_v1"
    for n,h in source.get("file_hashes",{}).items():
        if file_hash(source_path/n) != h: raise ValueError("efficiency_source_file_changed")
    models={x["role"]:x for x in json.loads((path/"models.json").read_text())}
    cases={x["variant_id"]:x for x in (json.loads(l) for l in (path/"cases.jsonl").read_text().splitlines() if l)}
    jobs=[json.loads(l) for l in (path/"jobs.jsonl").read_text().splitlines() if l]
    cells=json.loads((path/"cells.json").read_text())
    if len(cases)!=1088 or len(cells)!=60 or len(jobs)!=12300: raise ValueError("efficiency_counts_mismatch")
    if set(models)!=set(ROLES): raise ValueError("efficiency_model_roster_mismatch")
    sm, original_models, original_cases, _ = load_freeze(source_path)
    if sm['freeze_digest']!=m['source_freeze_digest'] or models!=original_models or cases!=original_cases:
        raise ValueError('efficiency_source_content_mismatch')
    expected_cells, expected_jobs = make_schedule(cases, models)
    if cells != expected_cells or jobs != expected_jobs: raise ValueError("efficiency_schedule_recalculation_mismatch")
    if Decimal(m.get("total_reservation_usd", "-1")) != sum((Decimal(j["reserved_usd"]) for j in jobs), Decimal(0)):
        raise ValueError("efficiency_total_reservation_mismatch")
    if m.get('admission_spacing_seconds')!={'jev':0.06}: raise ValueError('efficiency_admission_policy_changed')
    if set(m.get('preflight_evidence',{}))!={'reports/preflight/efficiency_openrouter_public.json','reports/preflight/efficiency_deepseek_public.json','reports/preflight/efficiency_typesafe_limits.json','reports/preflight/efficiency_live_preflight.json'}:
        raise ValueError('efficiency_preflight_roster')
    for name, expected in m.get("preflight_evidence", {}).items():
        if file_hash(root / name) != expected: raise ValueError("efficiency_preflight_changed")
    seen=set()
    for j in jobs:
        if j["key"] in seen or j["role"] not in models or j["variant_id"] not in cases: raise ValueError("efficiency_job_identity")
        seen.add(j["key"])
    return m, models, cases, cells, jobs

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--destination", type=Path, default=ROOT/"freezes/efficiency_v1")
    a=ap.parse_args(); print(json.dumps(build_freeze(a.destination), sort_keys=True))
if __name__ == "__main__": main()
