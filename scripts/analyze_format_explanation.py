"""Create the offline format explanation report from frozen evidence."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from postanalysis.format_explanation import ROOT, analyze, summarize_pairs, write_csv


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports" / "format_explanation_v1")
    args = parser.parse_args(); args.output_dir.mkdir(parents=True, exist_ok=True)
    report = analyze(); pairs = report["pairs"]; summary = summarize_pairs(pairs)
    write_csv(args.output_dir / "pairs.csv", pairs)
    (args.output_dir / "results.json").write_text(json.dumps({"audit": report["audit"], "summary": summary}, ensure_ascii=False, indent=2) + "\n")
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / "freezes" / "format_control_v1").iterdir() if p.is_file()}
    (args.output_dir / "audit.json").write_text(json.dumps({"audit": report["audit"], "freeze_file_hashes": hashes}, ensure_ascii=False, indent=2) + "\n")
    lines = ["# 解释 JSON 格式离线分析 v1", "", "本报告只读取冻结文件、三轮结构化账本和格式控制 response evidence；不含 API 请求，也不改写原冻结或原报告。首轮为主要比较，第二、三轮为描述性复现。", "", "| 层级 | 模型 | planned | 解释有效 | 解释正确 | 基线有效 | 基线正确 | common-valid 差值 (95% CI) | full-plan 差值 (95% CI) | exact/extra |", "|---|---|---:|---:|---:|---:|---:|---|---|---:|"]
    order = ("main_clear", "main_full", "emergency")
    for round_no in (1, 2, 3):
        for role in ("luna", "gemini", "deepseek"):
            for cohort in order:
                m = summary[f"r{round_no}_{role}_{cohort}"]; c = m["common_valid"]; f = m["full_plan"]
                fmt = lambda x: "NA" if x is None else f"{x:.4f}"
                lines.append(f"| r{round_no} {cohort} | {role} | {m['planned']} | {m['explanation_valid']} | {m['explanation_correct']} | {m['baseline_valid']} | {m['baseline_correct']} | {fmt(c['estimate'])} ({fmt(c['ci95'][0])}, {fmt(c['ci95'][1])}) | {fmt(f['estimate'])} ({fmt(f['ci95'][0])}, {fmt(f['ci95'][1])}) | {m['exact_schema']}/{m['extra_fields']} |")
    lines += ["", "配对单位为同一 68 个 reference 输入；`main_clear`=30 版本/15 情境，`main_full`=60/30，`emergency`=8/4。bootstrap 按情境等权，5000 次，seed=20260922。`explanation_valid` 要求 JSON dict、triage 为 A–D、explanation 为非空字符串；extra field 单独计数，不使决策失效。", "", "本报告不作 confirmatory significance 声称；所有轮次和模型均为描述性差值与区间。格式/schema 判定是在数据收集后按 raw evidence 解析的选择，未预注册为临床终点。", "", "自然语言条件（204 jobs）仅完成账本/response 审计，未纳入本评分表；其旧提示含有 trailing `choose best TRIAGE option` 的自然语言设计问题。本报告不对该条件编码。"]
    (args.output_dir / "结果.md").write_text("\n".join(lines) + "\n")
    provenance = {
        "implementation_timing": "post_collection_exploratory_analysis",
        "software_hashes": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in (Path(__file__).resolve(), ROOT / "postanalysis/format_explanation.py",
                                      ROOT / "analysis/paired.py", ROOT / "analysis/scoring.py")},
        "output_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted(args.output_dir.iterdir())
                          if p.is_file() and p.name != "provenance.json"},
        "natural_language_legacy_instruction_inputs": 68,
        "natural_language_legacy_instruction_requests": 204,
    }
    (args.output_dir / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output_dir": str(args.output_dir), "pairs": len(pairs), "audit": report["audit"]}, ensure_ascii=False))


if __name__ == "__main__": main()
