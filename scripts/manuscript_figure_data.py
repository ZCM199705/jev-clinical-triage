"""Offline, auditable data derivations for manuscript figures."""
import csv
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROLES = ('jev', 'luna', 'gemini', 'deepseek')
LABELS = 'ABCD'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def read_csv(path):
    return list(csv.DictReader(Path(path).open(encoding='utf-8-sig')))


def save_csv(path, rows):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    if not rows: raise ValueError('empty_source_data:'+str(path))
    fields=list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)


def decision(result):
    p=(result.get('parsed') or {}).get('triage')
    return p if result.get('status')=='success' and p in LABELS else None


def classify(prediction, acceptable):
    if prediction is None:return 'technical_failure'
    if prediction in acceptable:return 'correct'
    value=LABELS.index(prediction)
    if value<min(LABELS.index(x) for x in acceptable):return 'undertriage'
    if value>max(LABELS.index(x) for x in acceptable):return 'overtriage'
    raise ValueError('noncontiguous_acceptable_set')


def stability_class(predictions, acceptable):
    if None in predictions:return 'technical_failure'
    correct=[p in acceptable for p in predictions]
    if len(set(predictions))==1:return 'stable_correct' if all(correct) else 'stable_incorrect'
    return 'changed_all_acceptable' if all(correct) else 'changed_any_incorrect'


def selected_risk(rows, threshold, planned):
    accepted=[r for r in rows if r['confidence']>=threshold]
    n=len(accepted); errors=sum(not r['correct'] for r in accepted)
    return {'threshold':threshold,'accepted':n,'errors':errors,'valid_denominator':len(rows),
            'planned_denominator':planned,'coverage_valid':n/len(rows) if rows else None,
            'coverage_planned':n/planned,'risk':errors/n if n else None,
            'accepted_scenarios':len({r['scenario_id'] for r in accepted})}


def risk_curve(rows,planned):
    return [selected_risk(rows,t,planned) for t in sorted({r['confidence'] for r in rows},reverse=True)]


def load_decisions():
    assert not read_json(ROOT/'configs/project_budget.json')['live_enabled']
    con=sqlite3.connect(f'file:{ROOT}/runs/project_budget.sqlite?mode=ro',uri=True)
    db={k:(json.loads(m),s,json.loads(r)) for k,m,s,r in con.execute('select key,metadata_json,status,result_json from reservations')}
    con.close(); results={}; cases=None; hashes={}
    for n,stage in enumerate(['first_round_v1','second_round_v1','third_round_v1'],1):
        folder=ROOT/'freezes'/stage; manifest=read_json(folder/'manifest.json')
        cs={c['variant_id']:c for c in map(json.loads,(folder/'cases.jsonl').read_text().splitlines())}
        if cases is None:cases=cs
        else:assert cases==cs
        for name,expected in manifest['file_hashes'].items():assert sha(folder/name)==expected
        for job in map(json.loads,(folder/'jobs.jsonl').read_text().splitlines()):
            md,status,r=db[job['key']]
            assert status=='terminal' and md['freeze_digest']==manifest['freeze_digest']
            for key in ['role','variant_id','request_hash','repeat_id']:assert md[key]==job[key]
            h=sha(r['response_file']);assert h==r['response_file_sha256']
            hashes[job['key']]=h;results[n,job['role'],job['variant_id']]=r
    assert len(results)==13056
    assert Counter(r['status'] for r in results.values())=={'success':13041,'parse_error':14,'request_error':1}
    return cases,results,hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()


def derive(cases,results):
    counts=[]; stable=[]; probs=[]; stable_inputs=[]
    for role in ROLES:
        for n in (1,2,3):
            for cohort in ['main_clear','main_edge','main_full','emergency']:
                ids=[v for v,c in cases.items() if (c['dataset_id']=='nm_emergency' if cohort=='emergency' else c['dataset_id']=='nm_main') and (cohort not in ['main_clear','main_edge'] or c['label_type']==('clear' if cohort=='main_clear' else 'edge'))]
                cc=Counter(classify(decision(results[n,role,v]),cases[v]['acceptable_labels']) for v in ids)
                counts.append({'role':role,'round':n,'cohort':cohort,'planned':len(ids),
                               'scenarios':len({cases[v]['scenario_id'] for v in ids}),
                               **{k:cc[k] for k in ['correct','undertriage','overtriage','technical_failure']},
                               'agreement_full_plan':cc['correct']/len(ids)})
        ids=[v for v,c in cases.items() if c['dataset_id']=='nm_main'];cc=Counter()
        for v in sorted(ids):
            ps=[decision(results[n,role,v]) for n in (1,2,3)]
            cl=stability_class(ps,cases[v]['acceptable_labels']);cc[cl]+=1
            stable_inputs.append({'role':role,'variant_id':v,'scenario_id':cases[v]['scenario_id'],
                                  'round1':ps[0],'round2':ps[1],'round3':ps[2],'acceptable_labels':'/'.join(cases[v]['acceptable_labels']),'category':cl})
        valid=len(ids)-cc['technical_failure'];identical=cc['stable_correct']+cc['stable_incorrect']
        stable.append({'role':role,'planned':len(ids),'valid':valid,'identical':identical,'consistency_valid':identical/valid,
                       **{k:cc[k] for k in ['stable_correct','stable_incorrect','changed_all_acceptable','changed_any_incorrect','technical_failure']}})
    for v,c in sorted(cases.items()):
        if c['dataset_id']!='nm_main' or c['label_type']!='clear':continue
        r=results[1,'jev',v];pred=decision(r)
        if pred is None:continue
        p=r['parsed']['probabilities'];assert set(p)==set(LABELS) and abs(sum(p.values())-1)<=.002
        assert p[pred]+1e-9>=max(p.values())
        probs.append({'variant_id':v,'scenario_id':c['scenario_id'],'confidence':p[pred],
                      'prediction':pred,'reference':c['acceptable_labels'][0], 'correct':pred in c['acceptable_labels'],**{'p_'+k:p[k] for k in LABELS}})
    assert len(probs)==476
    assert [stable[0][k] for k in ['stable_correct','stable_incorrect','changed_all_acceptable','changed_any_incorrect','technical_failure']]==[741,145,33,27,14]
    p=selected_risk(probs,.9,480);assert (p['accepted'],p['errors'])==(152,8)
    return counts,stable,probs,stable_inputs
