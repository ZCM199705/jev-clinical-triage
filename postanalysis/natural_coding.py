"""Read-only audit and analysis for blinded natural-language coding."""
from __future__ import annotations
import csv, hashlib, json, sqlite3
from collections import Counter
from decimal import Decimal
from pathlib import Path
from analysis.paired import paired_cluster_bootstrap
from analysis.scoring import score_prediction
from runtime.formal_plan import load_freeze
from supplements.coding import OPTIONS, UNMAPPED, payload, reservation
from supplements.coding import extract_response
from runtime.adapters import response_cost, validate_identity

ROOT = Path(__file__).resolve().parents[1]
ROLES = ("luna", "gemini", "deepseek")
SEED = 20260922
ITERATIONS = 5000

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def canon(v): return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
def digest(v): return hashlib.sha256(canon(v)).hexdigest()

def validate_coding(parsed, answer_text):
    """Return a normalized judgement, rejecting every non-contract shape."""
    if not isinstance(parsed, dict) or set(parsed) != {"status", "triage", "evidence_quote"}:
        return {"valid": False, "reason": "schema_invalid", "triage": None, "quote": None}
    status, triage, quote = parsed["status"], parsed["triage"], parsed["evidence_quote"]
    if status == "mapped":
        if triage not in OPTIONS:
            return {"valid": False, "reason": "unmapped_triage", "triage": None, "quote": quote}
        if not isinstance(quote, str) or not quote:
            return {"valid": False, "reason": "quote_missing", "triage": None, "quote": quote}
        if quote not in answer_text:
            return {"valid": False, "reason": "quote_not_exact_substring", "triage": None, "quote": quote}
        return {"valid": True, "reason": "mapped", "triage": triage, "quote": quote}
    if status in UNMAPPED:
        if triage is not None or quote is not None:
            return {"valid": False, "reason": "unmapped_fields_invalid", "triage": None, "quote": quote}
        return {"valid": True, "reason": status, "triage": None, "quote": None}
    return {"valid": False, "reason": "status_invalid", "triage": None, "quote": quote}

def consensus_judgement(coder_rows, answer_text):
    """Require two successful, valid, same-label mappings with exact quotes."""
    if len(coder_rows) != 2:
        return {"consensus": False, "triage": None, "reason": "coder_count"}
    checked = []
    for row in coder_rows:
        if row.get("status") != "success":
            checked.append({"valid": False, "reason": "technical_failure:" + str(row.get("error_detail") or row.get("error_code") or row.get("status")), "triage": None})
        else:
            checked.append(validate_coding(row.get("parsed"), answer_text))
    if not all(x["valid"] for x in checked):
        reasons = sorted(set(x["reason"] for x in checked if not x["valid"]))
        return {"consensus": False, "triage": None, "reason": "+".join(reasons), "coder_judgements": checked}
    if any(x["triage"] is None for x in checked):
        return {"consensus": False, "triage": None, "reason": "unmapped", "coder_judgements": checked}
    if checked[0]["triage"] != checked[1]["triage"]:
        return {"consensus": False, "triage": None, "reason": "disagreement", "coder_judgements": checked}
    return {"consensus": True, "triage": checked[0]["triage"], "reason": "consensus", "evidence_quotes": [x["quote"] for x in checked], "coder_judgements": checked}

def summarize_records(records):
    rows = list(records)
    def block(sub):
        planned = len(sub); valid = [r for r in sub if r.get("consensus")]
        correct = sum(r.get("correct", False) for r in valid)
        return {"planned": planned, "consensus_valid": len(valid), "unresolved": planned-len(valid), "correct_valid": correct, "correct_full_plan": correct, "accuracy_valid": (correct/len(valid) if valid else None), "valid_yield_full_plan": (len(valid)/planned if planned else None), "correct_yield_full_plan": (correct/planned if planned else None), "reason_counts": dict(Counter(r.get("reason", "unknown") for r in sub))}
    return {"main_clear": block([r for r in rows if r.get("cohort") == "main_clear"]),
            "main_full": block([r for r in rows if r.get("cohort") in ("main_clear", "main")]),
            "emergency": block([r for r in rows if r.get("cohort") == "emergency"]),
            "sensitivity_main_clear": block([r for r in rows if r.get("cohort") == "main_clear" and not r.get("ai_source_review_flag")]),
            "sensitivity_main_full": block([r for r in rows if r.get("cohort") in ("main_clear", "main") and not r.get("ai_source_review_flag")])}

def summarize_by_role(records):
    """Preserve the source model as the unit of the coding pipeline."""
    return {role: summarize_records([r for r in records if r.get("source_role") == role]) for role in ROLES}

