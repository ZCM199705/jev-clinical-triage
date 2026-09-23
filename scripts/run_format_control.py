"""Execute the frozen 408-job format control; one attempt per job."""
from __future__ import annotations
import argparse,asyncio,certifi,fcntl,hashlib,json,re,ssl,time
from collections import Counter
from datetime import datetime,timezone
from decimal import Decimal
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(ROOT))
from runtime.format_control import payload,reservation
from runtime.adapters import response_cost,validate_identity
from runtime.formal_runner import budget_evidence,read_keys,redact,write_json,stamp
from runtime.project_ledger import ProjectLedger,BudgetExceeded
ROLES=('luna','gemini','deepseek')
FILES={'luna':'openaiAPI_openrouter.txt','gemini':'GeminiAPI_openrouter.txt','deepseek':'deepseekAPI.txt'}
def load_freeze(d):
 d=Path(d); m=json.loads((d/'manifest.json').read_text()); models={x['role']:x for x in json.loads((d/'models.json').read_text())}; cases={json.loads(x)['variant_id']:json.loads(x) for x in (d/'cases.jsonl').read_text().splitlines() if x}; jobs=[json.loads(x) for x in (d/'jobs.jsonl').read_text().splitlines() if x]
 if m['n_jobs']!=408 or len(jobs)!=408 or set(models)!=set(ROLES) or len(cases)!=68: raise ValueError('format_freeze_count')
 if len({j['key'] for j in jobs})!=408: raise ValueError('format_keys_not_unique')
 for j in jobs:
  body=payload(models[j['role']],cases[j['variant_id']],j['condition']);
  if hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()!=j['request_hash'] or reservation(models[j['role']],body)!=Decimal(j['reserved_usd']): raise ValueError('format_job_mismatch')
 return m,models,cases,jobs
