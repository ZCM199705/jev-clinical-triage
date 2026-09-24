"""Post-collection Brief selection of existing frozen JEV evidence; no API calls."""
from __future__ import annotations
import csv
import hashlib
import json
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from scripts import make_manuscript_figures as old
from scripts.manuscript_figure_data import ROOT, ROLES, read_csv, read_json, save_csv, sha, load_decisions, derive, risk_curve, selected_risk

OUT = ROOT/'reports/manuscript_figures_brief_v4'
SRC = OUT/'source_data'
LEGACY = ROOT/'reports/manuscript_figures_v1'
NAMES,COLORS,MARKERS=dict(old.NAMES),old.COLORS,old.MARKERS
NAMES['jev']='Jev'
old.NAMES['jev']='Jev'

def match(rows,**kwargs): return [r for r in rows if all(str(r[k])==str(v) for k,v in kwargs.items())]
def panel(ax,letter,title): old.panel(ax,letter,title)
def save(fig,name): old.save(fig,name)

def figure1(counts,stable,report):
    fig,axs=old.subplots(2,2,145);a,b,c,d=axs.ravel()
    fig.subplots_adjust(left=.09,right=.975,bottom=.18,top=.93,wspace=.40,hspace=.78)
    cats=[('main_clear','Single-grade reference scenarios (15)','#237A8A','o'),('main_full','All main scenarios (30)','#747987','s'),('main_edge','Two-grade reference scenarios (15)','#AA8650','^')]
    data=[]
    for j,(co,label,color,marker) in enumerate(cats):
        vals=[]
        for role in ROLES:
            row=match(counts,role=role,round=1,cohort=co)[0]
            data.append(row);vals.append(row['agreement_full_plan']*100)
        a.scatter(np.arange(4)+(j-1)*.18,vals,color=color,marker=marker,s=22,label=label,zorder=3)
    a.set(xticks=range(4),xticklabels=[NAMES[r] for r in ROLES],ylabel='Reference-label agreement (%)',ylim=(45,102),yticks=[50,60,70,80,90,100])
    a.legend(loc='upper center',bbox_to_anchor=(.5,-.20),ncol=1,fontsize=6.5,handletextpad=.4,labelspacing=.25)
    panel(a,'a','Agreement by reference-label stratum')
    forest=[]
    for i,n in enumerate(['1','2','3']):
        m=report['paired_comparisons'][n]['main_clear']['luna']['full_plan'];x=m['estimate']*100;lo,hi=np.array(m['ci95'])*100
        b.errorbar(x,2-i,xerr=[[x-lo],[hi-x]],fmt='o',color=COLORS['jev'] if i==0 else '#747987',ms=5 if i==0 else 4,capsize=2)
        b.text(23,2-i-.28,f'{x:.2f} [{lo:.2f}, {hi:.2f}]',va='center',ha='right',fontsize=6.4)
        forest.append({'round':n,'difference_pp':x,'ci_lower_pp':lo,'ci_upper_pp':hi,'scenarios':15,'planned_pairs':480})
    b.axvline(0,color='#999999',ls='--',lw=.7)
    b.set(xlim=(-1,24),ylim=(-.7,2.7),yticks=[2,1,0],yticklabels=['Round 1*','Round 2','Round 3'],xlabel='Jev − Luna agreement (percentage points)')
    panel(b,'b','Prespecified comparison · single-grade')
    errcats=[('undertriage','Undertriage','#C77C65'),('correct','Agreement','#B9CDD0'),('overtriage','Overtriage','#C6B793'),('technical_failure','Technical failure','#444444')]
    first=match(counts,round=1,cohort='main_full');left=np.zeros(4)
    for key,label,color in errcats:
        vals=np.array([match(first,role=r)[0][key]/960*100 for r in ROLES]);c.barh(range(4),vals,left=left,color=color,label=label,height=.6,edgecolor='white',linewidth=.3)
        if key=='correct':
            for i,v in enumerate(vals):c.text(left[i]+v/2,i,f'{v:.1f}',ha='center',va='center',fontsize=6)
        left+=vals
    c.set(yticks=range(4),yticklabels=[NAMES[r] for r in ROLES],xlim=(0,100),xlabel='Planned requests (%)');c.invert_yaxis()
    c.legend(loc='upper center',bbox_to_anchor=(.5,-.23),ncol=2,columnspacing=.7,handlelength=1)
    panel(c,'c','Error direction · first round, all main scenarios')
    stcats=[('stable_correct','Stable, reference-agreeing','#8FB8BE'),('stable_incorrect','Stable, reference-disagreeing','#C77C65'),('changed_all_acceptable','Changed, always agreeing','#D0DCE0'),('changed_any_incorrect','Changed, some disagreement','#BDA98C'),('technical_failure','Technical failure','#444444')]
    left=np.zeros(4)
    for key,label,color in stcats:
        vals=np.array([r[key]/960*100 for r in stable]);d.barh(range(4),vals,left=left,color=color,label=label,height=.6,edgecolor='white',linewidth=.3)
        if key in ['stable_correct','stable_incorrect']:
            for i,v in enumerate(vals):
                if v>6:d.text(left[i]+v/2,i,str(stable[i][key]),ha='center',va='center',fontsize=6.5)
        left+=vals
    d.set(yticks=range(4),yticklabels=[NAMES[r] for r in ROLES],xlim=(0,117),xticks=[0,25,50,75,100],xlabel='Planned inputs (%)');d.invert_yaxis()
    for i,row in enumerate(stable):d.text(102,i,f'{row["consistency_valid"]*100:.1f}%',va='center',fontsize=6.5)
    d.text(101,-.60,'Identical¹',fontsize=6,ha='left')
    d.legend(loc='upper center',bbox_to_anchor=(.5,-.23),ncol=2,columnspacing=.6,handlelength=1,fontsize=6)
    panel(d,'d','Three-round consistency and reference agreement')
    for suffix,rows in [('a',data),('b',forest),('c',first),('d',stable)]:save_csv(SRC/f'Figure1{suffix}.csv',rows)
    save(fig,'Figure1')

