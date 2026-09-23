"""Offline first-round result summariser; never sends requests or writes a ledger."""
from __future__ import annotations
import argparse, hashlib, json, random, sqlite3
from pathlib import Path
from collections import defaultdict
ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))
from analysis.paired import paired_cluster_bootstrap, paired_denominators

ROLES = ("jev", "luna", "gemini", "deepseek")

def _valid(x): return x if x in {"A", "B", "C", "D"} else None

def _rows(cases):
    return cases.values() if isinstance(cases, dict) else cases

def _score(case, triage):
    labels = case.get("acceptable_labels", [])
    return triage is not None and triage in labels

def _metric(cases, jobs, result_by_key, subset):
    selected = {c["variant_id"]: c for c in _rows(cases) if subset(c)}
    by_role = {r: [] for r in ROLES}
    for job in jobs:
        case = selected.get(job.get("variant_id"))
        if case is None or job.get("role") not in by_role: continue
        result = result_by_key.get(job.get("key"), {})
        triage = _valid((result.get("parsed") or {}).get("triage")) if result.get("status") == "success" else None
        by_role[job["role"]].append((case, triage))
    summaries = {}
    for role, vals in by_role.items():
        valid = [(c, t) for c, t in vals if t is not None]
        scenarios = defaultdict(list)
        for c, t in valid: scenarios[c["scenario_id"]].append(_score(c, t))
        scenario_acc = sum(sum(v) / len(v) for v in scenarios.values()) / len(scenarios) if scenarios else None
        summaries[role] = {
            "planned": len(vals), "valid": len(valid), "invalid_or_missing": len(vals)-len(valid),
            "source_label_acceptable": sum(_score(c,t) for c,t in valid),
            "source_label_acceptable_rate": (sum(_score(c,t) for c,t in valid)/len(valid) if valid else None),
            "scenario_equal_weight_valid_accuracy": scenario_acc,
            "explicit_D_to_nonD": sum(c.get("acceptable_labels") == ["D"] and t != "D" for c,t in valid),
            "explicit_D_planned": sum(c.get("acceptable_labels") == ["D"] for c,t in vals),
            "explicit_D_valid": sum(c.get("acceptable_labels") == ["D"] for c,t in valid),
        }
    return summaries, by_role

def _paired(by_role, left, right, common_only):
    a = {(c["variant_id"]): (c,t) for c,t in by_role[left]}
    b = {(c["variant_id"]): (c,t) for c,t in by_role[right]}
    records=[]
    for key in sorted(a.keys() | b.keys()):
        c = (a.get(key) or b.get(key))[0]
        records.append({"scenario_id": c["scenario_id"], "model_a": {"valid_decision": a.get(key, (c,None))[1] is not None, "correct": bool(a.get(key, (c,None))[1] is not None and _score(*a[key]))}, "model_b": {"valid_decision": b.get(key, (c,None))[1] is not None, "correct": bool(b.get(key, (c,None))[1] is not None and _score(*b[key]))}})
    den = paired_denominators(records)
    boot = paired_cluster_bootstrap(records, common_only=common_only)
    return {"denominators": den, "bootstrap": boot, "common_only": common_only}

def summarize(cases_by_id, jobs, result_by_key):
    def main_clear(c): return c.get("dataset_id") == "nm_main" and c.get("label_type") == "clear"
    def main_all(c): return c.get("dataset_id") == "nm_main"
    def full(c): return True
    def sensitivity(c): return not c.get("ai_source_review_flag", False)
    layers = {}
    role_data = {}
    definitions = (("main_clear", main_clear), ("main_full", main_all), ("emergency", lambda c: c.get("dataset_id") == "nm_emergency"),
                   ("sensitivity_main_clear", lambda c: main_clear(c) and sensitivity(c)),
                   ("sensitivity_main_full", lambda c: main_all(c) and sensitivity(c)), ("all_data", full))
    pair_layers = {"main_clear", "main_full", "emergency", "sensitivity_main_clear", "sensitivity_main_full"}
    for name, fn in definitions:
        role_data[name], detail = _metric(cases_by_id, jobs, result_by_key, fn)
        selected_rows = [c for c in _rows(cases_by_id) if fn(c)]
        base_fn = {"sensitivity_main_clear": main_clear, "sensitivity_main_full": main_all}.get(name, fn)
        base_rows = [c for c in _rows(cases_by_id) if base_fn(c)]
        excluded = sum(bool(c.get("ai_source_review_flag")) for c in base_rows)
        layers[name] = {"models": role_data[name], "sensitivity_excluded_rows": excluded,
                        "sensitivity_total_excluded_rows": 176,
                        "sensitivity_subset_rows": len(selected_rows)}
        if name in pair_layers:
            layers[name]["paired"] = {pair: {"common_valid": _paired(detail, *pair.split("_vs_"), True), "full_plan": _paired(detail, *pair.split("_vs_"), False)} for pair in ("jev_vs_luna", "jev_vs_gemini", "jev_vs_deepseek")}
        if name == "emergency": layers[name]["analysis"] = "independent_supplement"
    return {"study_id": "jev_triage_20260922", "clinical_safety_claim": False,
            "primary": "main_clear", "layers": layers,
            "adjusted_significance": "not_reported; Holm/p-values not implemented",
            "equivalence_or_noninferiority": "not_claimed"}

