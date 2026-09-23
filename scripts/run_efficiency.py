"""Controlled efficiency runner; dry-run is the default and makes no calls."""
from __future__ import annotations
import argparse, asyncio, fcntl, hashlib, json, os, ssl, time, sys
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import httpx, certifi
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from runtime.adapters import payload, reservation, response_cost, parse_response, validate_identity
from runtime.formal_plan import digest
from runtime.project_ledger import ProjectLedger, BudgetExceeded
from scripts.freeze_efficiency import load_efficiency_freeze
from runtime.formal_runner import write_json as durable_write_json, redact

FILES={"jev":"JevAPI_openrouter.txt","luna":"openaiAPI_openrouter.txt","gemini":"GeminiAPI_openrouter.txt","deepseek":"deepseekAPI.txt"}
FATAL={"identity_error","cost_error","configuration_error","rate_limit"}
def stamp(): return datetime.now(timezone.utc).isoformat()
def write_json(p,v):
    durable_write_json(p,v)
def _redact(s,keys):
    return redact(s,keys)

class EfficiencyRunner:
    def __init__(self, manifest, models, cases, cells, jobs, ledger, run_directory, keys, client,
                 max_cells=None):
        self.m,self.models,self.cases,self.cells,self.jobs=manifest,models,cases,cells,jobs
        self.ledger,self.directory,self.keys,self.client=ledger,Path(run_directory),keys,client
        self.max_cells=max_cells; self.results={}; self.halt_reason=None; self.completed_cells=set(); self.invalid_cells=set()
        self.cell_records={}; self.errors=0
        self.admission_lock=asyncio.Lock(); self.last_admission={}; self.inflight=0; self.peak_inflight=0
        self._job_by_cell= {}
        for j in jobs: self._job_by_cell.setdefault(j["cell_id"],[]).append(j)
    async def initialize(self):
        snap=await asyncio.to_thread(self.ledger.snapshot)
        for key in snap["unresolved_keys"]:
            row=await asyncio.to_thread(self.ledger.get,key)
            if row: self.invalid_cells.add(row["metadata"].get("cell_id"))
            await asyncio.to_thread(self.ledger.mark_interrupted,key)
        for j in self.jobs:
            row=await asyncio.to_thread(self.ledger.get,j["key"])
            if not row: continue
            expected={**j,"freeze_digest":self.m["freeze_digest"],"model_requested":self.models[j["role"]]["model_requested"],"endpoint":self.models[j["role"]]["endpoint"],"software_hashes":self.m.get('software_hashes',{})}
            if any(row["metadata"].get(k)!=v for k,v in expected.items()) or row["reserved_usd"]!=j["reserved_usd"]:
                raise ValueError("efficiency_resume_metadata_mismatch")
            if row["result"]:
                self.results[j["key"]]=row["result"]
                if row["result"].get("status")=="interrupted_unknown": self.invalid_cells.add(j["cell_id"])
                if row["result"].get("status") in FATAL: raise ValueError("prior_fatal_efficiency_result")
                path=row["result"].get("response_file")
                sha=row["result"].get("response_file_sha256")
                if row['result']=={'status':'interrupted_unknown'}: continue
                if not path or not Path(path).is_file() or not sha or hashlib.sha256(Path(path).read_bytes()).hexdigest()!=sha:
                    raise ValueError("efficiency_response_evidence_mismatch")
                ev=json.loads(Path(path).read_text())
                stripped={k:v for k,v in row['result'].items() if k not in {'response_file','response_file_sha256','persistence_ms'}}
                if ev.get('job')!=j or ev.get('result')!=stripped or digest(ev.get('request_payload'))!=j['request_hash']:
                    raise ValueError('efficiency_response_result_mismatch')
        for cell in self.cells:
            cid=cell['cell_id']; jobs=self._job_by_cell[cid]; path=self.directory/'cells'/(cid+'.json')
            done=sum(j['key'] in self.results for j in jobs)
            if path.exists():
                rec=json.loads(path.read_text())
                if rec.get('cell')!=cell or rec.get('freeze_digest')!=self.m['freeze_digest']: raise ValueError('cell_metadata_mismatch')
                if rec.get('complete'):
                    if done!=len(jobs) or rec.get('response_hashes')!={j['key']:self.results[j['key']].get('response_file_sha256') for j in jobs}: raise ValueError('cell_completion_mismatch')
                    self.completed_cells.add(cid); self.cell_records[cid]=rec
                    if not rec.get('valid_timing'): self.invalid_cells.add(cid)
                    continue
            if done: self.invalid_cells.add(cid)
    async def _one(self,j, timed=False):
        if j["key"] in self.results:
            return self.results[j["key"]]
        if self.halt_reason: return {"status":"not_started","cell_id":j["cell_id"]}
        prep_start=time.monotonic(); model=self.models[j["role"]]; case=self.cases[j["variant_id"]]; body=payload(model,case)
        amount=reservation(model,body)
        if digest(body)!=j["request_hash"] or amount.__str__()!=j["reserved_usd"]: raise ValueError("efficiency_request_or_reservation_mismatch")
        meta={**j,"freeze_digest":self.m["freeze_digest"],"model_requested":model["model_requested"],"endpoint":model["endpoint"],"request_time":stamp(),"software_hashes":self.m.get('software_hashes',{})}
        meta["client_prepare_ms"]=round((time.monotonic()-prep_start)*1000,3)
        if self.halt_reason: return {"status":"not_started","cell_id":j["cell_id"]}
        try:
            fresh=await asyncio.to_thread(self.ledger.reserve,j['key'],j['reserved_usd'],meta)
        except BudgetExceeded:
            self.halt_reason='project_budget_exhausted'; return {'status':'not_started'}
        if not fresh:
            self.halt_reason='unexpected_existing_reservation'; raise ValueError(self.halt_reason)
        result={"status":"request_error","role":j["role"],"cell_id":j["cell_id"],"phase":j["phase"],"position":j["position"],"variant_id":j["variant_id"],"cost_usd":None,"cost_status":"unknown","client_prepare_ms":meta["client_prepare_ms"]}
        raw=None
        gate_start=time.monotonic()
        spacing=self.m.get('admission_spacing_seconds',{}).get(j['role'],0)
        if spacing:
            async with self.admission_lock:
                await asyncio.sleep(max(0,self.last_admission.get(j['role'],0)+spacing-time.monotonic()))
                self.last_admission[j['role']]=time.monotonic()
        result['admission_wait_ms']=round((time.monotonic()-gate_start)*1000,3)
        transport_start=time.monotonic(); result["request_started_at"]=stamp(); counted=False
        try:
            if self.halt_reason: raise RuntimeError('not_sent_after_halt')
            self.inflight+=1; counted=True; self.peak_inflight=max(self.peak_inflight,self.inflight)
            response=await asyncio.wait_for(self.client.post(model["endpoint"],json=body,headers={"Authorization":"Bearer "+self.keys[j["role"]],"Content-Type":"application/json"}),self.m.get('total_timeout_seconds',75))
            self.inflight-=1; counted=False
            result["transport_ms"]=round((time.monotonic()-transport_start)*1000,3); result["http_duration_ms"]=result["transport_ms"]; result["http_status"]=response.status_code
            raw=_redact(response.text,list(self.keys.values()))
            try: data=json.loads(raw)
            except (TypeError,json.JSONDecodeError): data=None
            if isinstance(data,dict):
                result['usage']=data.get('usage'); result['model_returned']=data.get('model'); result['provider_returned']=data.get('provider','DeepSeek' if j['role']=='deepseek' else None)
                result['response_id']=data.get('id')
                result['cost_usd'],result['cost_status']=response_cost(model,data)
            if result['cost_usd'] is not None and Decimal(result['cost_usd'])>amount:
                result.update(status='cost_error',error_code='cost_exceeds_reservation')
            elif response.status_code == 429 or response.status_code == 402:
                result.update(status="rate_limit",error_code="http_"+str(response.status_code))
            elif response.status_code != 200:
                result.update(status="configuration_error" if 400 <= response.status_code < 500 else "request_error",error_code="http_"+str(response.status_code))
            elif not isinstance(data,dict):
                result.update(status="parse_error",error_code="response_not_json_object")
            else:
                result["usage"]=data.get("usage"); result["model_returned"]=data.get("model"); result["provider_returned"]=data.get("provider","DeepSeek" if j["role"]=="deepseek" else None)
                result["cost_usd"],result["cost_status"]=response_cost(model,data)
                if result["cost_usd"] is None: result.update(status="cost_error",error_code="successful_response_cost_unknown")
                elif Decimal(result["cost_usd"])>amount: result.update(status="cost_error",error_code="cost_exceeds_reservation")
                else:
                    try: validate_identity(model,data)
                    except (ValueError,TypeError) as e: result.update(status="identity_error",error_code=type(e).__name__)
                    else:
                        try: result["parsed"]=parse_response(model,data); result["status"]="success"
                        except (ValueError,TypeError,KeyError) as e:
                            choice=(data.get("choices") or [{}])[0] if isinstance(data,dict) else {}
                            refusal=j["role"]!="jev" and isinstance(choice,dict) and (choice.get("finish_reason")=="content_filter" or bool((choice.get("message") or {}).get("refusal")))
                            result.update(status="refusal" if refusal else "parse_error",error_code=type(e).__name__)
        except (asyncio.TimeoutError,httpx.TimeoutException): result.update(status="timeout",error_code="timeout")
        except (httpx.HTTPError, json.JSONDecodeError): result.update(status="request_error",error_code="transport_error")
        except RuntimeError as e:
            if str(e)=='not_sent_after_halt': result.update(status='not_sent_after_halt',cost_usd='0',cost_status='not_sent',transport_ms=0,http_duration_ms=0)
            else: result.update(status='configuration_error',error_code=type(e).__name__)
        except Exception as e: result.update(status="configuration_error",error_code=type(e).__name__)
        finally:
            if counted: self.inflight-=1
        if 'transport_ms' not in result: result['transport_ms']=result['http_duration_ms']=round((time.monotonic()-transport_start)*1000,3)
        result["finished_at"]=stamp()
        if result['status'] in FATAL: self.halt_reason=result.get('error_code',result['status'])
        self.errors=self.errors+1 if result['status'] in {'timeout','request_error'} else 0
        if self.errors>=3: self.halt_reason='three_consecutive_transport_errors'
        persistence_start=time.monotonic()
        evidence=self.directory/"responses"/(j["key"]+".json")
        durable_write_json(evidence,{"job":j,"request_payload":body,"result":result,"raw_response":raw},exclusive=True)
        result['response_file']=str(evidence); result['persistence_ms']=round((time.monotonic()-persistence_start)*1000,3); result["response_file_sha256"]=hashlib.sha256(evidence.read_bytes()).hexdigest()
        await asyncio.to_thread(self.ledger.finish,j["key"],result); self.results[j["key"]]=result
        if result["status"] in FATAL: self.halt_reason=result.get("error_code",result["status"])
        return result
    async def _cell(self,cell):
        if cell['cell_id'] in self.completed_cells: return True
        cell_start=time.monotonic(); cell_started=stamp(); self.errors=0
        jobs=sorted(self._job_by_cell[cell["cell_id"]],key=lambda x:(x["phase"]!="warmup",x["position"]))
        for j in jobs:
            if self.halt_reason: break
            if j["phase"]=="warmup":
                r=await self._one(j)
        warmup_ms=round((time.monotonic()-cell_start)*1000,3)
        measured=[j for j in jobs if j["phase"]=="measured"]
        queue=asyncio.Queue()
        for j in measured:
            if j['key'] not in self.results: queue.put_nowait(j)
        async def go():
            local=[]
            while not self.halt_reason:
                try: j=queue.get_nowait()
                except asyncio.QueueEmpty: break
                try: local.append(await self._one(j,True))
                except Exception as exc:
                    self.halt_reason='local_failure_'+type(exc).__name__; raise
                finally: queue.task_done()
            return local
        measured_start=time.monotonic(); measured_at=stamp(); self.peak_inflight=0
        groups=await asyncio.gather(*(go() for _ in range(cell['concurrency'])),return_exceptions=True)
        elapsed=round((time.monotonic()-measured_start)*1000,3)
        complete=all(j['key'] in self.results for j in jobs)
        rec={'cell':cell,'freeze_digest':self.m['freeze_digest'],'started_at':cell_started,'measured_started_at':measured_at,'ended_at':stamp(),
             'walltime_ms':round((time.monotonic()-cell_start)*1000,3),'warmup_walltime_ms':warmup_ms,'measured_walltime_ms':elapsed,
             'planned':len(jobs),'completed':sum(j['key'] in self.results for j in jobs),'complete':complete,
             'peak_http_inflight_measured':self.peak_inflight,'admission_spacing_seconds':self.m.get('admission_spacing_seconds',{}).get(cell['role'],0),
             'measured_valid':sum(self.results.get(j['key'],{}).get('status')=='success' for j in measured),
             'valid_timing':complete and cell['cell_id'] not in self.invalid_cells and not self.halt_reason,'halt_reason':self.halt_reason,
             'response_hashes':{j['key']:self.results[j['key']].get('response_file_sha256') for j in jobs if j['key'] in self.results}}
        p=self.directory/'cells'/(cell['cell_id']+'.json')
        if p.exists(): durable_write_json(self.directory/'cell_segments'/(cell['cell_id']+'_'+str(time.time_ns())+'.json'),json.loads(p.read_text()),exclusive=True)
        write_json(p,rec); self.cell_records[cell['cell_id']]=rec
        if complete: self.completed_cells.add(cell['cell_id'])
        if not rec['valid_timing']: self.invalid_cells.add(cell['cell_id'])
        return not self.halt_reason
    async def run(self):
        await self.initialize(); await self.progress(); count=0
        for cell in sorted(self.cells,key=lambda x:x["order"]):
            if self.halt_reason or (self.max_cells is not None and count>=self.max_cells): break
            if cell['cell_id'] in self.completed_cells: continue
            await self._cell(cell); count+=1; await self.progress()
        return await self.progress()
    async def progress(self):
        report={"updated_at":stamp(),"freeze_digest":self.m["freeze_digest"],"planned":len(self.jobs),"completed":len(self.results),"status_counts":dict(Counter(r["status"] for r in self.results.values())),"halt_reason":self.halt_reason,"complete":len(self.results)==len(self.jobs),"invalid_cells":sorted(x for x in self.invalid_cells if x)}
        snap=await asyncio.to_thread(self.ledger.snapshot)
        report.update(reserved_project_usd=snap['reserved_usd'],known_cost_usd=str(sum((Decimal(r['cost_usd']) for r in self.results.values() if r.get('cost_usd') is not None),Decimal(0))),unknown_cost_attempts=sum(r.get('cost_usd') is None for r in self.results.values()),cells_complete=len(self.completed_cells))
        write_json(self.directory/"progress.json",report); return report

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--execute",action="store_true"); ap.add_argument("--credentials-directory",type=Path); ap.add_argument("--freeze",type=Path,default=ROOT/"freezes/efficiency_v1"); ap.add_argument("--run-directory",type=Path,default=ROOT/"runs/efficiency_v1"); ap.add_argument("--max-cells",type=int)
    a=ap.parse_args(); m,models,cases,cells,jobs=load_efficiency_freeze(a.freeze); total=sum((Decimal(j["reserved_usd"]) for j in jobs),Decimal(0)); print(json.dumps({"execute":a.execute,"jobs":len(jobs),"cells":len(cells),"reservation_usd":str(total)}))
    if not a.execute:return
    config=json.loads((ROOT/"configs/project_budget.json").read_text())
    if not config.get("live_enabled") or config.get("efficiency_v1_freeze_digest") != m["freeze_digest"]:
        raise ValueError("efficiency_live_gate_closed_or_digest_mismatch")
    if not a.credentials_directory: raise ValueError("credentials_missing")
    from runtime.formal_runner import read_keys,budget_evidence
    with (ROOT/"runs/project_runner.lock").open("a") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        config,prior,prior_sha=budget_evidence(); ledger=ProjectLedger(ROOT/"runs/project_budget.sqlite","jev_triage_20260922","180",prior,prior_sha); keys=read_keys(a.credentials_directory,models)
        if not config.get('live_enabled') or config.get('efficiency_v1_freeze_digest')!=m['freeze_digest']: raise ValueError('efficiency_gate_changed')
        async def go():
            async with httpx.AsyncClient(verify=ssl.create_default_context(cafile=certifi.where()),timeout=httpx.Timeout(m['socket_timeout_seconds'],connect=m['connect_timeout_seconds']),follow_redirects=False,limits=httpx.Limits(max_connections=32,max_keepalive_connections=32)) as c:return await EfficiencyRunner(m,models,cases,cells,jobs,ledger,a.run_directory,keys,c,a.max_cells).run()
        try:
            remaining=sum((Decimal(j['reserved_usd']) for j in jobs if ledger.get(j['key']) is None),Decimal(0))
            if Decimal(ledger.snapshot()['reserved_usd'])+remaining>180: raise ValueError('stage_budget_exceeds_remaining')
            out=asyncio.run(go())
        finally:
            current=json.loads((ROOT/'configs/project_budget.json').read_text()); current['live_enabled']=False; current['status']='efficiency_v1_stopped_live_closed'
            current['remaining_automatic_reservation_capacity_usd']=str(Decimal('180')-Decimal(ledger.snapshot()['reserved_usd']))
            write_json(ROOT/'configs/project_budget.json',current)
    if out["halt_reason"]: raise SystemExit(2)
if __name__=="__main__":main()