def figure2(counts,probs,ecells):
    fig,axs=old.subplots(1,2,88);a,b=axs.ravel()
    fig.subplots_adjust(left=.09,right=.96,bottom=.27,top=.83,wspace=.36)
    curve=risk_curve(probs,480);thresholds=[selected_risk(probs,t,480) for t in [.5,.7,.8,.9]]
    a.step([r['coverage_valid']*100 for r in curve],[r['risk']*100 for r in curve],where='post',color=COLORS['jev'],lw=1.3)
    for i,r in enumerate(thresholds):
        if r['risk'] is None:continue
        x,y=r['coverage_valid']*100,r['risk']*100;a.scatter(x,y,color=COLORS['jev'],s=16,zorder=4)
        a.annotate(f'τ = {r["threshold"]:.1f}',(x,y),xytext=[(6,8),(5,-14),(6,8),(7,-12)][i],textcoords='offset points',fontsize=6)
    a.set(xlim=(0,105),ylim=(0,32),xticks=[0,25,50,75,100],xlabel='Coverage of valid inputs (%)',ylabel='Disagreement risk (%)')
    a.text(.02,.97,'τ ≥ 0.9: 8 errors / 152 inputs',transform=a.transAxes,va='top',fontsize=6.5)
    panel(a,'a','Selective prediction')
    select=match(ecells,concurrency=1);source=[]
    for role in ROLES:
        rr=match(select,role=role);lat=[float(r['valid_http_p50_ms'])/1000 for r in rr]
        accuracy=match(counts,role=role,round=1,cohort='main_clear')[0]['agreement_full_plan']*100
        for r,y in zip(rr,lat):
            b.scatter(y,accuracy,s=18,facecolors=COLORS[role],edgecolors=COLORS[role],alpha=.38,marker=MARKERS[role])
            source.append({**r,'single_grade_agreement_pct':accuracy,'http_p50_s':y,'point_type':'block'})
        y=float(np.median(lat))
        b.scatter(y,accuracy,s=42,facecolors=COLORS[role],edgecolors=COLORS[role],marker=MARKERS[role],zorder=5)
        offset={'jev':(6,7),'luna':(6,8),'gemini':(-34,-12),'deepseek':(-60,9)}[role]
        b.annotate(NAMES[role],(y,accuracy),xytext=offset,textcoords='offset points',fontsize=7)
    b.set(xlim=(.2,1.53),ylim=(56,79),xlabel='HTTP latency p50 (s)',ylabel='Single-grade reference-label agreement (%)')
    panel(b,'b','Performance and service profile')
    fig.text(.09,.055,'First-round single-grade scenarios: 476 valid / 480 planned. Service: concurrency 1, three blocks/model.',fontsize=6.3)
    save_csv(SRC/'Figure2a_curve.csv',curve);save_csv(SRC/'Figure2a_thresholds.csv',thresholds);save_csv(SRC/'Figure2a_inputs.csv',probs)
    save_csv(SRC/'Figure2b.csv',source);save(fig,'Figure2')

