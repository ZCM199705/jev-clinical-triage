from __future__ import annotations

import csv
import importlib.util
import json
import tempfile
from pathlib import Path


SCRIPT = Path("scripts/analyze_chatgpt_health_historical.py")
SPEC = importlib.util.spec_from_file_location("chatgpt_health_historical", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_fixed_label_set_and_minimum_safety_examples():
    edge = MODULE.score_prediction("C", ("C", "D"))
    assert edge["valid"] is True
    assert edge["correct"] is True
    assert edge["undertriage"] is False

    explicit_d = MODULE.score_prediction("C", ("D",))
    assert explicit_d["valid"] is True
    assert explicit_d["correct"] is False
    assert explicit_d["undertriage"] is True
    assert explicit_d["d_to_non_d"] is True


def test_full_historical_audit_and_fixed_claim():
    with tempfile.TemporaryDirectory() as temp_dir:
        first = MODULE.audit(output_dir=Path(temp_dir) / "one")
        second = MODULE.audit(output_dir=Path(temp_dir) / "two")

        assert first["source_hashes"] == second["source_hashes"]
        assert first["cohorts"]["main_full"] == {
            "agreement_full_plan": 0.8125,
            "correct": 780,
            "correct_rate_valid": 0.8125,
            "d_to_non_d": 33,
            "invalid": 0,
            "matched": 960,
            "overtriage": 134,
            "planned": 960,
            "scenario_count": 30,
            "scenarios": [f"s{i:03d}" for i in range(1, 31)],
            "undertriage": 46,
            "valid": 960,
        }
        assert first["cohorts"]["main_clear"]["planned"] == 480
        assert first["cohorts"]["main_edge"]["correct"] == 462
        assert first["cohorts"]["emergency"] == {
            "agreement_full_plan": 1.0,
            "correct": 128,
            "correct_rate_valid": 1.0,
            "d_to_non_d": 0,
            "invalid": 0,
            "matched": 128,
            "overtriage": 0,
            "planned": 128,
            "scenario_count": 4,
            "scenarios": ["s036", "s037", "s038", "s039"],
            "undertriage": 0,
            "valid": 128,
        }
        assert first["d_underrecognition_audit"]["paper_claim_33_of_64_reproduced"] is True
        assert first["match_audit"] == {
            "body_mismatches": 0,
            "label_mismatches": 0,
            "match_status_counts": {"matched": 1088},
            "matched_rows": 1088,
            "selected_rows": 1088,
            "template_status_counts": {"ok": 1088},
        }
        assert first["excluded"]["expanded_psychological_extension_rows"] == 160

        one_results = (Path(temp_dir) / "one/results.json").read_bytes()
        two_results = (Path(temp_dir) / "two/results.json").read_bytes()
        one_csv = (Path(temp_dir) / "one/per_input.csv").read_bytes()
        two_csv = (Path(temp_dir) / "two/per_input.csv").read_bytes()
        assert one_results == two_results
        assert one_csv == two_csv

        with (Path(temp_dir) / "one/per_input.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 1088
        assert rows[0]["llm_triage"]
        assert rows[0]["response_raw"]



def test_information_version_matches_frozen_and_mismatches_fail_closed():
    import shutil
    with tempfile.TemporaryDirectory() as td:
        root=Path(td);source=root/'source';source.mkdir()
        for name in ['DataOriginal_FINAL.csv','DataExpanded_FINAL.csv']:
            shutil.copyfile(MODULE.RAW/name,source/name)
        original=MODULE.load_csv(source/'DataOriginal_FINAL.csv')
        original[0]['prompt_text']='Altered clinical content. '+original[0]['prompt_text']
        original[1]['gold_triage']='A'
        with (source/'DataOriginal_FINAL.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(original[0]));w.writeheader();w.writerows(original)
        result=MODULE.audit(source_dir=source,output_dir=root/'out')
        assert result['cohorts']['main_full']['planned']==960
        assert result['cohorts']['main_full']['invalid']==2
        assert result['match_audit']['match_status_counts']['body_mismatch']==1
        assert result['match_audit']['match_status_counts']['label_mismatch']==1
        rows=MODULE.load_csv(root/'out/per_input.csv')
        for r in rows:
            assert r['information_version']==('objective' if r['case_id'].startswith(('E','MH')) else 'subjective')


def test_duplicate_source_key_is_not_uniquely_matched():
    import shutil
    with tempfile.TemporaryDirectory() as td:
        root=Path(td);source=root/'source';source.mkdir()
        for name in ['DataOriginal_FINAL.csv','DataExpanded_FINAL.csv']:
            shutil.copyfile(MODULE.RAW/name,source/name)
        original=MODULE.load_csv(source/'DataOriginal_FINAL.csv');original[1]=dict(original[0])
        with (source/'DataOriginal_FINAL.csv').open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(original[0]));w.writeheader();w.writerows(original)
        result=MODULE.audit(source_dir=source,output_dir=root/'out')
        assert result['match_audit']['match_status_counts']['duplicate_source_key']==2
        assert result['cohorts']['main_full']['planned']==960
        assert result['cohorts']['main_full']['invalid']==2
