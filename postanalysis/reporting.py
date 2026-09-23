"""Readable reports and static export figures for the offline analysis."""
import csv
import json
from pathlib import Path
from .core import ROLES, LAYERS

def csv_write(path, rows):
    if not rows:
        path.write_text('status\nno_rows\n'); return
    fields=list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k:json.dumps(v,ensure_ascii=False,sort_keys=True) if isinstance(v,(dict,list)) else v for k,v in row.items()})

def pct(v): return 'NA' if v is None else f'{100*v:.2f}%'
def interval(b):
    return 'NA' if b['ci95'] is None else f"{pct(b['estimate'])} [{pct(b['ci95'][0])}, {pct(b['ci95'][1])}]"

def difference_interval(b):
    return 'NA' if b['ci95'] is None else f"{100*b['estimate']:.2f} 个百分点 [{100*b['ci95'][0]:.2f}, {100*b['ci95'][1]:.2f}]"

def markdown(report):
    labels={'main_clear':'明确标签主数据','main_full':'全部主数据','emergency':'急症补充',
            'sensitivity_main_clear':'疑点排除：明确标签','sensitivity_main_full':'疑点排除：全部主数据'}
    lines=['# 三轮结构化分诊：离线汇总与分析','',
        '第一轮为预设主分析；第二、第三轮及三轮综合为稳定性和补充分析。本分析实现于采集完成后，未新增 API 调用。源标签一致性不等于独立临床验证。','',
        '## 证据与分母','',
        '三轮共 13,056 个终态：13,041 次成功、14 次解析失败、1 次请求失败。全量任务、冻结输入、参数、请求及响应证据已核对，第一轮 JSON 结果完整复现。',
        '第一轮运行器在后续支持重复轮次时修改过；运行记录按各自冻结版本校验，不宣称三轮运行器文件完全相同。原报告和冻结文件保持不变。','',
        '每轮明确标签主数据含 15 个情境；全部主数据 30 个，急症补充 4 个。多次调用与因素变体不增加独立病例数。','',
        '## 各轮及三轮综合','',
        '情境等权指标先在输入内平均轮次，再在情境内平均输入，最后平均情境；有效指标只平均有效回答，完整计划指标将无有效决策计为未获得正确答案。综合不是多数投票。区间以情境重采样 5,000 次，种子 20260922。单模型有效分母不同，模型差异应看下方共同有效配对结果。','',
        '| 分析 | 轮次 | 模型 | 计划/有效 | 有效回答可接受率 | 情境等权有效：95% CI | 情境等权完整计划：95% CI |',
        '|---|---|---|---:|---:|---|---|']
    for layer in LAYERS:
        for n in ('1','2','3','combined'):
            for role in ROLES:
                m=report['metrics'][n][layer][role]
                lines.append(f"| {labels[layer]} | {n} | {role} | {m['planned_requests']}/{m['valid_decisions']} | {pct(m['correct_rate_valid'])} | {interval(m['scenario_equal_valid'])} | {interval(m['scenario_equal_full_plan'])} |")
    lines+=['','## Jev 与 Luna 的主分析及稳定性','',
            '| 轮次 | 共同有效配对差值：95% CI | 完整计划配对差值：95% CI | 共同有效/计划配对 |','|---|---|---|---:|']
    for n in ('1','2','3','combined'):
        b=report['paired_comparisons'][n]['main_clear']['luna']; v=b['common_valid']
        lines.append(f"| {n} | {interval(v)} | {interval(b['full_plan'])} | {v['common_valid_request_pairs']}/{v['planned_request_pairs']} |")
    lines+=['','## 两项预设次要比较','',
        '第一轮明确标签子集、共同有效配对，情境等权双侧精确符号翻转检验；两项作为一个 Holm 校正族。假设零假设下情境差值符号可交换，检验不意味着临床安全。其他分层只作探索性解释。','',
        '| 比较 | 情境数 | 原始 p | Holm p |','|---|---:|---:|---:|']
    for x in report['secondary_tests']:
        lines.append(f"| {x['comparison']} | {x['scenarios']} | {x['p_raw']:.6f} | {x['p_holm']:.6f} |")
    lines+=['','## 答案重复稳定性','',
        '下表为全部主数据。只有所比较轮次均有效才计算答案一致；连续失败不算一致。全部层级及两轮改变率见 stability.csv。','',
        '| 模型 | 轮次 | 计划输入 | 均有效 | 因失败无法判断 | 一致率 | 改变率 |','|---|---|---:|---:|---:|---:|---:|']
    for x in report['stability']:
        if x['layer']=='main_full':
            lines.append(f"| {x['role']} | {x['rounds']} | {x['planned_inputs']} | {x['valid_inputs']} | {x['invalid_inputs']} | {pct(x['agreement_rate_valid'])} | {pct(x['change_rate_valid'])} |")
    lines+=['','## 错误方向与急症','',
        'D→C 单独算急症低估，C/D 参考集合接受 C 或 D。失败不赋予临床等级。全部主数据的明确 D 输入只来自两个基础情境；急症补充四个情境另列。逐情境分母和错误计数见 scenario_metrics.csv。','',
        '| 轮次 | 模型 | 有效回答 | 低估 | 跨两级低估 | 高估 | 非急症→D | D→非D / 有效D |','|---|---|---:|---:|---:|---:|---:|---|']
    for n in ('1','2','3'):
        for role,m in report['metrics'][n]['main_full'].items():
            lines.append(f"| {n} | {role} | {m['valid_decisions']} | {m['undertriage_valid']} | {m['cross_two_levels_under_valid']} | {m['overtriage_valid']} | {m['non_emergency_to_D_valid']} | {m['d_to_non_d_valid']}/{m['explicit_d_valid']} |")
    lines+=['','## 因素与信息版本','',
        '仅比较单因素不同的输入；信息版本按各自参考标签评价，不将答案改变直接称为改善。敏感性筛选必须保留完整配对，并报告损失数量。因素结果为描述性，不进行因果或人群公平性推断。',
        '种族因素为“显式 Black 描述与未标注种族描述”，不是两组都明确标注的 White/Black 对照。',
        '分层汇总见 factor_summary.csv；每一个配对及其有效性见 factor_pairs.csv。匹配问题：'+json.dumps(report['factors']['issues'],ensure_ascii=False),'',
        '## Jev 概率校准','',
        '第一轮为主要校准描述，后两轮分别复核；仅使用单标签及有效概率。Brier 为四类平方误差之和，NLL 为自然对数；零真实标签概率记 infinity，不裁剪。图用固定十等宽 top-label 置信度区间，空区间不插值。概率通过原归一化容差，不重新归一化，均值是描述性结果，不选择部署阈值。',
        '完整校准指标与分箱见 calibration.json；图见 figures/calibration.png。','',
        '## 费用与局限','',
        f"含预检的已知报告及估算费用为 ${report['audit']['costs']['total']['reported_plus_estimated_including_pilot_usd']}；另有一次费用未知，不能当成零。永久预算预留 ${report['audit']['costs']['total']['permanent_reservation_usd']}，不是实际支出。分模型费用状态及普通采集延迟见 results.json。", 
        '成本包含 OpenRouter 报告值与 DeepSeek 峰时未缓存估算，非最终含税账单；Codex 协作模型用量不在供应商实验账本内。普通采集延迟不是受控并发效率测试。',
        '只有 34 个基础情境；主分析明确病例只有 15 个。公开病例与源标签可能有偏差，AI 疑点审查未经过独立临床验证。重复稳定不代表正确，不能宣称等效、非劣或可安全部署。','',
        '## 产物','',
        '- results.json：全部结果、分层配对、费用与审计。',
        '- scenario_metrics.csv、factor_summary.csv、factor_pairs.csv、stability.csv：明细与分母。',
        '- calibration.json：校准指标和固定分箱。',
        '- provenance.json：采集后分析版本、软件、输入与输出哈希。',
        '- figures/：轮次成绩、答案稳定性、错误方向、校准图，PNG 和 SVG。','']
    highlights=['## 主要解读','',
        '主分析差异以情境等权配对为准，以下均将解析失败保留在完整计划分母：','']
    for n in ('1','2','3'):
        highlights.append(f"- 第{n}轮 Jev 对 Luna 的差值：{difference_interval(report['paired_comparisons'][n]['main_clear']['luna']['full_plan'])}。")
    highlights += ['', '两项第一轮次要检验 Holm 校正 p 值：'+ '；'.join(f"{x['comparison']}={x['p_holm']:.6f}" for x in report['secondary_tests'])+'。按0.05阈值解释校正后的证据，不依据未校正区间宣称多模型全面优越。',
        '重复稳定性只说明相同输入的回答是否一致。主数据三轮均有效输入上的完全一致率：'+ '；'.join(f"{role} {pct(next(x for x in report['stability'] if x['role']==role and x['layer']=='main_full' and x['rounds']=='1-2-3')['agreement_rate_valid'])}" for role in ROLES)+'。','']
    lines[4:4]=highlights
    lines.insert(lines.index('## 各轮及三轮综合')+2,'单轮模型配对区间沿用原汇总器的情境遍历顺序和数值操作，以完整复现第一轮区间；三轮综合及新增单模型区间使用排序后的情境。均为固定种子的情境 bootstrap。\n')
    info_lines=['','主客观信息对照（主数据）：下列为完整有效配对上的描述性比例，按两端各自参考标签评分；不进行独立病例数膨胀的请求级显著性检验。','',
        '| 轮次 | 模型 | 掩码 | 计划/筛选损失/有效配对 | 主观版正确率 | 客观版正确率 | 差值（百分点） |',
        '|---|---|---|---:|---:|---:|---:|']
    for x in report['factors']['summary']:
        if x['factor']=='information_level' and x['cohort']=='main':
            delta='NA' if x['accuracy_difference'] is None else f"{100*x['accuracy_difference']:.2f}"
            info_lines.append(f"| {x['round']} | {x['role']} | {x['mask']} | {x['planned_pairs']}/{x['lost_selection_pairs']}/{x['valid_pairs']} | {pct(x['baseline_accuracy'])} | {pct(x['changed_accuracy'])} | {delta} |")
    lines[lines.index('## Jev 概率校准'):lines.index('## Jev 概率校准')]=info_lines+['']
    calibration_lines=['','返回概率含零值时，NLL 无穷大反映 API 序列化概率下的对数损失，不能推断服务内部未舍入概率也恰好为零。高置信错误说明返回概率尚不能直接作为安全分流保证。','',
        '| 轮次 | 分层 | 纳入有效/排除边界/无效 | Brier（回答等权） | NLL | 参考等级零概率数 | ≥0.9错误/该区间有效 |','|---|---|---:|---:|---:|---:|---:|']
    blocks=[report['calibration']['primary']]+[report['calibration']['later_rounds'][str(n)] for n in (2,3)]
    for block in blocks:
        for layer,m in block['layers'].items():
            calibration_lines.append(f"| {block['round']} | {layer} | {m['valid']}/{m['excluded_edge']}/{m['invalid']} | {m['brier']:.6f} | {m['mean_nll']} | {m['zero_probability_true_label_count']} | {m['high_confidence_errors']}/{m['high_confidence_valid']} |")
    at=lines.index('## 费用与局限')
    lines[at:at]=calibration_lines+['']
    return '\n'.join(lines)

