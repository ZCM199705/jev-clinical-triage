"""Freeze blinded coding jobs from the completed natural-language outputs."""
from __future__ import annotations
import argparse, hashlib, json, random, shutil, sys
from decimal import Decimal
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from supplements.coding import ROLES, payload, reservation, RUBRIC
from postanalysis.format_explanation import verify_format_freeze, _read_jobs_ledger

def canon(v): return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
def digest(v): return hashlib.sha256(canon(v)).hexdigest()
def fh(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--source-freeze',type=Path,default=ROOT/'freezes/format_control_v1'); ap.add_argument('--source-run',type=Path,default=ROOT/'runs/format_control_v1'); ap.add_argument('--destination',type=Path,default=ROOT/'freezes/natural_coding_v1'); a=ap.parse_args()
    d=a.destination if a.destination.is_absolute() else ROOT/a.destination
    if d.exists() and any(d.iterdir()): raise SystemExit('freeze_destination_not_empty')
    sf=a.source_freeze
    sm, models, _, audited_jobs = verify_format_freeze(sf)
    audited = _read_jobs_ledger(ROOT/'runs/project_budget.sqlite', audited_jobs, sm, models)
    source_jobs={}
    for line in (sf/'jobs.jsonl').read_text().splitlines():
        if line:
            j=json.loads(line)
            if j['condition']=='natural_language': source_jobs[j['key']]=j
    records=[]
    for key,j in sorted(source_jobs.items()):
        p=a.source_run/'responses'/(key+'.json')
        if not p.exists(): raise ValueError('missing_source_response')
        ev=json.loads(p.read_text()); r=ev.get('result',{}); text=r.get('assistant_text')
        if r.get('status')!='success' or not isinstance(text,str) or not text: raise ValueError('source_response_not_success')
        if ev != audited[key][2]: raise ValueError('source_run_not_audited')
        import re
        if re.search(r'\b(?:OpenAI|ChatGPT|GPT[- ]?5|Gemini|DeepSeek|Luna)\b', text, re.I):
            raise ValueError('source_identity_requires_logged_redaction_before_freeze')
        records.append({'source_key':key,'source_role':j['role'],'variant_id':j['variant_id'],'repeat_id':j['repeat_id'],
                        'source_response_file':str(p),'source_response_sha256':fh(p),'answer_text':text,
                        'answer_text_sha256':hashlib.sha256(text.encode()).hexdigest(),'transformation':'none'})
    if len(records)!=204 or len({r['source_key'] for r in records})!=204: raise ValueError('natural_source_count')
    d.mkdir(parents=True,exist_ok=True)
    (d/'models.json').write_text(json.dumps([models[r] for r in ROLES],ensure_ascii=False,sort_keys=True,indent=2)+'\n')
    (d/'source_answers.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n' for r in records))
    jobs=[]; mb={r:models[r] for r in ROLES}
    for src in records:
        for coder in ROLES:
            if coder==src['source_role']: continue
            body=payload(mb[coder],src['answer_text']); rh=digest(body)
            base={'source_key':src['source_key'],'source_role':src['source_role'],'coder_role':coder,'variant_id':src['variant_id'],'repeat_id':src['repeat_id'],'answer_text_sha256':src['answer_text_sha256'],'request_hash':rh}
            key=digest(base)
            jobs.append({**base,'key':key,'reserved_usd':str(reservation(mb[coder],body))})
    if len(jobs)!=408: raise ValueError('coding_job_count')
    random.Random(20260922).shuffle(jobs); (d/'jobs.jsonl').write_text(''.join(json.dumps(j,ensure_ascii=False,sort_keys=True)+'\n' for j in jobs))
    rules={'study':'natural_language_coding','source_stage':'format_control_v1','n_source_answers':204,'n_jobs':408,'roles':list(ROLES),'max_output_tokens':4096,'no_source_case_or_gold_in_request':True,'no_resend_existing_keys':True, 'rubric': RUBRIC}
    (d/'rules.json').write_text(json.dumps(rules,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
    shutil.copy2(ROOT/'reports/后续实验执行说明.md',d/'protocol.md')
    files=['models.json','source_answers.jsonl','jobs.jsonl','rules.json','protocol.md']
    manifest={'study_id':'jev_triage_20260922','stage':'natural_coding_v1','schema_version':1,'n_source_answers':204,'n_jobs':408,'max_output_tokens':4096,'total_timeout_seconds':75,'socket_timeout_seconds':50,'connect_timeout_seconds':10,'per_model_concurrency':1,'max_attempts_per_job':1,'schedule_seed':20260922,'total_reservation_usd':str(sum((Decimal(j['reserved_usd']) for j in jobs),Decimal(0))),'source_freeze_digest':json.loads((sf/'manifest.json').read_text())['freeze_digest'],'source_file_hashes':{str(p):fh(p) for p in sorted((a.source_run/'responses').glob('*.json')) if json.loads(p.read_text()).get('result',{}).get('condition')=='natural_language'},'file_hashes':{n:fh(d/n) for n in files},'software_hashes':{str(p.relative_to(ROOT)):fh(p) for p in [ROOT/'supplements/coding.py',ROOT/'runtime/adapters.py',ROOT/'runtime/formal_runner.py',ROOT/'runtime/project_ledger.py',ROOT/'scripts/freeze_natural_coding.py',ROOT/'scripts/run_natural_coding.py', ROOT/'postanalysis/format_explanation.py', ROOT/'runtime/format_control.py', ROOT/'runtime/formal_plan.py', ROOT/'analysis/paired.py', ROOT/'analysis/scoring.py']}}
    manifest['freeze_digest']=digest(manifest); (d/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
    print(json.dumps({'jobs':408,'source_answers':204,'reservation_usd':manifest['total_reservation_usd'],'freeze_digest':manifest['freeze_digest']}))
if __name__=='__main__': main()
