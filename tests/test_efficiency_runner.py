import asyncio, json, tempfile, time, unittest
import httpx
from pathlib import Path
from runtime.adapters import payload, reservation
from runtime.formal_plan import digest
from runtime.project_ledger import ProjectLedger
from scripts.run_efficiency import EfficiencyRunner
from scripts.freeze_efficiency import build_freeze, load_efficiency_freeze

MODEL={"role":"luna","model_requested":"openai/gpt-5.6-luna","endpoint":"https://openrouter.ai/api/v1/chat/completions","provider":"OpenAI","provider_tag":"openai","price_in":"0.20","price_out":"1.20"}
CASE={"variant_id":"v1","case_text":"A short case","scenario_id":"s1"}; MANIFEST={"freeze_digest":"freeze-test","max_output_tokens":4096}
class Response:
 def __init__(self,status=200,data=None,text=None): self.status_code=status; self.text=text if text is not None else json.dumps(data or {})
class Transport:
 def __init__(self,responses,delays=None): self.responses=list(responses); self.delays=list(delays or [0]*len(self.responses)); self.calls=0; self.active=0; self.max_active=0; self.started=[]; self.finished=[]; self.finished_by_index={}
 async def post(self,*a,**k):
  i=self.calls; self.calls+=1; self.active+=1; self.max_active=max(self.max_active,self.active); self.started.append(time.monotonic()); await asyncio.sleep(self.delays[i] if i<len(self.delays) else 0); self.active-=1; self.finished.append(time.monotonic()); self.finished_by_index[i]=self.finished[-1]; item=self.responses[i]
  if isinstance(item,BaseException): raise item
  return item
def ok(cost="0.0001"): return Response(data={"id":"id","model":MODEL["model_requested"],"provider":"OpenAI","usage":{"cost":cost},"choices":[{"finish_reason":"stop","message":{"content":"{\"triage\":\"A\"}"}}]})
def fixture(phases=("measured",),concurrency=2,n_each=1):
 body=payload(MODEL,CASE); jobs=[]
 for pos,phase in enumerate(phases):
  for _ in range(n_each):
   ident={"stage":"efficiency_v1","cell_id":"w1_luna_c2","phase":phase,"position":len(jobs),"role":"luna","variant_id":"v1","request_hash":digest(body)}; jobs.append({**ident,"key":digest(ident),"reserved_usd":str(reservation(MODEL,body))})
 cell={"cell_id":"w1_luna_c2","wave":1,"role":"luna","concurrency":concurrency,"order":0,"measured_requests":n_each,"warmup_requests":n_each if "warmup" in phases else 0}
 return {"luna":MODEL},{"v1":CASE},[cell],jobs
def make_runner(tmp,jobs,cells,t,cap="10"):
 ledger=ProjectLedger(Path(tmp)/"ledger.sqlite","test-project",cap,"0","prior-evidence")
 return EfficiencyRunner(MANIFEST,{"luna":MODEL},{"v1":CASE},cells,jobs,ledger,Path(tmp)/"run",{"luna":"sk-test"},t)
