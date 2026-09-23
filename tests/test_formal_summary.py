import unittest
import json, sqlite3, tempfile, hashlib
from pathlib import Path
from scripts.summarize_formal_first_round import summarize
from scripts.summarize_formal_first_round import _read_sqlite

class FormalSummaryTests(unittest.TestCase):
    def test_metrics_missing_is_not_non_d(self):
        cases={"v1":{"variant_id":"v1","dataset_id":"nm_main","label_type":"clear","scenario_id":"s1","acceptable_labels":["D"],"ai_source_review_flag":False,"information_level":"objective"},"v2":{"variant_id":"v2","dataset_id":"nm_main","label_type":"clear","scenario_id":"s2","acceptable_labels":["A"],"ai_source_review_flag":True,"information_level":"subjective"}}
        jobs=[]; results={}
        for c in cases.values():
            for role in ("jev","luna","gemini","deepseek"):
                k=role+c["variant_id"]; jobs.append({"key":k,"role":role,"variant_id":c["variant_id"]})
                results[k]={"status":"success","parsed":{"triage":"D" if c["variant_id"]=="v1" else "A"}}
        results["lunav2"]={"status":"timeout","parsed":None}
        out=summarize(cases,jobs,results)
        self.assertEqual(out["layers"]["main_clear"]["models"]["luna"]["valid"],1)
        self.assertEqual(out["layers"]["main_clear"]["models"]["luna"]["explicit_D_to_nonD"],0)
        self.assertEqual(out["layers"]["main_clear"]["paired"]["jev_vs_luna"]["common_valid"]["denominators"]["common_valid_requests"],1)
        self.assertFalse(out["clinical_safety_claim"])

    def test_failure_is_excluded_and_sensitivity_is_explicit(self):
        cases={"v":{"variant_id":"v","dataset_id":"nm_main","label_type":"clear","scenario_id":"s","acceptable_labels":["D"],"ai_source_review_flag":True}}
        jobs=[{"key":r,"role":r,"variant_id":"v"} for r in ("jev","luna","gemini","deepseek")]
        results={j["key"]:{"status":"timeout","parsed":{"triage":"D"}} for j in jobs}
        out=summarize(cases,jobs,results)
        self.assertEqual(out["layers"]["main_clear"]["models"]["jev"]["valid"],0)
        self.assertEqual(out["layers"]["main_clear"]["models"]["jev"]["explicit_D_to_nonD"],0)
        self.assertEqual(out["layers"]["main_clear"]["sensitivity_excluded_rows"],1)

    def test_real_reservations_schema_and_response_hash(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d); response=d/'r.json'
            job={"key":"k","role":"jev","variant_id":"v","condition":"structured","repeat_id":1,"request_hash":hashlib.sha256(json.dumps({},sort_keys=True,separators=(",",":")).encode()).hexdigest(),"reserved_usd":"1"}
            base={"status":"success","role":"jev","variant_id":"v","condition":"structured","repeat_id":1}
            response.write_text(json.dumps({"job":job,"request_payload":{},"result":base}))
            result={**base,"response_file":str(response),"response_file_sha256":hashlib.sha256(response.read_bytes()).hexdigest()}
            con=sqlite3.connect(d/'x.sqlite'); con.execute('create table reservations(key text, amount_usd text, metadata_json text, status text, result_json text)')
            meta={**job,"freeze_digest":"f"}; con.execute('insert into reservations values(?,?,?,?,?)',('k','1',json.dumps(meta), 'terminal',json.dumps(result))); con.commit(); con.close()
            got=_read_sqlite(d/'x.sqlite',[job],'f',{},d)
            self.assertEqual(got['k']['status'],'success')
            con=sqlite3.connect(d/'x.sqlite'); con.execute("update reservations set result_json=?",(json.dumps({**result,"status":"parse_error"}),)); con.commit(); con.close()
            with self.assertRaises(ValueError): _read_sqlite(d/'x.sqlite',[job],'f',{},d)
            response.write_text('tampered')
            with self.assertRaises(ValueError): _read_sqlite(d/'x.sqlite',[job],'f',{},d)

if __name__ == "__main__": unittest.main()
