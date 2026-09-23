"""Post-collection offline efficiency analysis; never performs API calls."""
import csv
import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.freeze_efficiency import load_efficiency_freeze
from runtime.formal_plan import digest


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def costs(results):
    known = sum((Decimal(r['cost_usd']) for r in results if r.get('cost_usd') is not None), Decimal(0))
    return {'known_usd': str(known), 'unknown_requests': sum(r.get('cost_usd') is None for r in results),
            'cost_status_counts': dict(Counter(r.get('cost_status', 'missing') for r in results))}


def metrics(results, walltime_ms):
    valid = [r for r in results if r['status'] == 'success']
    lat = [r['transport_ms'] for r in valid]
    out = {'planned': len(results), 'valid_outputs': len(valid), 'failed': len(results)-len(valid),
           'effective_outputs_per_second': len(valid)/(walltime_ms/1000),
           'measured_walltime_s': walltime_ms/1000, **costs(results)}
    for name, values in [('valid_http', lat), ('all_attempt_http', [r['transport_ms'] for r in results]),
                         ('admission_wait', [r.get('admission_wait_ms', 0) for r in results])]:
        for q in [50,95]:
            out[f'{name}_p{q}_ms'] = float(np.percentile(values,q)) if values else None
    out['known_usd_per_1000_planned'] = float(Decimal(out['known_usd'])*1000/len(results))
    return out


def write_csv(path, rows):
    with path.open('w', newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)