class Tests(unittest.TestCase):
 def test_warmup_excluded_and_walltime(self):
  models,cases,cells,jobs=fixture(("warmup","measured"),1)
  with tempfile.TemporaryDirectory() as d:
   asyncio.run(make_runner(d,jobs,cells,Transport([ok(),ok()],[.03,.005])).run()); rec=json.loads((Path(d)/"run/cells/w1_luna_c2.json").read_text()); self.assertIn("measured_walltime_ms",rec); self.assertIn("warmup_walltime_ms",rec); self.assertTrue(rec["walltime_ms"]>0)
 def test_sustained_refill(self):
  _,_,cells,jobs=fixture(("measured",),2,4)
  with tempfile.TemporaryDirectory() as d:
   t=Transport([ok() for _ in jobs],[.08,.01,.01,.01]); asyncio.run(make_runner(d,jobs,cells,t).run()); self.assertEqual(t.max_active,2); self.assertLess(t.started[2],t.finished_by_index[0])
 def test_admission_spacing_and_peak_are_recorded(self):
  _,_,cells,jobs=fixture(("measured",),2,2); old=dict(MANIFEST); MANIFEST.update({"admission_spacing_seconds":{"luna":.02}})
  try:
   with tempfile.TemporaryDirectory() as d:
    t=Transport([ok(),ok()],[.01,.01]); r=make_runner(d,jobs,cells,t); asyncio.run(r.run()); self.assertGreaterEqual(t.started[1]-t.started[0],.018); rec=json.loads((Path(d)/"run/cells/w1_luna_c2.json").read_text()); self.assertLessEqual(rec["peak_http_inflight_measured"],2)
  finally: MANIFEST.clear(); MANIFEST.update(old)
 def test_freeze_job_tamper_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   dest=Path(d)/"freeze"; build_freeze(dest); p=dest/"jobs.jsonl"; p.write_text(p.read_text().replace('"phase": "warmup"','"phase": "measured"',1))
   with self.assertRaises(ValueError): load_efficiency_freeze(dest)
 def test_html_429_stops_queue(self):
  _,_,cells,jobs=fixture(("measured",),1,4)
  with tempfile.TemporaryDirectory() as d:
   t=Transport([Response(429,text="<html>429</html>")]+[ok() for _ in jobs[1:]]); out=asyncio.run(make_runner(d,jobs,cells,t).run()); self.assertEqual(t.calls,1); self.assertTrue(out["halt_reason"])
 def test_interrupted_resume_invalidates_cell(self):
  _,_,cells,jobs=fixture(("measured",),1,2)
  with tempfile.TemporaryDirectory() as d:
   r=make_runner(d,jobs,cells,Transport([ok()])); asyncio.run(r._one(jobs[0])); r.ledger.reserve(jobs[1]["key"],jobs[1]["reserved_usd"],{**jobs[1],"freeze_digest":"freeze-test","model_requested":MODEL["model_requested"],"endpoint":MODEL["endpoint"],"software_hashes":{}}); r2=make_runner(d,jobs,cells,Transport([])); asyncio.run(r2.initialize()); self.assertIn("w1_luna_c2",r2.invalid_cells)
 def test_evidence_tamper_rejected(self):
  _,_,cells,jobs=fixture()
  with tempfile.TemporaryDirectory() as d:
   r=make_runner(d,jobs,cells,Transport([ok()])); asyncio.run(r.run()); p=Path(d)/"run/responses"/(jobs[0]["key"]+".json"); p.write_text("tampered");
   with self.assertRaises(ValueError): asyncio.run(make_runner(d,jobs,cells,Transport([])).initialize())
 def test_completed_cell_record_is_immutable_on_resume(self):
  _,_,cells,jobs=fixture()
  with tempfile.TemporaryDirectory() as d:
   r=make_runner(d,jobs,cells,Transport([ok()])); asyncio.run(r.run()); p=Path(d)/"run/cells/w1_luna_c2.json"; before=p.read_bytes(); asyncio.run(make_runner(d,jobs,cells,Transport([])).run()); self.assertEqual(before,p.read_bytes())
 def test_known_http_cost_and_overflow_fatal(self):
  _,_,cells,jobs=fixture()
  with tempfile.TemporaryDirectory() as d:
   r=make_runner(d,jobs,cells,Transport([Response(500,{"usage":{"cost":"0.0001"}})])); asyncio.run(r.run()); self.assertEqual(r.results[jobs[0]["key"]]["cost_usd"],"0.0001")
  with tempfile.TemporaryDirectory() as d: self.assertTrue(asyncio.run(make_runner(d,jobs,cells,Transport([ok("99")])).run())["halt_reason"])
 def test_budget_zero_and_timeout_duration(self):
  _,_,cells,jobs=fixture()
  with tempfile.TemporaryDirectory() as d: self.assertEqual(asyncio.run(make_runner(d,jobs,cells,Transport([ok()]),"0.000001").run())["completed"],0)
  with tempfile.TemporaryDirectory() as d:
   r=make_runner(d,jobs,cells,Transport([httpx.ReadTimeout("timeout")])); out=asyncio.run(r.run()); self.assertEqual(out["status_counts"].get("timeout"),1); self.assertGreater(out["completed"],0); self.assertGreater(r.results[jobs[0]["key"]].get("transport_ms",0),0)
if __name__=="__main__": unittest.main()