def supplement1(counts,report):
    fig,axs=old.subplots(1,2,83);a,b=axs.ravel()
    fig.subplots_adjust(left=.10,right=.98,top=.80,bottom=.25,wspace=.35)
    data=[]
    for role in ROLES:
        rr=[]
        for n in [1,2,3]:
            row=match(counts,role=role,round=n,cohort='main_full')[0]
            rr.append(row['agreement_full_plan']*100);data.append(row)
        a.plot([1,2,3],rr,color=COLORS[role],marker=MARKERS[role],ms=3)
    a.set(xticks=[1,2,3],xlabel='Round',ylabel='Agreement (%)',ylim=(70,90))
    panel(a,'a','All main scenarios')
    source_b=[]
    for j,(co,marker,face) in enumerate([('main_clear','o','full'),('main_edge','^','none')]):
        for i,role in enumerate(ROLES):
            vals=[]
            for n in [1,2,3]:
                r=match(counts,role=role,round=n,cohort=co)[0];vals.append(r['agreement_full_plan']*100);source_b.append(r)
            x=i+(j-.5)*.18
            b.plot([x,x],[min(vals),max(vals)],color=COLORS[role],lw=.9,alpha=.65)
            b.scatter(x,np.mean(vals),s=24,marker=marker,facecolors='white' if face=='none' else COLORS[role],edgecolors=COLORS[role],zorder=4)
    b.set(xticks=range(4),xticklabels=[NAMES[r] for r in ROLES],ylabel='Agreement (%)',ylim=(50,101))
    panel(b,'b','Single- and two-grade scenarios')
    b.legend(handles=[Line2D([],[],marker='o',linestyle='none',color='#444444',label='Single-grade'),Line2D([],[],marker='^',linestyle='none',markerfacecolor='white',markeredgecolor='#444444',color='#444444',label='Two-grade')],loc='lower right',ncol=2,fontsize=6)
    old.model_legend(fig,.01)
    fig.text(.10,.07,'Panel b: symbols show three-round means; whiskers show observed round ranges.',fontsize=6.2)
    save_csv(SRC/'FigureS1a.csv',data);save_csv(SRC/'FigureS1b.csv',source_b);save(fig,'FigureS1')