def main():
    out=ROOT/'reports/efficiency_v1'; out.mkdir(exist_ok=True)
    m,models,cases,cells,jobs=load_efficiency_freeze(ROOT/'freezes/efficiency_v1')
    con=sqlite3.connect(f'file:{ROOT}/runs/project_budget.sqlite?mode=ro',uri=True)
    assert con.execute('pragma quick_check').fetchone()[0]=='ok'
    rows={k:(a,json.loads(md),s,json.loads(r)) for k,a,md,s,r in con.execute('select key,amount_usd,metadata_json,status,result_json from reservations')}
    prior=Decimal(con.execute('select prior_reservation_usd from ledger_meta').fetchone()[0])
    grouped=defaultdict(list); hashes={}; request_rows=[]
    for j in jobs:
        a,md,s,r=rows[j['key']]
        assert s=='terminal' and a==j['reserved_usd']
        expected={**j,'freeze_digest':m['freeze_digest'],'model_requested':models[j['role']]['model_requested'],'endpoint':models[j['role']]['endpoint'],'software_hashes':m['software_hashes']}
        assert all(md.get(k)==v for k,v in expected.items())
        p=Path(r['response_file']); h=sha(p); assert h==r['response_file_sha256']
        ev=json.loads(p.read_text())
        assert ev['job']==j and digest(ev['request_payload'])==j['request_hash']
        assert ev['result']=={k:v for k,v in r.items() if k not in {'response_file','response_file_sha256','persistence_ms'}}
        hashes[j['key']]=h; grouped[j['cell_id']].append((j,r))
        usage=r.get('usage') or {}
        request_rows.append({**{k:j[k] for k in ['key','cell_id','role','phase','variant_id','position']},
                             **{k:r.get(k) for k in ['status','transport_ms','admission_wait_ms','cost_usd','cost_status','request_started_at','finished_at','model_returned','provider_returned']},
                             'usage_json':json.dumps(usage,sort_keys=True)})
    cell_rows=[]; excluded=[]; cell_hashes={}
    for c in cells:
        p=ROOT/'runs/efficiency_v1/cells'/f"{c['cell_id']}.json"; rec=json.loads(p.read_text()); cell_hashes[c['cell_id']]=sha(p)
        jr=grouped[c['cell_id']]
        assert rec['cell']==c and rec['freeze_digest']==m['freeze_digest']
        assert rec['response_hashes']=={j['key']:hashes[j['key']] for j,r in jr}
        assert rec['completed']==len(jr)==rec['planned']==205
        if not rec['complete'] or not rec['valid_timing'] or rec['halt_reason']:
            excluded.append(c['cell_id']); continue
        measured=[r for j,r in jr if j['phase']=='measured']
        assert len(measured)==200 and rec['measured_valid']==sum(r['status']=='success' for r in measured)
        cell_rows.append({**c,**metrics(measured,rec['measured_walltime_ms']),
                          'peak_http_inflight':rec['peak_http_inflight_measured'],
                          'started_at':rec['measured_started_at'],'ended_at':rec['ended_at']})
    summaries=[]
    for role in models:
        for level in [1,4,8,16,32]:
            selected=[r for r in cell_rows if r['role']==role and r['concurrency']==level]
            row={'role':role,'concurrency':level,'valid_cells':len(selected),'planned':sum(r['planned'] for r in selected),'valid_outputs':sum(r['valid_outputs'] for r in selected)}
            for field in ['valid_http_p50_ms','valid_http_p95_ms','effective_outputs_per_second','known_usd_per_1000_planned','admission_wait_p50_ms','peak_http_inflight']:
                vals=[r[field] for r in selected if r[field] is not None]
                for suffix,fn in [('median',np.median),('min',np.min),('max',np.max)]:
                    row[field+'_'+suffix]=float(fn(vals)) if vals else None
            row['unknown_requests']=sum(r['unknown_requests'] for r in selected)
            summaries.append(row)
    all_results=[rows[j['key']][3] for j in jobs]
    project_reserved=prior+sum((Decimal(a) for a,md,s,r in rows.values()),Decimal(0))
    assert project_reserved<=180 and all(s=='terminal' for a,md,s,r in rows.values())
    assert not json.loads((ROOT/'configs/project_budget.json').read_text())['live_enabled']
    result={'analysis_version':1,'analysis_timing':'post_collection','freeze_digest':m['freeze_digest'],
            'status_counts':dict(Counter(r['status'] for r in all_results)),'valid_cells':len(cell_rows),'excluded_cells':excluded,
            'phase_counts':dict(Counter(j['phase'] for j in jobs)), 'efficiency_costs':costs(all_results),
            'measured_costs':costs([rows[j['key']][3] for j in jobs if j['phase']=='measured']),
            'warmup_costs':costs([rows[j['key']][3] for j in jobs if j['phase']=='warmup']),
            'project_ledger_costs_excluding_pilot':costs([r for a,md,s,r in rows.values()]),
            'project_permanent_reserved_usd':str(project_reserved),'summary':summaries}
    (out/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    write_csv(out/'cells.csv',cell_rows); write_csv(out/'summary.csv',summaries); write_csv(out/'requests.csv',request_rows)
    colors=['#0072B2','#E69F00','#009E73','#CC79A7']
    fig,axes=plt.subplots(1,3,figsize=(13,4))
    for ax,field,label in zip(axes,['valid_http_p50_ms','valid_http_p95_ms','effective_outputs_per_second'],['Valid HTTP latency p50 (ms)','Valid HTTP latency p95 (ms)','Valid outputs / wall-clock second']):
        for role,color in zip(models,colors):
            ss=[r for r in summaries if r['role']==role]; x=[r['concurrency'] for r in ss]
            ax.plot(x,[r[field+'_median'] for r in ss],'-o',label=role,color=color)
            ax.fill_between(x,[r[field+'_min'] for r in ss],[r[field+'_max'] for r in ss],alpha=.12,color=color)
        ax.set(xlabel='Concurrency ceiling',ylabel=label,xticks=[1,4,8,16,32]); ax.grid(alpha=.2)
        if field!='effective_outputs_per_second': ax.set_yscale('log')
    axes[0].legend(); fig.suptitle('Three time blocks: median and observed range (not confidence intervals)'); fig.tight_layout()
    fig.savefig(out/'efficiency.png',dpi=180); fig.savefig(out/'efficiency.svg'); plt.close(fig)
    lines=['# 受控效率实验离线分析','', '本分析在采集完成后实现；不新增 API 请求。指标沿用冻结方案，汇总规则为每个模型/并发档的三个时间区块单元指标中位数及最小—最大范围。范围不是置信区间。不对请求级数据作独立样本显著性检验。','',
           f"共 {len(jobs)} 次请求（12000 正式、300 预热），{len(cell_rows)}/60 个完整有效单元；排除单元：{excluded}。状态：{result['status_counts']}。所有请求证据、冻结文件和单元响应哈希通过核验。",'',
           '有效输出指满足解析契约的输出，不等于临床正确。HTTP 延迟从实际请求发送起至响应或失败，不含准入等待；同时在 CSV 保留所有尝试延迟和准入等待。有效吞吐=正式有效输出数/正式单元墙钟秒数，包含预留、准入及持久化等开销。预热不纳入这些计时指标。','',
           '|模型|并发上限|有效/计划|HTTP p50 ms，中位数（范围）|HTTP p95 ms，中位数|有效输出/秒，中位数（范围）|已知美元/千次，中位数|', '|---|---:|---:|---|---:|---|---:|']
    for r in summaries:
        def fmt(f):return f"{r[f+'_median']:.2f} ({r[f+'_min']:.2f}–{r[f+'_max']:.2f})"
        lines.append(f"|{r['role']}|{r['concurrency']}|{r['valid_outputs']}/{r['planned']}|{fmt('valid_http_p50_ms')}|{r['valid_http_p95_ms_median']:.2f}|{fmt('effective_outputs_per_second')}|{r['known_usd_per_1000_planned_median']:.6f}|")
    lines += ['', '## 费用与解释边界','',f"本实验已知报告/估算费用 {result['efficiency_costs']['known_usd']} 美元，未知费用 {result['efficiency_costs']['unknown_requests']} 次；费用来源计数 {result['efficiency_costs']['cost_status_counts']}。预热已知费用 {result['warmup_costs']['known_usd']} 美元。未知费用不计为零，美元/千次为已知金额部分，非完整账单。项目账本未知费用共 {result['project_ledger_costs_excluding_pilot']['unknown_requests']} 次；永久预留 {project_reserved} 美元属于预算上界而非实际费用。",'',
              'Jev/Luna/Gemini 经 OpenRouter，DeepSeek 官方直连；当前结果描述指定服务路径。Jev 有 0.06 秒请求准入间隔，理论准入约 16.67 次/秒，实际在途峰值详见 cells.csv。并发上限不等于实际并发，也不代表已达到服务最大吞吐。', '',
              '三个时间区块提供有限的运行重复，不能排除时段、网络、供应商缓存和路由变化。usage 原字段、返回身份、时间戳已导出到 requests.csv，缓存统计不可跨供应商直接等同。未测量首 token 延迟；HTTP 时间也不含完整用户排队等待。', '',
              '效率请求复用原有 34 情境的变体，不提供新的独立临床验证。速度和输出格式成功率不能证明分诊安全性；须结合第一轮主分析及安全错误结果。', '', '产物：results.json、summary.csv、cells.csv、requests.csv、efficiency.png/svg；provenance.json 记录分析代码和证据集合摘要。']
    lines += ['', '## 结果解读', '',
              '在本次各并发档的三个区块中位数上，JEV 有效回答 HTTP p50 最短（约 0.39–0.43 秒）。并发上限 1 时，JEV、Gemini、DeepSeek、Luna 分别约 0.404、0.732、1.024、1.244 秒。此处是服务请求延迟，不是临床处理总时间。', '',
              '高并发吞吐优势并不属于同一个模型：并发上限 32 时，Gemini、DeepSeek、Luna、JEV 分别约 29.38、23.59、18.91、15.80 次有效输出/秒。JEV 在并发 16–32 出现约 15.8 次/秒平台，与约 16.67 次/秒的冻结准入限制接近。不能据此认定裸模型的最大吞吐更低。', '',
              '正式请求中 JEV 2988/3000 有效，Luna 与 Gemini 各 3000/3000，DeepSeek 2998/3000。JEV 延迟优势同时伴随较多技术/解析失败，应共同报告。有效回答不等于正确回答。', '',
              'JEV 已知费用约 0.0287 美元/千次，Luna 约 0.0857、Gemini 约 0.106、DeepSeek 约 0.119–0.121。DeepSeek 使用峰时未缓存上界估算，其他模型主要为供应商报告费用，因此不可将这些数字作为完全同口径的实际账单成本比。', '',
              '论文可表述：在冻结的结构化分诊任务及指定服务路径下，JEV 具有较低典型响应延迟和较低已知报告费用；吞吐结果取决于并发档及准入策略。该证据补充准确性、安全性与稳定性分析，不能单独支持全面优胜或临床部署结论。']
    (out/'结果.md').write_text('\n'.join(lines)+'\n')
    provenance={'analysis_script_sha256':sha(__file__),'freeze_manifest_sha256':sha(ROOT/'freezes/efficiency_v1/manifest.json'),
                'response_hash_collection_digest':digest(hashes),'cell_hash_collection_digest':digest(cell_hashes),
                'outputs':{p.name:sha(p) for p in sorted(out.iterdir()) if p.is_file() and p.name!='provenance.json'}}
    (out/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='summary'},ensure_ascii=False))

if __name__=='__main__':main()
