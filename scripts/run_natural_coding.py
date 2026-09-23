"""Run the immutable, blinded natural-language coding freeze."""
from __future__ import annotations
import argparse, asyncio, certifi, fcntl, hashlib, json, ssl, sys, time, httpx
from collections import Counter
from decimal import Decimal
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from supplements.coding import payload, reservation, extract_response, ROLES
from runtime.adapters import response_cost, validate_identity
from runtime.formal_runner import budget_evidence, read_keys, redact, write_json, stamp
from runtime.project_ledger import ProjectLedger, BudgetExceeded
FATAL={'identity_error','cost_error','configuration_error'}

def fh(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def canon(v): return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
def load_freeze(d):
    d=Path(d); m=json.loads((d/'manifest.json').read_text());
    if m.get('stage') != 'natural_coding_v1' or m.get('study_id') != 'jev_triage_20260922' or m.get('max_output_tokens') != 4096:
        raise ValueError('coding_stage_mismatch')
    if set(m.get('file_hashes', {})) != {'models.json','source_answers.jsonl','jobs.jsonl','rules.json','protocol.md'}:
        raise ValueError('coding_file_roster')
    for n,h in m['file_hashes'].items():
        if fh(d/n)!=h: raise ValueError('frozen_file_changed')
    if hashlib.sha256(canon({k:v for k,v in m.items() if k!='freeze_digest'})).hexdigest()!=m['freeze_digest']: raise ValueError('freeze_manifest_changed')
    models={x['role']:x for x in json.loads((d/'models.json').read_text())}; sources=[json.loads(x) for x in (d/'source_answers.jsonl').read_text().splitlines() if x]; jobs=[json.loads(x) for x in (d/'jobs.jsonl').read_text().splitlines() if x]
    if len(sources)!=204 or len(jobs)!=408 or set(models)!=set(ROLES): raise ValueError('coding_freeze_count')
    src={x['source_key']:x for x in sources}
    from postanalysis.format_explanation import verify_format_freeze, _read_jobs_ledger
    sm, source_models, _, source_jobs = verify_format_freeze(ROOT/'freezes/format_control_v1')
    if sm['freeze_digest'] != m['source_freeze_digest'] or source_models != models:
        raise ValueError('coding_original_freeze_mismatch')
    source_evidence = _read_jobs_ledger(ROOT/'runs/project_budget.sqlite',source_jobs,sm,source_models)
    source_jobs = {j['key']:j for j in source_jobs if j['condition']=='natural_language'}
    if set(src) != set(source_jobs): raise ValueError('coding_original_grid_mismatch')
    if len(src)!=204 or len({j['key'] for j in jobs})!=408: raise ValueError('coding_keys_not_unique')
    expected_grid={(s['source_key'],r) for s in sources for r in ROLES if r!=s['source_role']}
    actual={(j['source_key'],j['coder_role']) for j in jobs}
    if actual!=expected_grid or any(j['source_role']==j['coder_role'] for j in jobs): raise ValueError('coding_cartesian_mismatch')
    for s in sources:
        original = source_jobs[s['source_key']]
        if any(s[k] != original[v] for k,v in [('source_role','role'),('variant_id','variant_id'),('repeat_id','repeat_id')]):
            raise ValueError('coding_source_identity_mismatch')
        if s['transformation'] != 'none' or s['answer_text'] != source_evidence[s['source_key']][2]['result']['assistant_text']:
            raise ValueError('coding_source_text_mismatch')
        if hashlib.sha256(s['answer_text'].encode()).hexdigest()!=s['answer_text_sha256']: raise ValueError('source_answer_hash_mismatch')
        source_path=Path(s['source_response_file'])
        if not source_path.exists() or fh(source_path)!=s['source_response_sha256']: raise ValueError('source_response_changed')
    if len(m.get('source_file_hashes',{})) != 204: raise ValueError('source_file_manifest_incomplete')
    for path, expected in m['source_file_hashes'].items():
        if not Path(path).exists() or fh(Path(path)) != expected: raise ValueError('source_file_changed')
    for j in jobs:
        s=src.get(j['source_key']);
        if not s or s['answer_text_sha256']!=j['answer_text_sha256']: raise ValueError('coding_source_mismatch')
        if any(s[k] != j[k] for k in ('source_role','variant_id','repeat_id')): raise ValueError('coding_source_metadata_mismatch')
        body=payload(models[j['coder_role']],s['answer_text'],m['max_output_tokens'])
        if hashlib.sha256(canon(body)).hexdigest()!=j['request_hash'] or reservation(models[j['coder_role']],body,m['max_output_tokens'])!=Decimal(j['reserved_usd']): raise ValueError('coding_job_mismatch')
        base={k:j[k] for k in ('source_key','source_role','coder_role','variant_id','repeat_id','answer_text_sha256','request_hash')}
        if hashlib.sha256(canon(base)).hexdigest()!=j['key']: raise ValueError('coding_job_key_mismatch')
    if sum((Decimal(j['reserved_usd']) for j in jobs),Decimal(0)) != Decimal(m['total_reservation_usd']): raise ValueError('coding_total_mismatch')
    if not {'scripts/run_natural_coding.py','scripts/freeze_natural_coding.py','supplements/coding.py','runtime/adapters.py','runtime/project_ledger.py','runtime/formal_runner.py'}.issubset(m.get('software_hashes',{})):
        raise ValueError('coding_software_roster')
    for name, expected in m.get('software_hashes',{}).items():
        if fh(ROOT/name)!=expected: raise ValueError('frozen_software_changed')
    return m,models,src,jobs

class Runner:
    def __init__(self,m,models,sources,jobs,ledger,keys,client,directory): self.m=m;self.models=models;self.sources=sources;self.jobs=jobs;self.ledger=ledger;self.keys=keys;self.client=client;self.directory=Path(directory);self.results={};self.stop=asyncio.Event();self.new=0;self.halt_reason=None
    async def init(self):
        snap=await asyncio.to_thread(self.ledger.snapshot)
        for k in snap['unresolved_keys']: await asyncio.to_thread(self.ledger.mark_interrupted,k)
        for j in self.jobs:
            row=await asyncio.to_thread(self.ledger.get,j['key'])
            if not row: continue
            expected={**j,'freeze_digest':self.m['freeze_digest'],'model_requested':self.models[j['coder_role']]['model_requested'],'endpoint':self.models[j['coder_role']]['endpoint'],'software_hashes':self.m['software_hashes']}
            if any(row['metadata'].get(k)!=v for k,v in expected.items()) or Decimal(row['reserved_usd'])!=Decimal(j['reserved_usd']): raise ValueError('existing_attempt_request_mismatch')
            if row['result']:
                result=row['result']; path=Path(result.get('response_file',''))
                if result == {'status': 'interrupted_unknown'}:
                    self.results[j['key']] = result
                    continue
                if not path.exists() or fh(path)!=result.get('response_file_sha256'): raise ValueError('resume_response_hash_mismatch')
                evidence=json.loads(path.read_text()); stripped={k:v for k,v in result.items() if k not in {'response_file','response_file_sha256'}}
                if evidence.get('job')!=j or evidence.get('result')!=stripped or hashlib.sha256(canon(evidence.get('request_payload'))).hexdigest()!=j['request_hash']: raise ValueError('resume_evidence_mismatch')
                self.results[j['key']]=result
                if result.get('status') in FATAL: raise ValueError('prior_fatal_contract_failure')
    async def perform(self,j):
        if self.stop.is_set(): return
        model=self.models[j['coder_role']]; s=self.sources[j['source_key']]; body=payload(model,s['answer_text'],self.m['max_output_tokens']); amount=reservation(model,body,self.m['max_output_tokens'])
        if hashlib.sha256(canon(body)).hexdigest()!=j['request_hash'] or amount!=Decimal(j['reserved_usd']): raise ValueError('frozen_request_or_reservation_changed')
        meta={**j,'freeze_digest':self.m['freeze_digest'],'model_requested':model['model_requested'],'endpoint':model['endpoint'],'software_hashes':self.m['software_hashes'],'request_time':stamp()}
        if not await asyncio.to_thread(self.ledger.reserve,j['key'],str(amount),meta): return
        self.new+=1; start=time.monotonic(); result={'status':'request_error','coder_role':j['coder_role'],'source_key':j['source_key'],'variant_id':j['variant_id'],'cost_usd':None,'cost_status':'unknown','request_time':meta['request_time']}; raw=None
        try:
            resp=await asyncio.wait_for(self.client.post(model['endpoint'],json=body,headers={'Authorization':'Bearer '+self.keys[j['coder_role']],'Content-Type':'application/json'}),timeout=self.m['total_timeout_seconds']); result['http_status']=resp.status_code; raw=redact(resp.text,list(self.keys.values()))
            try: data=json.loads(raw) if raw else None
            except (TypeError,json.JSONDecodeError): data=None
            if isinstance(data,dict): result.update({'usage':data.get('usage'),'model_returned':data.get('model'),'provider_returned':data.get('provider'),'response_id':data.get('id')}); result['cost_usd'],result['cost_status']=response_cost(model,data)
            if result['cost_usd'] is not None and Decimal(result['cost_usd'])>amount: result.update(status='cost_error',error_code='cost_exceeds_permanent_reservation'); self.halt_reason='cost_error'; self.stop.set()
            elif resp.status_code==429: result.update(status='configuration_error',error_code='http_429'); self.halt_reason='http_429'; self.stop.set()
            elif resp.status_code!=200:
                result.update(status='configuration_error' if resp.status_code in (400,401,402,403,404,422) else 'request_error',error_code='http_'+str(resp.status_code))
                if result['status']=='configuration_error': self.halt_reason=result['error_code']; self.stop.set()
            elif not isinstance(data,dict): result.update(status='parse_error',error_code='response_not_json_object')
            else:
                try: validate_identity(model,data)
                except (ValueError,TypeError): result.update(status='identity_error',error_code='unexpected_model_or_provider'); self.halt_reason='identity_error'; self.stop.set()
                if result['status']=='identity_error': pass
                elif result['cost_usd'] is None: result.update(status='cost_error',error_code='successful_response_cost_unknown'); self.halt_reason='cost_error'; self.stop.set()
                elif Decimal(result['cost_usd'])>amount: result.update(status='cost_error',error_code='cost_exceeds_permanent_reservation'); self.halt_reason='cost_error'; self.stop.set()
                else:
                    msg=(data.get('choices') or [{}])[0].get('message') or {}
                    if msg.get('refusal'):
                        result['status']='refusal'; result['error_code']='provider_refusal'
                    else:
                        result['parsed']=extract_response(data,s['answer_text']); result['status']='success'
        except (asyncio.TimeoutError,httpx.TimeoutException): result['status']='timeout'
        except httpx.HTTPError: result.update(status='request_error',error_code='transport_error')
        except Exception as exc: result.update(status='parse_error' if isinstance(exc,(ValueError,TypeError,KeyError,json.JSONDecodeError)) else 'configuration_error',error_code=type(exc).__name__)
        if result['status'] in FATAL and not self.halt_reason: self.halt_reason=result.get('error_code',result['status']); self.stop.set()
        result.update(response_time=stamp(),latency_ms=round((time.monotonic()-start)*1000,3)); path=self.directory/'responses'/(j['key']+'.json'); write_json(path,{'job':j,'request_payload':body,'result':result,'raw_response':raw},exclusive=True); result['response_file']=str(path); result['response_file_sha256']=fh(path); await asyncio.to_thread(self.ledger.finish,j['key'],result); self.results[j['key']]=result
    async def progress(self):
        snap=await asyncio.to_thread(self.ledger.snapshot); costs=[Decimal(x['cost_usd']) for x in self.results.values() if x.get('cost_usd') is not None]; report={'updated_at':stamp(),'planned':len(self.jobs),'completed':len(self.results),'status_counts':dict(Counter(x['status'] for x in self.results.values())),'reserved_project_usd':snap['reserved_usd'],'known_cost_usd':str(sum(costs,Decimal(0))),'unknown_cost_attempts':sum(x.get('cost_usd') is None for x in self.results.values()),'new_attempts_this_invocation':self.new,'halt_reason':self.halt_reason,'complete':len(self.results)==len(self.jobs)}; write_json(self.directory/'progress.json',report); return report
    async def worker(self,role):
        transport_failures=0
        for j in self.jobs:
            if j['coder_role']!=role or j['key'] in self.results or self.stop.is_set(): continue
            try: await self.perform(j)
            except BudgetExceeded: self.halt_reason='project_budget_exhausted'; self.stop.set(); return
            except Exception as exc:
                self.halt_reason='local_failure_'+type(exc).__name__; self.stop.set(); raise
            if self.results.get(j['key'],{}).get('status') in {'timeout','request_error'}:
                transport_failures+=1
                if transport_failures>=3: self.halt_reason='three_consecutive_transport_failures'; self.stop.set(); return
            else: transport_failures=0
            if len(self.results)%25==0: await self.progress()
    async def run(self): await self.init(); await self.progress(); await asyncio.gather(*(self.worker(r) for r in ROLES)); await self.progress(); return self.results

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--execute',action='store_true'); ap.add_argument('--freeze',type=Path,default=ROOT/'freezes/natural_coding_v1'); ap.add_argument('--run-directory',type=Path,default=ROOT/'runs/natural_coding_v1'); ap.add_argument('--credentials-directory',type=Path); a=ap.parse_args(); m,models,sources,jobs=load_freeze(a.freeze); config,prior,prior_sha=budget_evidence(); print(json.dumps({'execute':a.execute,'jobs':len(jobs),'reserved_usd':m['total_reservation_usd']}),flush=True)
    if not a.execute:return
    if not config.get('live_enabled') or config.get('natural_coding_v1_freeze_digest')!=m['freeze_digest'] or not a.credentials_directory: raise ValueError('live_disabled_or_freeze_gate_missing')
    a.run_directory.mkdir(parents=True,exist_ok=True)
    with (ROOT/'runs/project_runner.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB); ledger=ProjectLedger(ROOT/'runs/project_budget.sqlite','jev_triage_20260922','180',prior,prior_sha); keys=read_keys(a.credentials_directory,models)
        async def go():
            ctx=ssl.create_default_context(cafile=certifi.where()); timeout=httpx.Timeout(m['socket_timeout_seconds'],connect=m['connect_timeout_seconds'])
            async with httpx.AsyncClient(verify=ctx,timeout=timeout,follow_redirects=False,limits=httpx.Limits(max_connections=3,max_keepalive_connections=3)) as client:return await Runner(m,models,sources,jobs,ledger,keys,client,a.run_directory).run()
        remaining = sum((Decimal(j['reserved_usd']) for j in jobs if ledger.get(j['key']) is None), Decimal(0))
        if Decimal(ledger.snapshot()['reserved_usd']) + remaining > Decimal('180'):
            raise ValueError('stage_exceeds_remaining_budget')
        try:
            out=asyncio.run(go())
        finally:
            current=json.loads((ROOT/'configs/project_budget.json').read_text())
            current['live_enabled']=False
            current['status']='natural_coding_v1_stopped_live_closed'
            current['remaining_automatic_reservation_capacity_usd']=str(Decimal('180')-Decimal(ledger.snapshot()['reserved_usd']))
            write_json(ROOT/'configs/project_budget.json',current)
        if len(out)!=408: raise SystemExit(2)
if __name__=='__main__': main()