def supplement3(factors):
    fig,axs=old.subplots(1,3,105);a,b,c=axs.ravel();fig.subplots_adjust(left=.12,right=.98,top=.86,bottom=.23,wspace=.50)
    info=[r for r in factors if r['cohort']=='main' and r['factor']=='information_level' and r['mask']=='all']
    infosum=[]
    for i,role in enumerate(ROLES):
        rr=sorted(match(info,role=role),key=lambda r:int(r['round']));assert len(rr)==3
        vals=[100*float(r['accuracy_difference']) for r in rr];avg=float(np.mean(vals))
        a.plot([i,i],[min(vals),max(vals)],color=COLORS[role],lw=1,alpha=.7)
        a.scatter(i,avg,s=22,marker='o',facecolors=COLORS[role],edgecolors=COLORS[role],zorder=4)
        infosum.append({'role':role,'three_round_mean_pp':avg,'round_min_pp':min(vals),'round_max_pp':max(vals),'valid_pairs_by_round':'/'.join(r['valid_pairs'] for r in rr),'selected_pairs_by_round':'/'.join(r['selected_pairs'] for r in rr)})
    a.axhline(0,color='#999999',lw=.7,ls='--');a.set(xticks=range(4),xticklabels=[NAMES[r] for r in ROLES],ylim=(-1,16),ylabel='Objective − subjective (pp)');a.tick_params(axis='x',rotation=40);panel(a,'a','Information version')
    groups=defaultdict(Counter)
    for r in factors:
        if r['cohort']!='main' or r['mask']!='all' or r['factor']=='information_level':continue
        for k in ['planned_pairs','valid_pairs','prediction_changed','lowered','correct_to_wrong']:groups[int(r['round']),r['role'],r['factor']][k]+=int(r[k])
    fs=[('anchor_type','Anchoring'),('barrier_type','Access barrier'),('gender','Gender wording'),('race','Explicit Black wording')];details=[];summaries=[]
    for fac,label in fs:
        for role in ROLES:
            rr=[]
            for n in [1,2,3]:
                d=groups[n,role,fac];assert d['valid_pairs']>0
                row={'factor':fac,'factor_label':label,'role':role,'round':n,**d,'changed_pct':100*d['prediction_changed']/d['valid_pairs'],'lowered_pct':100*d['lowered']/d['valid_pairs'],'correct_to_wrong_pct':100*d['correct_to_wrong']/d['valid_pairs']}
                details.append(row);rr.append(row)
            summaries.append({'factor':fac,'factor_label':label,'role':role,'mean_changed_pct':float(np.mean([r['changed_pct'] for r in rr])),'round_min_pct':min(r['changed_pct'] for r in rr),'round_max_pct':max(r['changed_pct'] for r in rr)})
    for ax,chosen,letter,title in [(b,fs[:2],'b','Anchoring and access'),(c,fs[2:],'c','Demographic wording')]:
        vals=np.array([[match(summaries,factor=fac,role=role)[0]['mean_changed_pct'] for role in ROLES] for fac,label in chosen])
        ax.imshow(vals,cmap='Blues',vmin=0,vmax=35,aspect='auto')
        display=['Anchor','Barrier'] if letter=='b' else ['Gender','Black']
        ax.set(yticks=[0,1],yticklabels=display,xticks=range(4),xticklabels=[NAMES[r] for r in ROLES]);ax.tick_params(axis='x',rotation=40)
        for y in range(2):
            for x in range(4):ax.text(x,y,f'{vals[y,x]:.1f}',ha='center',va='center',fontsize=6.5,color='white' if vals[y,x]>23 else '#222222')
        panel(ax,letter,title)
    fig.text(.12,.055,'Three-round means are descriptive; whiskers in a are observed ranges. Shading in b–c: decision changed (% of valid pairs).',fontsize=6.1)
    save_csv(SRC/'FigureS3a.csv',infosum);save_csv(SRC/'FigureS3b_c_rounds.csv',details);save_csv(SRC/'FigureS3b_c_means.csv',summaries)
    save_csv(SRC/'FigureS3a_rounds.csv',info);save(fig,'FigureS3')

def supplement5(counts):
    hist=read_json(ROOT/'reports/chatgpt_health_historical_v1/results.json')['cohorts'];cohorts=['main_clear','main_full','main_edge']
    if any(hist[k]['matched']!=hist[k]['planned'] or hist[k]['invalid'] for k in cohorts):raise ValueError('Historical reference incomplete')
    fig=plt.figure(figsize=(183/25.4,121/25.4));gs=fig.add_gridspec(2,2,left=.14,right=.97,top=.92,bottom=.17,hspace=.71,wspace=.37,height_ratios=[1,1.1]);a,b=fig.add_subplot(gs[0,0]),fig.add_subplot(gs[0,1]);c=fig.add_subplot(gs[1,:]);data=[]
    for ax,source,letter,title in [(a,'historical','a','ChatGPT Health · Jan 2026'),(b,'current','b','Current APIs · Sep 2026')]:
        roles=['health'] if source=='historical' else ROLES
        for i,role in enumerate(roles):
            vals=[]
            for co in cohorts:
                r=hist[co] if source=='historical' else match(counts,role=role,round=1,cohort=co)[0]
                data.append({'source':source,'role':role,'cohort':co,'planned':r['planned'],'correct':r['correct'],'undertriage':r['undertriage'],'overtriage':r['overtriage'],'invalid':r.get('invalid',r.get('technical_failure',0)),'agreement_full_plan':r['agreement_full_plan']})
                vals.append(r['agreement_full_plan']*100)
            ax.scatter(np.arange(3)+(i-(len(roles)-1)/2)*.12,vals,color='#555555' if source=='historical' else COLORS[role],marker='o' if source=='historical' else MARKERS[role],s=22)
        ax.set(xticks=range(3),xticklabels=['Single-grade','All main scenarios','Two-grade'],ylabel='Agreement (%)',ylim=(40,102));panel(ax,letter,title)
    errcats=[('undertriage','Undertriage','#C77C65'),('correct','Agreement','#B9CDD0'),('overtriage','Overtriage','#C6B793'),('invalid','Unscorable','#444444')]
    rr=[hist['main_full']]+[match(counts,role=role,round=1,cohort='main_full')[0] for role in ROLES]
    left=np.zeros(5)
    for key,label,color in errcats:
        vals=np.array([(r.get(key,r.get('technical_failure',0)))/r['planned']*100 for r in rr]);c.barh(range(5),vals,left=left,color=color,height=.58,edgecolor='white',linewidth=.3);left+=vals
    c.set(yticks=range(5),yticklabels=['Health · Jan 2026']+[NAMES[r] for r in ROLES],xlim=(0,100),xlabel='Planned main inputs (%)');c.invert_yaxis();c.axhline(.5,color='#999999',lw=.6)
    panel(c,'c','Error direction')
    fig.legend(handles=[Patch(facecolor=color,label=label) for key,label,color in errcats],loc='lower center',bbox_to_anchor=(.53,.025),ncol=4,fontsize=6.3)
    fig.text(.14,.005,'Noncontemporaneous historical reference; API models were evaluated in a separate period.',fontsize=6.2)
    save_csv(SRC/'FigureS5.csv',data);save(fig,'FigureS5')