class Runner:
 def __init__(self,m,models,cases,jobs,ledger,keys,client,directory): self.m=m;self.models=models;self.cases=cases;self.jobs=jobs;self.ledger=ledger;self.keys=keys;self.client=client;self.directory=directory;self.results={};self.stop=asyncio.Event();self.lock=asyncio.Lock();self.new=0
 async def init(self):
  for j in self.jobs:
   row=await asyncio.to_thread(self.ledger.get,j['key'])
   if row:
    if row['metadata'].get('condition')!=j['condition'] or row['metadata'].get('format_stage')!='format_control_v1': raise ValueError('existing_format_metadata_mismatch')
    if row['result']: self.results[j['key']]=row['result']
 async def perform(self,j):
  model=self.models[j['role']]; body=payload(model,self.cases[j['variant_id']],j['condition'],self.m['max_output_tokens']); amount=reservation(model,body,self.m['max_output_tokens']);
  if amount!=Decimal(j['reserved_usd']): raise ValueError('format_reservation_changed')
  meta={**j,'format_stage':'format_control_v1','freeze_digest':self.m['freeze_digest'],'model_requested':model['model_requested'],'endpoint':model['endpoint'],'software_hashes':self.m['software_hashes'],'request_time':stamp()}
  if not await asyncio.to_thread(self.ledger.reserve,j['key'],str(amount),meta): return
  self.new+=1; started=time.monotonic(); result={'status':'request_error','role':j['role'],'variant_id':j['variant_id'],'condition':j['condition'],'repeat_id':1,'cost_usd':None,'cost_status':'unknown','request_time':meta['request_time']}; raw=None
  try:
   resp=await asyncio.wait_for(self.client.post(model['endpoint'],json=body,headers={'Authorization':'Bearer '+self.keys[j['role']],'Content-Type':'application/json'}),timeout=self.m['total_timeout_seconds']); result['http_status']=resp.status_code; raw=redact(resp.text,list(self.keys.values())); data=json.loads(raw) if raw else None
   if isinstance(data,dict):
    result.update({'usage':data.get('usage'),'model_returned':data.get('model'),'provider_returned':data.get('provider'),'response_id':data.get('id')}); result['cost_usd'],result['cost_status']=response_cost(model,data)
   if resp.status_code!=200: result['status']='configuration_error' if resp.status_code in (400,401,402,403,404,422) else 'request_error'
   elif not isinstance(data,dict): result['status']='parse_error'
   else:
    validate_identity(model,data)
    if result['cost_usd'] is None: result['status']='cost_error'
    else:
     choice=(data.get('choices') or [{}])[0]; msg=(choice.get('message') or {}) if isinstance(choice,dict) else {}; content=msg.get('content')
     result['assistant_text']=content if isinstance(content,str) else json.dumps(content,ensure_ascii=False) if content is not None else None
     result['status']='success'
  except asyncio.TimeoutError: result['status']='timeout'
  except httpx.HTTPError: result['status']='request_error'; result['error_code']='transport_error'
  except Exception as e: result['status']='parse_error' if isinstance(e,(ValueError,TypeError,KeyError,json.JSONDecodeError)) else 'configuration_error'; result['error_code']=type(e).__name__
  result.update({'response_time':stamp(),'latency_ms':round((time.monotonic()-started)*1000,3)}); evidence={'job':j,'request_payload':body,'result':result,'raw_response':raw}; path=self.directory/'responses'/(j['key']+'.json'); write_json(path,evidence,exclusive=True); result['response_file']=str(path); result['response_file_sha256']=hashlib.sha256(path.read_bytes()).hexdigest(); await asyncio.to_thread(self.ledger.finish,j['key'],result); self.results[j['key']]=result
 async def worker(self,role):
  for j in self.jobs:
   if j['role']!=role or j['key'] in self.results: continue
   if self.stop.is_set(): return
   try: await self.perform(j)
   except BudgetExceeded: self.stop.set(); return
   if len(self.results)%25==0: await self.progress()
   await asyncio.sleep(1)
 async def progress(self):
  snap=await asyncio.to_thread(self.ledger.snapshot); costs=[Decimal(x['cost_usd']) for x in self.results.values() if x.get('cost_usd') is not None]; report={'updated_at':stamp(),'planned':len(self.jobs),'completed':len(self.results),'status_counts':dict(Counter(x['status'] for x in self.results.values())),'reserved_project_usd':snap['reserved_usd'],'known_cost_usd':str(sum(costs,Decimal(0))),'unknown_cost_attempts':sum(x.get('cost_usd') is None for x in self.results.values()),'complete':len(self.results)==len(self.jobs)}; write_json(self.directory/'progress.json',report); print(json.dumps(report,ensure_ascii=False),flush=True); return report
 async def run(self): await self.init(); await self.progress(); await asyncio.gather(*(self.worker(r) for r in ROLES)); return await self.progress()
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--execute',action='store_true'); ap.add_argument('--credentials-directory',type=Path); ap.add_argument('--freeze',type=Path,default=ROOT/'freezes/format_control_v1'); ap.add_argument('--run-directory',type=Path,default=ROOT/'runs/format_control_v1'); a=ap.parse_args(); m,models,cases,jobs=load_freeze(a.freeze); budget,prior,prior_sha=budget_evidence(); total=sum((Decimal(j['reserved_usd']) for j in jobs),Decimal(0)); print(json.dumps({'execute':a.execute,'jobs':len(jobs),'reserved_usd':str(total),'prior_reservation_usd':prior},ensure_ascii=False),flush=True)
 if not a.execute:return
 if not budget['live_enabled'] or not a.credentials_directory: raise ValueError('live_disabled_or_credentials_missing')
 a.run_directory.mkdir(parents=True,exist_ok=True)
 with (ROOT/'runs/project_runner.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB); ledger=ProjectLedger(ROOT/'runs/project_budget.sqlite','jev_triage_20260922','180',prior,prior_sha); keys=read_keys(a.credentials_directory,models)
  async def go():
   ssl_context=ssl.create_default_context(cafile=certifi.where()); timeout=httpx.Timeout(m['socket_timeout_seconds'],connect=m['connect_timeout_seconds'])
   async with httpx.AsyncClient(verify=ssl_context,timeout=timeout,follow_redirects=False,limits=httpx.Limits(max_connections=3,max_keepalive_connections=3)) as client:return await Runner(m,models,cases,jobs,ledger,keys,client,a.run_directory).run()
  outcome=asyncio.run(go())
  if not outcome['complete']: raise SystemExit(2)
if __name__=='__main__': main()
