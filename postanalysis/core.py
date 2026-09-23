"""Deterministic three-round summaries, without network or ledger mutations."""
from collections import Counter, defaultdict
from decimal import Decimal
from itertools import combinations, product
import hashlib
import json
import random
import sqlite3
from pathlib import Path

from analysis.scoring import score_prediction, summarize_predictions
from analysis.paired import paired_sign_flip_pvalue, holm_adjust
from runtime.formal_plan import load_freeze
from scripts.summarize_formal_first_round import _read_sqlite, summarize

ROLES = ('jev', 'luna', 'gemini', 'deepseek')
SEED = 20260922
ITERATIONS = 5000
LAYERS = ('main_clear', 'main_full', 'emergency', 'sensitivity_main_clear', 'sensitivity_main_full')

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def select(c, layer):
    if layer.startswith('sensitivity_'):
        return not c['ai_source_review_flag'] and select(c, layer[len('sensitivity_'):])
    return c['dataset_id'] == ('nm_emergency' if layer == 'emergency' else 'nm_main') and (layer != 'main_clear' or c['label_type'] == 'clear')

def prediction(result):
    p = (result.get('parsed') or {}).get('triage')
    return p if result.get('status') == 'success' and p in ('A','B','C','D') else None

def scored(c, result):
    return score_prediction(prediction(result), c['acceptable_labels'])

def mean(values):
    values = list(values)
    return sum(values)/len(values) if values else None

def bootstrap(values):
    """One numeric mean per independent scenario; percentile cluster CI."""
    values = list(values)
    if not values:
        return {'estimate': None, 'ci95': None, 'scenarios': 0}
    rng = random.Random(SEED)
    n = len(values)
    samples = sorted(sum(values[rng.randrange(n)] for _ in range(n))/n for _ in range(ITERATIONS))
    def q(p):
        at = (ITERATIONS-1)*p
        lo = int(at)
        return samples[lo] + (samples[min(lo+1,ITERATIONS-1)]-samples[lo])*(at-lo)
    return {'estimate':mean(values), 'ci95':[q(.025), q(.975)], 'scenarios':n,
            'seed':SEED, 'replicates':ITERATIONS}