def tables(stable,counts,ecells,cal):
    for n in ['Table1.csv','Table1.md','Supplementary_Table_S1.csv','Supplementary_Table_S1.md']:
        shutil.copyfile(LEGACY/n,OUT/n)
    # The source tables retain their frozen numeric schema; only reader-facing text changes.
    for n in ['Table1.md','Supplementary_Table_S1.md']:
        p=OUT/n;content=p.read_text()
        content=content.replace('JEV','Jev').replace('Clear agreement','Single-grade reference agreement')
        content=content.replace('clear-label','single-grade reference').replace('ambiguous-label','two-grade reference')
        content=content.replace('ambiguous set agreement','two-grade set agreement').replace('ambiguous-case text','two-grade-scenario text')
        p.write_text(content)
    fmt=read_json(ROOT/'reports/format_explanation_v1/results.json')['summary']
    natural=read_json(ROOT/'reports/natural_coding_v1/results.json')['summary_by_source_role']
    rows=[]
    for role in ROLES[1:]:
        r=fmt[f'r1_{role}_main_full'];nr=[natural[role][co] for co in ['main_full','emergency']]
        rows.extend([
            {'experiment':'Structured JSON, first round','scope':'60 main inputs','role':role,'planned':r['planned'],'valid':r['baseline_valid'],'acceptable':r['baseline_correct'],'unresolved':r['planned']-r['baseline_valid']},
            {'experiment':'Explanation JSON, one sample','scope':'60 main inputs','role':role,'planned':r['planned'],'valid':r['explanation_valid'],'acceptable':r['explanation_correct'],'unresolved':r['planned']-r['explanation_valid']},
            {'experiment':'Natural-language consensus','scope':'60 main + 8 emergency inputs','role':role,'planned':sum(x['planned'] for x in nr),'valid':sum(x['consensus_valid'] for x in nr),'acceptable':sum(x['correct_valid'] for x in nr),'unresolved':sum(x['unresolved'] for x in nr)}])
    save_csv(OUT/'Supplementary_Table_S2.csv',rows)
    lines=['# Supplementary Table S2 | Output-format sensitivity','','| Experiment | Population | Luna acceptable / valid / planned | Gemini | DeepSeek |','|---|---|---:|---:|---:|']
    for ex in ['Structured JSON, first round','Explanation JSON, one sample','Natural-language consensus']:
        rr=[match(rows,experiment=ex,role=role)[0] for role in ROLES[1:]]
        lines.append('| '+ex+' | '+rr[0]['scope']+' | '+' | '.join(f"{r['acceptable']}/{r['valid']}/{r['planned']}" for r in rr)+' |')
    lines += ['','Values are acceptable outputs / valid or codable outputs / planned inputs. The natural-language row includes eight emergency inputs per model; it is not directly comparable to the 60-main-input rows. One explanation sample was compared with the first structured round; later baseline rounds and paired differences remain in the original analysis files. Model consensus is an exploratory coding convention, not an independent clinical label. Direction of format sensitivity varies by model.']
    (OUT/'Supplementary_Table_S2.md').write_text('\n'.join(lines)+'\n')
    layer=cal['primary']['layers']['main_clear']
    save_csv(OUT/'Supplementary_Calibration_Data.csv',layer['bins'])
    (OUT/'Supplementary_Calibration_Note.md').write_text(f"# Jev first-round probability reliability\n\nSingle-grade reference scenarios: 476/480 valid probability inputs. Multiclass Brier: {layer['brier']:.6f}. Mean NLL: infinite, with {layer['zero_probability_true_label_count']} zero-probability true-label errors. The accompanying CSV preserves all ten fixed-width bins, including empty bins. These metrics are descriptive; no deployment threshold was selected.\n")

