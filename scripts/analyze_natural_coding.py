import argparse, hashlib, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from postanalysis.natural_coding import ROOT, analyze, summarize_records, summarize_by_role, write_csv

def main():
    p=argparse.ArgumentParser(); p.add_argument("--output-dir",type=Path,default=ROOT/"reports"/"natural_coding_v1"); a=p.parse_args(); a.output_dir.mkdir(parents=True,exist_ok=True)
    report=analyze(); rows=report["records"]
    consensus=[{k:v for k,v in r.items() if k not in {"answer_text"}} for r in rows]
    judgements=[]
    for r in rows:
        for i,j in enumerate(r.get("coder_judgements",[]),1): judgements.append({"source_key":r["source_key"],"variant_id":r["variant_id"],"coder_index":i,"coder_role":r.get("coder_roles",[None,None])[i-1],**j})
    write_csv(a.output_dir/"consensus.csv",consensus); write_csv(a.output_dir/"judgements.csv",judgements)
    summary=summarize_by_role(rows)
    (a.output_dir/"results.json").write_text(json.dumps({"audit":report["audit"],"summary_by_source_role":summary,"paired":report["paired"]},ensure_ascii=False,indent=2)+"\n")
    lines=["# 自然语言双 coder 共识分析 v1","","仅使用冻结的 204 个原始自然语言回答、408 个 coding 账本终态及其 raw evidence；无 API 请求。双 coder 均成功、严格 schema、A–D 相同且 evidence_quote 是原回答精确子串时才接受映射；失败、unmapped、quote 不合法或分歧均保留为 unresolved。","","| source role | 层级 | planned | consensus valid | unresolved | valid-only accuracy | correct/full-plan yield | reasons |","|---|---|---:|---:|---:|---:|---:|---|"]
    for role, role_summary in summary.items():
      for n,m in role_summary.items():
        acc = "NA" if m["accuracy_valid"] is None else f"{m['accuracy_valid']:.4f}"
        yld = "NA" if m["correct_yield_full_plan"] is None else f"{m['correct_yield_full_plan']:.4f}"
        lines.append(f"| {role} | {n} | {m['planned']} | {m['consensus_valid']} | {m['unresolved']} | {acc} | {yld} | {m['reason_counts']} |")
    lines += ["","C/D 均按冻结 acceptable label 集合评分；未解决项不填充临床等级。source-role coder pair 按每个原始回答实际的两个 coder 保留。结果是自动 coding 产物，不是 gold，也没有人工验证。","", "首轮结构化配对（同 source role；情境等权 bootstrap）", "", "| source role | cohort | planned | common-valid estimate (95% CI) | full-plan estimate (95% CI) |", "|---|---|---:|---|---|"]
    for role in ("luna","gemini","deepseek"):
      for cohort in ("main_clear","main_full"):
        p=report["paired"][f"r1_{role}_{cohort}"]; c=p["common_valid"]; f=p["full_plan"]
        lines.append(f"| {role} | {cohort} | {p['planned']} | {c.get('estimate')} ({c.get('ci95')}) | {f.get('estimate')} ({f.get('ci95')}) |")
    lines += ["", "观察到账本 coding 成本合计：$0.06781540；unknown coding cost：0。项目账本中的 prior=1 个 unknown 属于既有项目证据，未并入本 coding 成本。不作正向因果结论。旧自然语言提示末尾的 `choose best TRIAGE option` 设计问题作为限制披露。", "", "首轮结构化配对的完整结果保存在 results.json 的 paired 字段；第二、三轮同样计算但仅作探索性描述。"]
    (a.output_dir/"结果.md").write_text("\n".join(lines)+"\n")
    files=list((ROOT/"freezes"/"natural_coding_v1").glob("*")); hashes={str(x.relative_to(ROOT)):hashlib.sha256(x.read_bytes()).hexdigest() for x in files if x.is_file()}; (a.output_dir/"audit.json").write_text(json.dumps({"audit":report["audit"],"freeze_hashes":hashes,"source_code_files":["postanalysis/natural_coding.py","scripts/analyze_natural_coding.py","supplements/coding.py"]},ensure_ascii=False,indent=2)+"\n")
    outputs=[p for p in a.output_dir.iterdir() if p.is_file() and p.name!="provenance.json"]
    provenance={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in outputs}
    for name in ("postanalysis/natural_coding.py","scripts/analyze_natural_coding.py","supplements/coding.py"):
        provenance[name]=hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
    (a.output_dir/"provenance.json").write_text(json.dumps(provenance,ensure_ascii=False,indent=2)+"\n")
if __name__=="__main__": main()
