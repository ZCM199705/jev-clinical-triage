"""Freeze the pre-specified 408-decision format-control study."""
from __future__ import annotations
import argparse,hashlib,json,random,shutil
import sys
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from runtime.format_control import payload,reservation
ROLES=('luna','gemini','deepseek'); CONDITIONS=('explain_options','natural_language')
def canon(v): return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
def digest(v): return hashlib.sha256(canon(v)).hexdigest()
def fh(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--destination',type=Path,default=ROOT/'freezes/format_control_v1'); a=ap.parse_args(); d=a.destination if a.destination.is_absolute() else ROOT/a.destination
 if d.exists() and any(d.iterdir()): raise SystemExit('freeze_destination_not_empty')
 d.mkdir(parents=True,exist_ok=True); src=ROOT/'freezes/first_round_v1'
 models=[m for m in json.loads((src/'models.json').read_text()) if m['role'] in ROLES]
 cases=[json.loads(x) for x in (src/'cases.jsonl').read_text().splitlines() if x and json.loads(x).get('is_reference_version')]
 if len(cases)!=68: raise ValueError('format_reference_case_count')
 (d/'models.json').write_text(json.dumps(models,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
 (d/'cases.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n' for x in cases))
 rules={'study':'format_control','conditions':CONDITIONS,'roles':ROLES,'n_cases':68,'n_jobs':408,'max_output_tokens':4096,
        'natural_language_coding':'exploratory; no human review; unresolved coding remains unresolved','same_case_text_as_structured_rounds':True,
        'no_post_result_changes':True,'clinical_accuracy_claim':False}
 (d/'rules.json').write_text(json.dumps(rules,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
 jobs=[]; cb={c['variant_id']:c for c in cases}; mb={m['role']:m for m in models}
 for c in cases:
  for role in ROLES:
   for condition in CONDITIONS:
    body=payload(mb[role],c,condition); rh=digest(body); key=digest({'role':role,'variant_id':c['variant_id'],'condition':condition,'repeat_id':1,'request_hash':rh})
    jobs.append({'key':key,'role':role,'variant_id':c['variant_id'],'condition':condition,'repeat_id':1,'request_hash':rh,'reserved_usd':str(reservation(mb[role],body))})
 random.Random(20260922).shuffle(jobs); (d/'jobs.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False,sort_keys=True)+'\n' for x in jobs))
 (d/'protocol.md').write_text((src/'protocol.md').read_text()+'\n\n## Format-control freeze v1\n\nThis 408-job supplement is frozen before execution. It does not change or replace structured primary outcomes.\n')
 total=sum((Decimal(j['reserved_usd']) for j in jobs),Decimal(0)); manifest={'study_id':'jev_triage_20260922','stage':'format_control_v1','schema_version':1,'n_jobs':len(jobs),'repeat_id':1,'max_output_tokens':4096,'total_timeout_seconds':75,'socket_timeout_seconds':50,'connect_timeout_seconds':10,'per_model_concurrency':1,'max_attempts_per_job':1,'schedule_seed':20260922,'total_reservation_usd':str(total),'software_hashes':{str(p.relative_to(ROOT)):fh(p) for pattern in ('runtime/*.py','analysis/*.py') for p in ROOT.glob(pattern)}}
 manifest['file_hashes']={n:fh(d/n) for n in ('models.json','cases.jsonl','jobs.jsonl','rules.json','protocol.md')}; manifest['freeze_digest']=digest(manifest); (d/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+'\n'); print(json.dumps({'jobs':len(jobs),'reservation_usd':str(total),'freeze_digest':manifest['freeze_digest']},ensure_ascii=False))
if __name__=='__main__': main()