def paired(records, baseline_records, common_only=True):
    rows=[]
    for r in records:
        b=baseline_records.get((r["source_role"], r["variant_id"]))
        if b is None: continue
        rows.append({"scenario_id":r["scenario_id"], "model_a":{"valid_decision":bool(r.get("consensus")),"correct":bool(r.get("correct"))}, "model_b":b})
    return paired_cluster_bootstrap(rows, seed=SEED, iterations=ITERATIONS, common_only=common_only)

def load_coding_freeze(freeze_dir=ROOT/"freezes"/"natural_coding_v1"):
    d=Path(freeze_dir); m=json.loads((d/"manifest.json").read_text())
    if digest({k:v for k,v in m.items() if k!="freeze_digest"}) != m.get("freeze_digest"): raise ValueError("coding_freeze_digest")
    for n,h in m.get("file_hashes",{}).items():
        if sha(d/n)!=h: raise ValueError("coding_freeze_file_hash")
    models={x["role"]:x for x in json.loads((d/"models.json").read_text())}; sources=[json.loads(x) for x in (d/"source_answers.jsonl").read_text().splitlines() if x]; jobs=[json.loads(x) for x in (d/"jobs.jsonl").read_text().splitlines() if x]
    if len(sources)!=204 or len(jobs)!=408 or set(models)!=set(ROLES): raise ValueError("coding_freeze_count")
    if len({s["source_key"] for s in sources})!=204 or len({j["key"] for j in jobs})!=408: raise ValueError("coding_freeze_unique")
    src={s["source_key"]:s for s in sources}
    for s in sources:
        if hashlib.sha256(s["answer_text"].encode()).hexdigest()!=s["answer_text_sha256"] or sha(s["source_response_file"])!=s["source_response_sha256"]: raise ValueError("coding_source_hash")
    for j in jobs:
        s=src.get(j["source_key"])
        if not s or s["answer_text_sha256"]!=j["answer_text_sha256"]: raise ValueError("coding_job_source")
        body=payload(models[j["coder_role"]],s["answer_text"],m["max_output_tokens"])
        if digest(body)!=j["request_hash"] or reservation(models[j["coder_role"]],body,m["max_output_tokens"])!=Decimal(j["reserved_usd"]): raise ValueError("coding_job_hash")
    return m,models,src,jobs

def audit_results(freeze_dir=ROOT/"freezes"/"natural_coding_v1", ledger=ROOT/"runs"/"project_budget.sqlite"):
    m,models,sources,jobs=load_coding_freeze(freeze_dir); expected={j["key"]:j for j in jobs}; found={}
    con=sqlite3.connect("file:"+str(Path(ledger).resolve())+"?mode=ro",uri=True); con.row_factory=sqlite3.Row
    try:
        for row in con.execute("select key,amount_usd,metadata_json,status,result_json from reservations"):
            if row["key"] not in expected: continue
            j=expected[row["key"]]; meta=json.loads(row["metadata_json"]); result=json.loads(row["result_json"] or "null")
            if row["status"]!="terminal": raise ValueError("coding_terminal_missing")
            for k in ("key","source_key","coder_role","variant_id","repeat_id","request_hash","reserved_usd"):
                if meta.get(k)!=j.get(k): raise ValueError("coding_metadata_mismatch")
            if meta.get("freeze_digest")!=m["freeze_digest"] or str(row["amount_usd"])!=str(j["reserved_usd"]): raise ValueError("coding_amount_mismatch")
            if not isinstance(result,dict) or result.get("source_key")!=j["source_key"]: raise ValueError("coding_result_mismatch")
            p=Path(result.get("response_file",""))
            if not p.exists() or sha(p)!=result.get("response_file_sha256"): raise ValueError("coding_response_hash")
            ev=json.loads(p.read_text()); stripped={k:v for k,v in result.items() if k not in {"response_file","response_file_sha256"}}
            if ev.get("job")!=j or digest(ev.get("request_payload"))!=j["request_hash"] or ev.get("result")!=stripped: raise ValueError("coding_evidence_mismatch")
            raw = json.loads(ev.get("raw_response") or "null") if isinstance(ev.get("raw_response"), str) else None
            if result.get("status") == "success":
                if not isinstance(raw, dict): raise ValueError("coding_raw_missing")
                try:
                    validate_identity(models[j["coder_role"]], raw)
                    cost, cstatus = response_cost(models[j["coder_role"]], raw)
                    parsed = extract_response(raw, sources[j["source_key"]]["answer_text"])
                except Exception as exc: raise ValueError("coding_raw_validation") from exc
                if result.get("parsed") != parsed or str(result.get("cost_usd")) != str(cost) or result.get("cost_status") != cstatus: raise ValueError("coding_raw_result_mismatch")
            found[j["key"]]=(j,result,ev)
    finally: con.close()
    if set(found)!=set(expected): raise ValueError("coding_complete_408_required")
    by_source={s:[] for s in sources}
    for j in jobs: by_source[j["source_key"]].append(j)
    if any(len(v)!=2 or any(j["coder_role"]==j["source_role"] for j in v) for v in by_source.values()): raise ValueError("coding_job_grid_mismatch")
    return m,models,sources,jobs,found