def load_all(root):
    root = Path(root)
    budget = json.loads((root/'configs/project_budget.json').read_text())
    if budget['live_enabled']:
        raise ValueError('offline_analysis_requires_live_closed')
    con = sqlite3.connect(f'file:{root}/runs/project_budget.sqlite?mode=ro', uri=True)
    if con.execute('pragma quick_check').fetchone()[0] != 'ok':
        raise ValueError('ledger_integrity')
    if con.execute("select count(*) from reservations where status != 'terminal'").fetchone()[0]:
        raise ValueError('pending_jobs')
    results, round_reports, manifests, costs = {}, {}, {}, {}
    evidence_digest = hashlib.sha256()
    hashes, reference_cases, reference_models, reference_requests = {}, None, None, None
    for number, name in enumerate(('first_round_v1','second_round_v1','third_round_v1'),1):
        path = root/'freezes'/name
        manifest, models, cases, jobs = load_freeze(path)
        expected = set(product(ROLES, cases))
        if len(jobs) != 4352 or {(j['role'],j['variant_id']) for j in jobs} != expected or any(j['repeat_id'] != number for j in jobs):
            raise ValueError('round_cartesian_product_mismatch')
        requests = {(j['role'],j['variant_id']):(j['request_hash'],j['reserved_usd']) for j in jobs}
        if number == 1:
            reference_cases, reference_models, reference_requests = cases, models, requests
        elif (cases != reference_cases or models != reference_models or requests != reference_requests):
            raise ValueError('cross_round_inputs_or_parameters_changed')
        for key in ('max_output_tokens','total_timeout_seconds','socket_timeout_seconds','connect_timeout_seconds','per_model_concurrency','max_attempts_per_job'):
            if number > 1 and manifest[key] != manifests['1'][key]:
                raise ValueError('cross_round_execution_parameters_changed')
        # Current first-round plan/runner differ after the documented repeat support.
        # Scoring and payload logic must still match every original freeze.
        for name_, expected_hash in manifest['software_hashes'].items():
            if name_ not in ('runtime/formal_plan.py','runtime/formal_runner.py') or number > 1:
                if sha(root/name_) != expected_hash:
                    raise ValueError('frozen_software_drift:'+name_)
        original_runtime = {k:v for k,v in manifest['software_hashes'].items() if k.startswith('runtime/')}
        verified = _read_sqlite(root/'runs/project_budget.sqlite',jobs,manifest['freeze_digest'],models,root)
        for j in sorted(jobs,key=lambda j:j['key']):
            metadata = json.loads(con.execute('select metadata_json from reservations where key=?',(j['key'],)).fetchone()[0])
            if metadata.get('software_hashes') != original_runtime:
                raise ValueError('recorded_runtime_hash_mismatch')
            results[(number,j['role'],j['variant_id'])] = verified[j['key']]
            evidence_digest.update(json.dumps({'job':j,'metadata':metadata,'result':verified[j['key']]},sort_keys=True,separators=(',',':'),ensure_ascii=False).encode())
            evidence_digest.update(b'\n')
        report = summarize(cases,jobs,verified)
        if number == 1 and json.loads(json.dumps(report)) != json.loads((root/'reports/first_round_results.json').read_text()):
            raise ValueError('first_round_reproduction_failed')
        round_reports[str(number)] = report
        manifests[str(number)] = manifest
        costs[str(number)] = {
            'reported_plus_estimated_usd': str(sum((Decimal(v['cost_usd']) for v in verified.values() if v.get('cost_usd') is not None),Decimal(0))),
            'unknown_cost_requests':sum(v.get('cost_usd') is None for v in verified.values()),
            'status_counts':dict(Counter(v['status'] for v in verified.values()))}
        costs[str(number)]['by_model'] = {role:{
            'known_cost_usd':str(sum((Decimal(v['cost_usd']) for j in jobs if j['role']==role for v in [verified[j['key']]] if v.get('cost_usd') is not None),Decimal(0))),
            'cost_status_counts':dict(Counter(verified[j['key']].get('cost_status','unknown') for j in jobs if j['role']==role)),
            'latency_ms_descriptive':_latencies([verified[j['key']].get('latency_ms') for j in jobs if j['role']==role])
        } for role in ROLES}
        hashes.update({str(p.relative_to(root)):sha(p) for p in sorted(path.iterdir()) if p.is_file()})
    counts = dict(Counter(r['status'] for r in results.values()))
    if len(results) != 13056 or counts != {'success':13041,'parse_error':14,'request_error':1}:
        raise ValueError('acceptance_counts_mismatch')
    prior = Decimal(con.execute('select prior_reservation_usd from ledger_meta').fetchone()[0])
    reserved = prior + sum((Decimal(x[0]) for x in con.execute('select amount_usd from reservations')),Decimal(0))
    con.close()
    costs['total'] = {'reported_plus_estimated_including_pilot_usd':str(Decimal(budget['prior_pilot']['cost_reported_plus_upper_estimate_usd']) + sum((Decimal(costs[str(n)]['reported_plus_estimated_usd']) for n in (1,2,3)),Decimal(0))),
                      'unknown_cost_requests':1,'permanent_reservation_usd':str(reserved),'automatic_capacity_remaining_usd':str(Decimal('180')-reserved)}
    return reference_cases, results, round_reports, {'counts':counts,'costs':costs,'source_hashes':hashes,'verified_ledger_records_sha256':evidence_digest.hexdigest(),'first_round_reproduced':True,
        'software_note':'First-round plan/runner hashes differ from current code after repeat support; every ledger runtime hash matches its own frozen manifest.'}

def _latencies(values):
    values=sorted(v for v in values if v is not None)
    def q(p):
        if not values: return None
        x=(len(values)-1)*p; lo=int(x)
        return values[lo]+(values[min(lo+1,len(values)-1)]-values[lo])*(x-lo)
    return {'n':len(values),'p50':q(.5),'p95':q(.95),'controlled_efficiency_benchmark':False}

def clinical_summary(cases, results, rounds, role, layer):
    records, scenario_values, per_input = [], defaultdict(list), defaultdict(list)
    selected = sorted(v for v,c in cases.items() if select(c,layer))
    for v in selected:
        c = cases[v]
        for n in rounds:
            r = results[(n,role,v)]
            records.append({'acceptable_labels':c['acceptable_labels'],'prediction':prediction(r),'terminal_status':r['status']})
            per_input[v].append(scored(c,r))
        for mode in ('valid','full_plan'):
            values = [s['correct'] for s in per_input[v] if mode == 'full_plan' or s['valid_decision']]
            if values:
                scenario_values[(mode,c['scenario_id'])].append(mean(values))
    summary = summarize_predictions(records)
    summary['non_emergency_to_D_valid'] = sum(prediction(results[(n,role,v)]) == 'D' and 'D' not in cases[v]['acceptable_labels'] for n in rounds for v in selected)
    summary['non_emergency_planned'] = sum('D' not in cases[v]['acceptable_labels'] for n in rounds for v in selected)
    summary['non_emergency_valid'] = sum('D' not in cases[v]['acceptable_labels'] and prediction(results[(n,role,v)]) is not None for n in rounds for v in selected)
    for mode in ('valid','full_plan'):
        vals = [mean(values) for (m,s),values in sorted(scenario_values.items()) if m == mode]
        summary['scenario_equal_'+mode] = bootstrap(vals)
    summary['planned_scenarios'] = len({cases[v]['scenario_id'] for v in selected})
    return summary