def _read_sqlite(path, jobs, freeze_digest, models, root=ROOT):
    uri = "file:" + str(Path(path).resolve()) + "?mode=ro"
    con = sqlite3.connect(uri, uri=True); con.row_factory = sqlite3.Row
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    if "reservations" not in tables: raise ValueError("ledger_reservations_table_missing")
    cols = {r[1] for r in con.execute("pragma table_info(reservations)")}
    if not {"key", "metadata_json", "status", "result_json"} <= cols: raise ValueError("ledger_job_metadata_missing")
    expected={j["key"]:j for j in jobs}; out={}
    for row in con.execute("select key,amount_usd,metadata_json,status,result_json from reservations"):
        if row["key"] not in expected: continue
        if row["status"] != "terminal": raise ValueError("incomplete_formal_results")
        meta=json.loads(row["metadata_json"]); job=expected[row["key"]]
        for key in ("key","role","variant_id","condition","repeat_id","request_hash","reserved_usd"):
            if meta.get(key) != job.get(key): raise ValueError("ledger_job_metadata_mismatch")
        if str(row["amount_usd"]) != str(job["reserved_usd"]): raise ValueError("ledger_amount_mismatch")
        if models:
            model=models.get(job["role"], {})
            if meta.get("model_requested") != model.get("model_requested") or meta.get("endpoint") != model.get("endpoint"):
                raise ValueError("ledger_model_metadata_mismatch")
        if meta.get("freeze_digest") != freeze_digest: raise ValueError("ledger_freeze_digest_mismatch")
        result=json.loads(row["result_json"] or "null")
        if not isinstance(result,dict): raise ValueError("ledger_result_invalid")
        for key in ("role", "variant_id", "condition", "repeat_id"):
            if result.get(key) != job.get(key): raise ValueError("ledger_result_job_mismatch")
        response_file=result.get("response_file")
        if result.get("status") == "interrupted_unknown":
            if response_file or result.get("response_file_sha256"): raise ValueError("interrupted_has_response")
        else:
            if not response_file or not result.get("response_file_sha256"): raise ValueError("response_evidence_missing")
            rp=Path(response_file); rp=rp if rp.is_absolute() else root/rp
            if not rp.exists() or hashlib.sha256(rp.read_bytes()).hexdigest() != result["response_file_sha256"]: raise ValueError("response_evidence_hash_mismatch")
            evidence=json.loads(rp.read_text())
            if evidence.get("job") != job: raise ValueError("response_job_mismatch")
            payload=evidence.get("request_payload")
            payload_hash=hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()
            if payload_hash != job.get("request_hash"): raise ValueError("response_payload_hash_mismatch")
            evidence_result=evidence.get("result")
            stripped={k:v for k,v in result.items() if k not in {"response_file","response_file_sha256"}}
            if evidence_result != stripped: raise ValueError("response_result_mismatch")
        out[row["key"]]=result
    con.close()
    if set(out) != set(expected): raise ValueError("incomplete_formal_results")
    return out

def _markdown(report):
    lines=["# 第一轮结构化分诊结果", "", "仅报告技术有效性与源标签一致性；不构成临床安全、等效性或非劣效结论。", "",
           "| 层级 | 模型 | planned | valid | 可接受率 | 情境等权正确率 | D planned/valid | D→非D |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for layer, block in report["layers"].items():
        for role, m in block["models"].items():
            def f(x): return "NA" if x is None else f"{x:.4f}" if isinstance(x,float) else str(x)
            lines.append(f"| {layer} | {role} | {m['planned']} | {m['valid']} | {f(m['source_label_acceptable_rate'])} | {f(m['scenario_equal_weight_valid_accuracy'])} | {m['explicit_D_planned']}/{m['explicit_D_valid']} | {m['explicit_D_to_nonD']} |")
        if "paired" in block:
            lines += ["", f"**{layer} 配对比较（情境等权 bootstrap）**", "", "| 比较 | 模式 | 情境数 | 估计差值 | CI95 |", "|---|---|---:|---:|---|"]
            for pair, modes in block["paired"].items():
                for mode, data in modes.items():
                    boot=data["bootstrap"]; lines.append(f"| {pair} | {mode} | {boot.get('n_scenarios',0)} | {boot.get('estimate')} | {boot.get('ci95')} |")
    lines += ["", "调整后显著性：未报告（未实现 p 值/Holm）。", ""]
    return "\n".join(lines)

def main():
    from runtime.formal_plan import load_freeze
    p=argparse.ArgumentParser(); p.add_argument("--freeze",type=Path,default=ROOT/"freezes/first_round_v1"); p.add_argument("--ledger",type=Path,default=ROOT/"runs/project_budget.sqlite"); p.add_argument("--output-dir",type=Path,default=ROOT/"reports"); args=p.parse_args()
    manifest, _, cases, jobs = load_freeze(args.freeze)
    from runtime.formal_runner import validate_software
    validate_software(manifest, ROOT)
    results = _read_sqlite(args.ledger, jobs, manifest["freeze_digest"], _, ROOT)
    report=summarize(cases,jobs,results); args.output_dir.mkdir(parents=True,exist_ok=True)
    (args.output_dir/"first_round_results.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    (args.output_dir/"第一轮结果.md").write_text(_markdown(report)+"\n")

if __name__ == "__main__": main()