def figures(report, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'svg.hashsalt':'jev-three-round-v1','font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    directory=output/'figures'; directory.mkdir(exist_ok=True)
    colors=['#0072B2','#E69F00','#009E73','#CC79A7']
    def save(fig,name):
        fig.tight_layout()
        fig.savefig(directory/(name+'.png'),dpi=220)
        fig.savefig(directory/(name+'.svg'),metadata={'Date':None})
        plt.close(fig)
    fig,ax=plt.subplots(figsize=(7,4))
    for role,color in zip(ROLES,colors):
        ax.plot([1,2,3],[report['metrics'][str(n)]['main_clear'][role]['scenario_equal_full_plan']['estimate']*100 for n in (1,2,3)],marker='o',label=role,color=color)
    ax.set(xticks=[1,2,3],xlabel='Round',ylabel='Scenario-equal valid-correct yield (%)',title='A. Clear main cases: all planned requests',ylim=(0,100)); ax.legend(ncol=4)
    save(fig,'round_accuracy')
    fig,ax=plt.subplots(figsize=(7,4))
    vals=[next(x for x in report['stability'] if x['role']==role and x['layer']=='main_full' and x['rounds']=='1-2-3') for role in ROLES]
    ax.bar(ROLES,[x['agreement_rate_valid']*100 for x in vals],color=colors)
    for i,x in enumerate(vals): ax.text(i,3,f"{x['identical']}/{x['valid_inputs']}\ninvalid: {x['invalid_inputs']}",ha='center')
    ax.set(ylim=(0,105),ylabel='Three-round identical answers (%)',title='B. Main cases: only three-valid inputs')
    save(fig,'stability')
    fig,axes=plt.subplots(1,3,figsize=(12,4),sharey=True)
    for n,ax in enumerate(axes,1):
        for offset,key,label,col in [(-.2,'undertriage_valid','Undertriage','#D55E00'),(.2,'overtriage_valid','Overtriage','#0072B2')]:
            ax.bar([i+offset for i in range(4)],[report['metrics'][str(n)]['main_full'][r][key]/report['metrics'][str(n)]['main_full'][r]['valid_decisions']*100 for r in ROLES],width=.4,label=label,color=col)
        ax.set(xticks=range(4),xticklabels=ROLES,title=f'Round {n}')
    axes[0].set_ylabel('Errors / valid main-case answers (%)'); axes[0].set_ylim(0,28); axes[-1].legend()
    fig.suptitle('C. Error direction relative to source labels')
    save(fig,'error_direction')
    fig,axes=plt.subplots(1,2,figsize=(10,4),sharex=True,sharey=True)
    calibration=report['calibration']
    blocks=[calibration['primary']]+[calibration['later_rounds'][str(n)] for n in (2,3)]
    for ax,layer in zip(axes,('main_clear','emergency')):
        ax.plot([0,1],[0,1],ls='--',color='gray',label='Perfect calibration')
        for b,col in zip(blocks,colors):
            bins=[x for x in b['layers'][layer]['bins'] if x['count']]
            ax.scatter([x['mean_confidence'] for x in bins],[x['accuracy'] for x in bins],s=[18+min(x['count'],150) for x in bins],color=col,label=f"Round {b['round']}",alpha=.7)
        ax.set(xlim=(0,1.02),ylim=(0,1.02),xlabel='Mean top-label probability',title=layer)
    axes[0].set_ylabel('Observed label agreement'); axes[-1].legend(fontsize=8)
    fig.suptitle('D. Jev reliability: fixed bins; marker size reflects count')
    save(fig,'calibration')