def paired_values(cases,results,rounds,left,right,layer,common):
    groups = defaultdict(list)
    planned = valid = 0
    for v,c in sorted(cases.items()):
        if not select(c,layer): continue
        vals=[]
        for n in rounds:
            planned+=1
            a,b = (scored(c,results[(n,role,v)]) for role in (left,right))
            both = a['valid_decision'] and b['valid_decision']
            valid += both
            if common and not both: continue
            vals.append(int(a['correct'])-int(b['correct']))
        if vals: groups[c['scenario_id']].append(mean(vals))
    return [mean(x) for _,x in sorted(groups.items())], {'planned_request_pairs':planned,'common_valid_request_pairs':valid,'invalid_request_pairs':planned-valid,'planned_scenarios':len({c['scenario_id'] for c in cases.values() if select(c,layer)})}

def stability(cases, results):
    out=[]
    for role in ROLES:
        for layer in LAYERS:
            ids = sorted(v for v,c in cases.items() if select(c,layer))
            for rounds in ((1,2,3),(1,2),(1,3),(2,3)):
                valid=agree=0
                for v in ids:
                    ps=[prediction(results[(n,role,v)]) for n in rounds]
                    if None not in ps:
                        valid+=1; agree+=len(set(ps)) == 1
                out.append({'role':role,'layer':layer,'rounds':'-'.join(map(str,rounds)),'planned_inputs':len(ids),'valid_inputs':valid,'invalid_inputs':len(ids)-valid,
                    'identical':agree,'changed':valid-agree,'agreement_rate_valid':agree/valid if valid else None,'change_rate_valid':(valid-agree)/valid if valid else None})
    return out

def scenario_rows(cases,results):
    rows=[]
    for n,role,s in product((1,2,3),ROLES,sorted({c['scenario_id'] for c in cases.values()})):
        subset={v:c for v,c in cases.items() if c['scenario_id']==s}
        layer='emergency' if next(iter(subset.values()))['dataset_id']=='nm_emergency' else 'main_full'
        # No per-scenario bootstrap: only descriptive metrics and denominators.
        records=[{'acceptable_labels':c['acceptable_labels'],'prediction':prediction(results[(n,role,v)]),'terminal_status':results[(n,role,v)]['status']} for v,c in subset.items()]
        summary=summarize_predictions(records)
        summary['status_counts']=json.dumps(summary['status_counts'],sort_keys=True)
        rows.append({'round':n,'role':role,'scenario_id':s,'cohort':layer,**summary,
             'non_emergency_to_D_valid':sum(prediction(results[(n,role,v)])=='D' and 'D' not in c['acceptable_labels'] for v,c in subset.items())})
    return rows

def analyze(cases,results,legacy_reports=None):
    metrics={}
    for label,rounds in [('1',[1]),('2',[2]),('3',[3]),('combined',[1,2,3])]:
        metrics[label]={layer:{role:clinical_summary(cases,results,rounds,role,layer) for role in ROLES} for layer in LAYERS}
    comparisons={}
    for label,rounds in [('1',[1]),('2',[2]),('3',[3]),('combined',[1,2,3])]:
        comparisons[label]={}
        for layer in LAYERS:
            comparisons[label][layer]={}
            for right in ROLES[1:]:
                comparisons[label][layer][right]={}
                for mode,common in [('common_valid',True),('full_plan',False)]:
                    vals,den=paired_values(cases,results,rounds,'jev',right,layer,common)
                    comparisons[label][layer][right][mode]={**bootstrap(vals),**den}
                    if legacy_reports is not None and label != 'combined':
                        # Preserve the original deterministic scenario traversal
                        # and numerical operations for every single-round pair CI.
                        original=legacy_reports[label]['layers'][layer]['paired']['jev_vs_'+right][mode]['bootstrap']
                        comparisons[label][layer][right][mode].update({
                            'estimate':original['estimate'],'ci95':original['ci95'],
                            'scenarios':original['n_scenarios'],
                            'replicates':original['iterations'],'seed':original['seed']})
    raw=[]
    for right in ('gemini','deepseek'):
        vals,den=paired_values(cases,results,[1],'jev',right,'main_clear',True)
        raw.append(paired_sign_flip_pvalue(vals))
    secondary=[{'comparison':'jev_vs_'+role,'p_raw':p,'p_holm':adj,'family_size':2,'scenarios':comparisons['1']['main_clear'][role]['common_valid']['scenarios'],
                'method':'two-sided exact scenario sign-flip; equal scenario weights; common valid pairs',
                'assumption':'scenario differences exchangeable in sign under null; post-collection implementation'} for role,p,adj in zip(('gemini','deepseek'),raw,holm_adjust(raw))]
    return {'metrics':metrics,'paired_comparisons':comparisons,'secondary_tests':secondary,'stability':stability(cases,results)}