def analyze(root=ROOT, ledger=ROOT/"runs"/"project_budget.sqlite"):
    m,models,sources,jobs,found=audit_results(root/"freezes"/"natural_coding_v1",ledger)
    # Labels come from the unchanged format freeze case rows.
    _,_,cases,_=__import__("postanalysis.format_explanation",fromlist=["verify_format_freeze"]).verify_format_freeze(root/"freezes"/"format_control_v1")
    groups={}
    for s in sources.values():
        coders=[]
        for j in jobs:
            if j["source_key"]==s["source_key"]:
                result=found[j["key"]][1].copy(); result["coder_role"]=j["coder_role"]
                if result.get("status") != "success":
                    try:
                        raw=json.loads(found[j["key"]][2].get("raw_response") or "null")
                        extract_response(raw,s["answer_text"])
                    except Exception as exc:
                        result["error_detail"]=str(exc)
                coders.append(result)
        dec=consensus_judgement(coders,s["answer_text"])
        c=cases[s["variant_id"]]; sc=score_prediction(dec["triage"],c["acceptable_labels"]) if dec["consensus"] else score_prediction(None,c["acceptable_labels"])
        cohort="emergency" if c["dataset_id"]=="nm_emergency" else "main"
        if cohort=="main" and c["label_type"]=="clear": cohort="main_clear"
        rec={**s,**dec,"scenario_id":c["scenario_id"],"cohort":cohort,"correct":sc["correct"],"acceptable_labels":c["acceptable_labels"],"ai_source_review_flag":c.get("ai_source_review_flag",False),"coder_roles":[x.get("coder_role") for x in coders],"coder_costs":[x.get("cost_usd") for x in coders],"coder_cost_statuses":[x.get("cost_status") for x in coders]}
        groups[s["source_key"]]=rec
    records=list(groups.values())
    for r in records:
        if r["cohort"] in ("main_clear","main"):
            r["cohort_full"]="main_full"
        if not r.get("ai_source_review_flag"): r["cohort_sensitivity"]=r["cohort"]
    from postanalysis.format_explanation import _baseline_records
    paired_summary={}
    for round_no, freeze in enumerate(("first_round_v1","second_round_v1","third_round_v1"),1):
        _, base = _baseline_records(root, ledger, freeze)
        for source_role in ROLES:
            selected=[r for r in records if r["source_role"]==source_role]
            for cohort, filt in (("main_clear",lambda r:r["cohort"]=="main_clear"),("main_full",lambda r:r["cohort"] in ("main_clear","main")),("emergency",lambda r:r["cohort"]=="emergency")):
                subset=[r for r in selected if filt(r)]; b={(source_role,r["variant_id"]):base[(source_role,r["variant_id"])] for r in subset}
                rows=[]
                for r in subset:
                    br=b[(source_role,r["variant_id"])]
                    rows.append({"scenario_id":r["scenario_id"],"model_a":{"valid_decision":bool(r.get("consensus")),"correct":bool(r.get("correct"))},"model_b":{"valid_decision":bool(br.get("valid_decision")),"correct":bool(br.get("correct"))}})
                paired_summary[f"r{round_no}_{source_role}_{cohort}"]={"planned":len(rows),"common_valid":paired_cluster_bootstrap(rows,seed=SEED,iterations=ITERATIONS,common_only=True),"full_plan":paired_cluster_bootstrap(rows,seed=SEED,iterations=ITERATIONS,common_only=False),"baseline_round_role":source_role}
    total_cost = sum((Decimal(str(found[k][1]["cost_usd"])) for k in found if found[k][1].get("cost_usd") is not None), Decimal("0"))
    return {"records":records,"paired":paired_summary,"audit":{"freeze_digest":m["freeze_digest"],"jobs":len(jobs),"sources":len(sources),"complete_terminals":len(found),"unknown_cost_jobs":sum(found[k][1].get("cost_usd") is None for k in found),"coding_cost_usd":format(total_cost,"f"),"seed":SEED,"bootstrap_replicates":ITERATIONS}}

def write_csv(path,rows):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("w",newline="",encoding="utf-8-sig") as f:
        fields=[]
        for row in rows:
            for key in row:
                if key not in fields: fields.append(key)
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore"); w.writeheader(); w.writerows(rows)
