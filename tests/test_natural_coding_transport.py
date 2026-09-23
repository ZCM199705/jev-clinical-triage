import asyncio, hashlib, json, tempfile, unittest
from decimal import Decimal
from pathlib import Path
import httpx

from runtime.project_ledger import ProjectLedger
from supplements.coding import payload, reservation
from scripts.run_natural_coding import Runner


class NaturalCodingTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.model = json.loads(Path('freezes/format_control_v1/models.json').read_text())[0]
        self.answer = 'Please see your doctor within 48 hours for evaluation.'
        self.source = {'source_key':'source-1','source_role':'gemini','variant_id':'E1_variant01','answer_text':self.answer,
                       'answer_text_sha256':hashlib.sha256(self.answer.encode()).hexdigest()}
        self.body = payload(self.model, self.answer); self.amount = reservation(self.model, self.body)
        base={'source_key':'source-1','source_role':'gemini','coder_role':'luna','variant_id':'E1_variant01','repeat_id':1,
              'answer_text_sha256':self.source['answer_text_sha256'],'request_hash':hashlib.sha256(json.dumps(self.body,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
        self.job={**base,'key':hashlib.sha256(json.dumps(base,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'reserved_usd':str(self.amount)}
        self.manifest={'freeze_digest':'fixture','max_output_tokens':4096,'total_timeout_seconds':.1,'software_hashes':{}}
    def tearDown(self): self.tmp.cleanup()
    def runner(self, client, cap='1', prior='0', directory=None):
        ledger=ProjectLedger(self.root/'ledger.sqlite','test',cap,prior,'prior')
        return Runner(self.manifest,{'luna':self.model,'gemini':json.loads(Path('freezes/format_control_v1/models.json').read_text())[1]}, {'source-1':self.source}, [self.job], ledger, {'luna':'sk-test'}, client, directory or self.root/'run')
    def response(self, **kw):
        d={'id':'x','model':'openai/gpt-5.6-luna','provider':'OpenAI','usage':{'cost':0.000001},'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'status':'mapped','triage':'C','evidence_quote':'within 48 hours'})}}]}; d.update(kw); return d
    async def run_data(self, data):
        calls=[]
        async def handler(req): calls.append(1); return httpx.Response(data[0],text=data[1],headers={'content-type':'application/json'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            r=self.runner(client); out=await r.run()
        return r,out,calls
    async def test_identity_failure_is_fatal_and_no_next_dispatch(self):
        r,out,calls=await self.run_data((200,json.dumps(self.response(provider='Wrong'))))
        self.assertEqual(out[self.job['key']]['status'],'identity_error'); self.assertTrue(r.halt_reason); self.assertEqual(len(calls),1)
    async def test_html_429_is_fatal(self):
        r,out,calls=await self.run_data((429,'rate limited'))
        self.assertEqual(out[self.job['key']]['status'],'configuration_error'); self.assertEqual(r.halt_reason,'http_429'); self.assertEqual(len(calls),1)
    async def test_cost_overflow_is_fatal(self):
        r,out,calls=await self.run_data((200,json.dumps(self.response(usage={'cost':1}))))
        self.assertEqual(out[self.job['key']]['status'],'cost_error'); self.assertEqual(len(calls),1)
    async def test_request_hash_mismatch_reserves_nothing(self):
        self.job['request_hash']='bad'
        async def handler(req): self.fail('must not dispatch')
        ledger=ProjectLedger(self.root/'bad.sqlite','test','1','0','prior')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(ValueError): await Runner(self.manifest,{'luna':self.model},{'source-1':self.source},[self.job],ledger,{'luna':'sk'},client,self.root/'bad').run()
        self.assertEqual(ledger.snapshot()['attempts'],0)
    async def test_resume_evidence_tamper_is_rejected(self):
        async def handler(req): return httpx.Response(200,json=self.response())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            r=self.runner(client); await r.run()
        p=next((self.root/'run/responses').glob('*.json')); p.write_text(p.read_text().replace('within 48 hours','tampered'))
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(500))) as client:
            with self.assertRaises(ValueError): await self.runner(client).run()
    async def test_interrupted_reserved_attempt_is_never_resent(self):
        ledger=ProjectLedger(self.root/'int.sqlite','test','1','0','prior'); meta={**self.job,'freeze_digest':'fixture','model_requested':self.model['model_requested'],'endpoint':self.model['endpoint'],'software_hashes':{}}
        ledger.reserve(self.job['key'],self.amount,meta)
        async def handler(req): self.fail('interrupted attempt must not resend')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            out=await Runner(self.manifest,{'luna':self.model},{'source-1':self.source},[self.job],ledger,{'luna':'sk'},client,self.root/'int').run()
        self.assertEqual(out[self.job['key']]['status'],'interrupted_unknown')
    async def test_budget_exhaustion_dispatches_zero(self):
        calls=[]
        async def handler(req): calls.append(1); return httpx.Response(200,json=self.response())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            r=self.runner(client,cap='0.000001',prior='0'); out=await r.run()
        self.assertEqual(calls,[]); self.assertEqual(r.new,0)

