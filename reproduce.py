"""Offline reconstruction from the public response bundle; no model or network calls."""
import argparse,hashlib,json,os,shutil,sqlite3,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def verify_data(data):
    manifest=json.loads((data/'SHA256SUMS.json').read_text())
    for name,h in manifest.items():
        p=(data/name).resolve()
        if not p.is_relative_to(data.resolve()) or not p.is_file() or sha(p)!=h:raise ValueError('data checksum mismatch: '+name)
    return len(manifest)
def compare(a,b,where='root'):
    if type(a)!=type(b) and not isinstance(a,(float,int)):raise AssertionError(where)
    if isinstance(a,dict):
        assert a.keys()==b.keys(),(where,'keys')
        for k in a:compare(a[k],b[k],where+'.'+k)
    elif isinstance(a,list):
        assert len(a)==len(b),(where,'length')
        for i,(x,y) in enumerate(zip(a,b)):compare(x,y,where+f'[{i}]')
    elif isinstance(a,float):assert abs(a-b)<=1e-12,(where,a,b)
    else:assert a==b,(where,a,b)
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--skip-figures',action='store_true',help='Recompute all numeric analyses but omit figure rendering')
    a=p.parse_args();data=a.data_dir.resolve();out=a.output_dir.resolve()
    if out.exists() and any(out.iterdir()):p.error('output directory must be empty; existing results are never overwritten')
    verified=verify_data(data);out.mkdir(parents=True,exist_ok=True)
    for name in ['analysis','postanalysis','runtime','supplements','scripts','freezes','configs','reports','tests']:
        shutil.copytree(ROOT/name,out/name,ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copyfile(ROOT/'研究方案.md',out/'研究方案.md')
    for name in ['runs','data']:shutil.copytree(data/name,out/name)
    # JSONL is the public interchange format. This disposable SQL index only adapts legacy readers.
    con=sqlite3.connect(out/'runs/project_budget.sqlite')
    con.execute('create table reservations (key text primary key, amount_usd text, metadata_json text, status text, result_json text)')
    con.execute('create table ledger_meta (id integer primary key, project_id text, cap_usd text, prior_reservation_usd text, prior_evidence_sha256 text)')
    m=json.loads((data/'index_metadata.json').read_text());con.execute('insert into ledger_meta values (1,?,?,?,?)',tuple(m[k] for k in ['project_id','cap_usd','prior_reservation_usd','prior_evidence_sha256']))
    count=0
    for line in (data/'records.jsonl').open():
        r=json.loads(line);con.execute('insert into reservations values (?,?,?,?,?)',(r['key'],r['amount_usd'],json.dumps(r['metadata']),r['status'],json.dumps(r['result'])));count+=1
    con.commit();con.close();assert count==26172
    # Explicit network denial inside every analysis subprocess, in addition to having no credentials.
    guard=out/'offline_guard';guard.mkdir()
    (guard/'sitecustomize.py').write_text('import socket\ndef denied(*a,**k): raise RuntimeError("network disabled during offline reproduction")\nsocket.socket.connect=denied\nsocket.socket.connect_ex=denied\nsocket.create_connection=denied\n')
    env={k:v for k,v in os.environ.items() if not any(t in k.upper() for t in ['API_KEY','TOKEN','SECRET','PASSWORD'])}
    env['PYTHONPATH']=str(guard);env['MPLCONFIGDIR']=str(out/'.mpl-cache')
    scripts=['summarize_three_rounds','analyze_efficiency','analyze_format_explanation','analyze_natural_coding','analyze_chatgpt_health_historical']
    if not a.skip_figures:scripts+=['make_manuscript_figures','make_manuscript_figures_brief']
    logs=out/'logs';logs.mkdir()
    for name in scripts:
        print('Reproducing '+name,flush=True)
        with (logs/(name+'.log')).open('w') as f:
            cmd=[sys.executable,'scripts/'+name+'.py']
            subprocess.run(cmd,cwd=out,env=env,stdout=f,stderr=subprocess.STDOUT,check=True)
    for stage in ['three_round_v1','efficiency_v1','format_explanation_v1','natural_coding_v1','chatgpt_health_historical_v1']:
        actual=json.loads((out/'reports'/stage/'results.json').read_text());expected=json.loads((ROOT/'expected'/stage/'results.json').read_text())
        if stage=='three_round_v1':actual.pop('audit');expected.pop('audit')
        if stage in ['format_explanation_v1','natural_coding_v1']:actual.pop('audit');expected.pop('audit')
        if stage=='efficiency_v1':
            actual.pop('freeze_digest');expected.pop('freeze_digest')
        actual.pop('provenance',None);expected.pop('provenance',None)
        compare(actual,expected,stage)
    figures_checked=[]
    if not a.skip_figures:
        for p in (ROOT/'expected/figures').rglob('*.csv'):
            q=out/'reports/manuscript_figures_brief_v2'/p.relative_to(ROOT/'expected/figures')
            assert p.read_bytes()==q.read_bytes(),str(q)
            figures_checked.append(str(p.relative_to(ROOT/'expected/figures')))
    report={'passed':True,'requests':count,'verified_evidence_files':verified,'numeric_analyses_match':5,'figure_csv_match':figures_checked,'network_disabled':True,'original_ledger_required':False,'temporary_index':'runs/project_budget.sqlite; rebuilt from records.jsonl'}
    (out/'REPRODUCTION_CHECKS.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
