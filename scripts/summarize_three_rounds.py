"""Read-only-ledger offline analysis. No network, API calls or runtime changes."""
from pathlib import Path
import argparse
import hashlib
import json
import platform
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from postanalysis.core import load_all, analyze, scenario_rows, sha
from postanalysis.factors import analyze_factors
from postanalysis.calibration import analyze_calibration
from postanalysis.reporting import csv_write, markdown, figures

def write_json(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n')

def run(output):
    protected=[p for folder in ('freezes','runtime','analysis') for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    protected += [ROOT/'reports/first_round_results.json',ROOT/'reports/第一轮结果.md',ROOT/'configs/project_budget.json']
    before={str(p.relative_to(ROOT)):sha(p) for p in protected}
    cases,results,legacy,audit=load_all(ROOT)
    report=analyze(cases,results,legacy)
    report['audit']=audit
    report['factors']=analyze_factors(cases,results)
    report['calibration']=analyze_calibration(cases,results)
    report['method']={'version':'postcollection_three_round_v1','primary_round':1,
        'bootstrap_replicates':5000,'seed':20260922,'primary_comparison':'jev_vs_luna',
        'combined':'within-input average of rounds, then within-scenario mean, then equal scenarios',
        'inference_unit':'scenario','clinical_validation':False,'added_api_calls':0,
        'other_strata':'exploratory; no adjusted superiority claims'}
    output.mkdir(parents=True,exist_ok=True)
    # Save large pair rows separately; the JSON contains their summary and count.
    pair_rows=report['factors'].pop('pairs')
    report['factors']['pair_rows']=len(pair_rows)
    csv_write(output/'factor_pairs.csv',pair_rows)
    csv_write(output/'factor_summary.csv',report['factors']['summary'])
    csv_write(output/'scenario_metrics.csv',scenario_rows(cases,results))
    csv_write(output/'stability.csv',report['stability'])
    write_json(output/'calibration.json',report['calibration'])
    write_json(output/'results.json',report)
    (output/'三轮汇总报告.md').write_text(markdown(report))
    figures(report,output)
    after={str(p.relative_to(ROOT)):sha(p) for p in protected}
    if before!=after: raise ValueError('protected_files_changed')
    software=[Path(__file__)]+list((ROOT/'postanalysis').glob('*.py'))+[ROOT/'scripts/summarize_formal_first_round.py']+list((ROOT/'analysis').glob('*.py'))
    import matplotlib
    write_json(output/'provenance.json',{'analysis_stage':'implemented after collecting all three rounds',
        'analysis_config':report['method'],'software_hashes':{str(p.relative_to(ROOT)):sha(p) for p in sorted(software)},
        'python':platform.python_version(),'matplotlib':matplotlib.__version__,
        'input_hashes':audit['source_hashes'],'protected_files_unchanged':True,
        'verified_ledger_records_sha256':audit['verified_ledger_records_sha256'],
        'preserved_first_report_sha256':before['reports/first_round_results.json'],
        'outputs_sha256':{str(p.relative_to(output)):sha(p) for p in sorted(output.rglob('*')) if p.is_file() and p.name!='provenance.json'}})
    print(json.dumps({'output':str(output),'counts':audit['counts'],'first_round_reproduced':True,
                      'factor_issues':report['factors']['issues'],'secondary_tests':report['secondary_tests']},ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output-dir',type=Path,default=ROOT/'reports/three_round_v1')
    run(parser.parse_args().output_dir.resolve())