def reader_docs():
    source = ROOT/'scripts/templates/brief_v4'
    for name in ['Figure_legends_EN.md', '图表解读_CN.md', '来源与修订.md']:
        shutil.copyfile(source/name, OUT/name)

def main():
    OUT.mkdir(parents=True,exist_ok=True);SRC.mkdir(exist_ok=True)
    old.OUT=OUT;old.SRC=SRC;old.FIGURES.clear();old.QA.clear()
    protected=[p for d in ['freezes','runtime','analysis'] for p in (ROOT/d).rglob('*') if p.is_file() and '__pycache__' not in p.parts]+[ROOT/'configs/project_budget.json',ROOT/'reports/first_round_results.json']
    before={str(p.relative_to(ROOT)):sha(p) for p in protected}
    cases,results,digest=load_decisions();counts,stable,probs,stable_inputs=derive(cases,results)
    report=read_json(ROOT/'reports/three_round_v1/results.json');cal=read_json(ROOT/'reports/three_round_v1/calibration.json')
    factors=read_csv(ROOT/'reports/three_round_v1/factor_summary.csv');scenarios=read_csv(ROOT/'reports/three_round_v1/scenario_metrics.csv');ecells=read_csv(ROOT/'reports/efficiency_v1/cells.csv')
    assert len(ecells)==60 and sum(int(r['planned']) for r in ecells)==12000
    figure1(counts,stable,report);figure2(counts,probs,ecells);supplement1(counts,report)
    for suffix in ['pdf','svg','png','tiff']:shutil.copyfile(LEGACY/f'FigureS2.{suffix}',OUT/f'FigureS2.{suffix}')
    shutil.copyfile(LEGACY/'source_data/FigureS2.csv',SRC/'FigureS2.csv')
    supplement3(factors)
    for suffix in ['pdf','svg','png','tiff']:shutil.copyfile(LEGACY/f'FigureS7.{suffix}',OUT/f'FigureS4.{suffix}')
    shutil.copyfile(LEGACY/'source_data/FigureS7.csv',SRC/'FigureS4.csv')
    supplement5(counts);tables(stable,counts,ecells,cal);reader_docs()
    assert before=={str(p.relative_to(ROOT)):sha(p) for p in protected}
    assert all(not r['text_outside_canvas'] for r in old.QA), old.QA
    provenance={'analysis_timing':'post_collection','preserved_v1_dir':str(LEGACY.relative_to(ROOT)),'protected_inputs_unchanged':True,'additional_api_calls':0,'response_hash_collection_digest':digest,'code_sha256':sha(Path(__file__)),'matplotlib_version':old.mpl.__version__,'reused_unchanged_figures':{'FigureS2':'v1 FigureS2','FigureS4':'v1 FigureS7'},'source_data_hashes':{p.name:sha(p) for p in sorted(SRC.glob('*.csv'))},'figure_hashes':{p.name:sha(p) for p in sorted(OUT.glob('Figure*.*')) if p.suffix in ['.png','.svg','.pdf','.tiff']}}
    (OUT/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    (OUT/'render_checks.json').write_text(json.dumps(old.QA,indent=2)+'\n')
    print('Brief figure selection:',list(provenance['figure_hashes'])[:8], 'rendered',len(old.QA),'reused',2,'text outside',sum(len(r['text_outside_canvas']) for r in old.QA))

if __name__=='__main__':main()
