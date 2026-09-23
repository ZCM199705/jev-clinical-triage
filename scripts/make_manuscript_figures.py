"""Nature-family manuscript figures from frozen offline evidence (Python only)."""
import argparse
import csv
import json
import sys
from collections import Counter,defaultdict
from pathlib import Path
import numpy as np
import matplotlib as mpl
mpl.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.manuscript_figure_data import (ROLES,read_json,read_csv,save_csv,sha,load_decisions,derive,risk_curve,selected_risk)

NAMES={'jev':'JEV','luna':'Luna','gemini':'Gemini','deepseek':'DeepSeek'}
COLORS={'jev':'#237A8A','luna':'#747987','gemini':'#AA8650','deepseek':'#8876A8'}
MARKERS={'jev':'o','luna':'s','gemini':'^','deepseek':'D'}
OUT=ROOT/'reports/manuscript_figures_v1'; SRC=OUT/'source_data'
FIGURES=[]
QA=[]
mpl.rcParams.update({'font.family':'Arial','font.size':7,'axes.titlesize':7,'axes.labelsize':7,
 'xtick.labelsize':7,'ytick.labelsize':7,'legend.fontsize':6.5,'legend.frameon':False,
 'axes.spines.top':False,'axes.spines.right':False,'axes.linewidth':.6,'lines.linewidth':1,
 'xtick.major.width':.6,'ytick.major.width':.6,'xtick.major.size':2.5,'ytick.major.size':2.5,
 'svg.fonttype':'none','pdf.fonttype':42,'svg.hashsalt':'jev_manuscript_v1','savefig.facecolor':'white'})

def subplots(r,c,height,width=183):
    return plt.subplots(r,c,figsize=(width/25.4,height/25.4),squeeze=False)

def panel(ax,letter,title):
    ax.set_title(title,loc='left',pad=8)
    ax.text(-.13,1.08,letter,transform=ax.transAxes,fontweight='bold',fontsize=8,va='top')

def model_legend(fig,y=.015):
    fig.legend(handles=[Line2D([],[],color=COLORS[r],marker=MARKERS[r],label=NAMES[r],ms=3) for r in ROLES],
               loc='lower center',bbox_to_anchor=(.5,y),ncol=4)

def save(fig,name):
    fig.canvas.draw()
    # All explicit text must fit on the fixed-size page. Axis tick visibility is checked visually too.
    renderer=fig.canvas.get_renderer();box=fig.bbox
    outside=[]
    for obj in fig.findobj(mpl.text.Text):
        if not obj.get_visible() or not obj.get_text():continue
        b=obj.get_window_extent(renderer)
        if b.x0 < -2 or b.y0 < -2 or b.x1 > box.width+2 or b.y1 > box.height+2:outside.append(obj.get_text())
    QA.append({'figure':name,'width_mm':round(fig.get_figwidth()*25.4,3),'height_mm':round(fig.get_figheight()*25.4,3),'text_outside_canvas':outside})
    for fmt in ['pdf','svg','png','tiff']:
        kwargs={'dpi':600 if fmt=='tiff' else 180}
        if fmt=='pdf':kwargs['metadata']={'CreationDate':None,'ModDate':None,'Creator':'JEV offline figures'}
        if fmt=='svg':kwargs['metadata']={'Date':None,'Creator':'JEV offline figures'}
        if fmt=='tiff':kwargs['pil_kwargs']={'compression':'tiff_lzw'}
        fig.savefig(OUT/f'{name}.{fmt}',**kwargs)
    FIGURES.append(name);plt.close(fig)

def match(rows,**kwargs):
    return [r for r in rows if all(str(r[k])==str(v) for k,v in kwargs.items())]

def main_fig1(counts,stable,report):
    fig,axs=subplots(2,2,145);a,b,c,d=axs.ravel()
    fig.subplots_adjust(left=.09,right=.975,bottom=.18,top=.93,wspace=.40,hspace=.78)
    cats=[('main_clear','Clear (15 scenarios)','#237A8A','o'),('main_full','All (30 scenarios)','#747987','s'),('main_edge','Ambiguous (15 scenarios)','#AA8650','^')]
    historical=read_json(ROOT/'reports/chatgpt_health_historical_v1/results.json')['cohorts']
    data=[]
    for j,(co,label,color,marker) in enumerate(cats):
        vals=[]
        for role in ROLES:
            row=match(counts,role=role,round=1,cohort=co)[0]
            data.append({**row,'source':'current_api','collection_period':'2026-09','comparison_role':'current_first_round'})
            vals.append(row['agreement_full_plan']*100)
        a.scatter(np.arange(4)+(j-1)*.18,vals,color=color,marker=marker,s=22,label=label,zorder=3)
        hr=historical[co]
        if hr['matched']!=hr['planned'] or hr['invalid']:
            raise ValueError('Figure 1a historical reference requires fully matched, valid inputs.')
        a.scatter(4.65+(j-1)*.18,hr['agreement_full_plan']*100,color='#555555',marker=marker,s=22,zorder=3)
        data.append({'role':'chatgpt_health','round':'','cohort':co,'planned':hr['planned'],
                     'scenarios':hr['scenario_count'],'correct':hr['correct'],'undertriage':hr['undertriage'],
                     'overtriage':hr['overtriage'],'technical_failure':hr['invalid'],
                     'agreement_full_plan':hr['agreement_full_plan'],'source':'historical_web_product',
                     'collection_period':'2026-01-09/2026-01-11','comparison_role':'posthoc_historical_reference'})
    a.axvline(3.75,color='#BBBBBB',ls=':',lw=.7)
    a.set(xticks=[0,1,2,3,4.65],xticklabels=[NAMES[r] for r in ROLES]+['ChatGPT Health\nJan 2026\n(historical)'],
          xlim=(-.5,5.35),ylabel='Source-label agreement (%)',ylim=(45,102),yticks=[50,60,70,80,90,100])
    a.get_xticklabels()[-1].set_color('#555555')
    a.legend(loc='upper center',bbox_to_anchor=(.45,-.26),ncol=1,fontsize=6.5,handletextpad=.4,labelspacing=.25)
    panel(a,'a','Agreement by reference-label stratum')
    forest=[]
    for i,n in enumerate(['1','2','3']):
        m=report['paired_comparisons'][n]['main_clear']['luna']['full_plan'];x=m['estimate']*100;lo,hi=np.array(m['ci95'])*100
        b.errorbar(x,2-i,xerr=[[x-lo],[hi-x]],fmt='o',color=COLORS['jev'] if i==0 else '#747987',ms=5 if i==0 else 4,capsize=2)
        b.text(23,2-i-.28,f'{x:.2f} [{lo:.2f}, {hi:.2f}]',va='center',ha='right',fontsize=6.4)
        forest.append({'round':n,'difference_pp':x,'ci_lower_pp':lo,'ci_upper_pp':hi,'scenarios':15,'planned_pairs':480})
    b.axvline(0,color='#999999',ls='--',lw=.7);b.set(xlim=(-1,24),ylim=(-.7,2.7),yticks=[2,1,0],yticklabels=['Round 1*','Round 2','Round 3'],xlabel='JEV − Luna agreement (percentage points)')
    panel(b,'b','Prespecified comparison in clear cases')
    errcats=[('undertriage','Undertriage','#C77C65'),('correct','Agreement','#B9CDD0'),('overtriage','Overtriage','#C6B793'),('technical_failure','Technical failure','#444444')]
    first=match(counts,round=1,cohort='main_full');left=np.zeros(4)
    for key,label,color in errcats:
        vals=np.array([match(first,role=r)[0][key]/960*100 for r in ROLES]);c.barh(range(4),vals,left=left,color=color,label=label,height=.6,edgecolor='white',linewidth=.3)
        if key=='correct':
            for i,v in enumerate(vals):c.text(left[i]+v/2,i,f'{v:.1f}',ha='center',va='center',fontsize=6.5)
        left+=vals
    c.set(yticks=range(4),yticklabels=[NAMES[r] for r in ROLES],xlim=(0,100),xlabel='Planned requests (%)');c.invert_yaxis()
    c.legend(loc='upper center',bbox_to_anchor=(.5,-.23),ncol=2,columnspacing=.7,handlelength=1)
    panel(c,'c','Error direction · first round, all scenarios')
    stcats=[('stable_correct','Stable, correct','#8FB8BE'),('stable_incorrect','Stable, incorrect','#C77C65'),('changed_all_acceptable','Changed, all acceptable','#D0DCE0'),('changed_any_incorrect','Changed, any incorrect','#BDA98C'),('technical_failure','Technical failure','#444444')]
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
    panel(d,'d','Stable decisions can remain incorrect')
    save_csv(SRC/'Figure1a.csv',data);save_csv(SRC/'Figure1b.csv',forest);save_csv(SRC/'Figure1c.csv',first);save_csv(SRC/'Figure1d.csv',stable)
    save(fig,'Figure1')

def main_fig2(probs,cal,ecells):
    fig,axs=subplots(1,3,85);a,b,c=axs.ravel()
    fig.subplots_adjust(left=.075,right=.985,bottom=.24,top=.82,wspace=.48)
    curve=risk_curve(probs,480);points=[selected_risk(probs,t,480) for t in [.5,.7,.8,.9]]
    a.step([r['coverage_valid']*100 for r in curve],[r['risk']*100 for r in curve],where='post',color=COLORS['jev'],lw=1.2)
    for i,r in enumerate(points):
        if r['risk'] is None:continue
        x,y=r['coverage_valid']*100,r['risk']*100;a.scatter(x,y,color=COLORS['jev'],s=13,zorder=4)
        a.annotate(f'τ = {r["threshold"]:.1f}',(x,y),xytext=[(6,8),(5,-14),(6,8),(7,-12)][i],textcoords='offset points',fontsize=6)
    a.set(xlim=(0,105),ylim=(0,32),xticks=[0,25,50,75,100],xlabel='Coverage of valid inputs (%)',ylabel='Disagreement risk (%)')
    a.text(.02,.97,'τ ≥ 0.9: 8 errors / 152 inputs',transform=a.transAxes,va='top',fontsize=6.5)
    panel(a,'a','Selective risk · exploratory')
    bins=cal['primary']['layers']['main_clear']['bins'];pop=[r for r in bins if r['count']]
    b.plot([0,1],[0,1],ls='--',color='#999999',lw=.7)
    b.scatter([r['mean_confidence'] for r in pop],[r['accuracy'] for r in pop],s=23,color=COLORS['jev'],zorder=3)
    for r in pop:b.annotate(str(r['count']),(r['mean_confidence'],r['accuracy']),xytext=(0,7),ha='center',textcoords='offset points',fontsize=6)
    b.set(xlim=(.25,1.02),ylim=(0,1.1),xticks=[.25,.5,.75,1],yticks=[0,.25,.5,.75,1],xlabel='Mean selected-label probability',ylabel='Observed source-label agreement')
    b.text(.02,.03,'Brier = 0.370; NLL = ∞\nNumbers indicate bin counts',transform=b.transAxes,fontsize=6.2,va='bottom')
    panel(b,'b','JEV probability reliability')
    select=match(ecells,concurrency=1);scatter=[]
    for role in ROLES:
        rs=match(select,role=role);x=[float(r['known_usd_per_1000_planned']) for r in rs];y=[float(r['valid_http_p50_ms'])/1000 for r in rs]
        c.scatter(x,y,s=14,facecolors='none' if role=='deepseek' else COLORS[role],edgecolors=COLORS[role],alpha=.5,marker=MARKERS[role])
        mx,my=np.median(x),np.median(y);c.scatter(mx,my,s=37,facecolors='white' if role=='deepseek' else COLORS[role],edgecolors=COLORS[role],marker=MARKERS[role],zorder=4)
        offsets={'jev':(5,-1),'luna':(-4,9),'gemini':(-8,-14),'deepseek':(-7,11)}
        c.annotate(NAMES[role]+('†' if role=='deepseek' else ''),(mx,my),xytext=offsets[role],textcoords='offset points',ha='right' if role!='jev' else 'left',fontsize=6.7)
        scatter.extend({**r,'http_p50_s':float(r['valid_http_p50_ms'])/1000} for r in rs)
    c.set(xlim=(0,.145),ylim=(.2,1.53),xticks=[0,.05,.10],xlabel='Known US$ per 1,000 requests',ylabel='HTTP latency p50 (s)')
    c.text(.02,.02,'Concurrency = 1; † upper estimate',transform=c.transAxes,fontsize=5.5,va='bottom')
    panel(c,'c','Single-decision service profile')
    fig.text(.075,.07,'First-round clear cases: 476 valid / 480 planned inputs; thresholds are descriptive, not deployment rules.',fontsize=6.7)
    fig.text(.075,.025,'Service costs: one JEV request has an unknown charge; plotted known costs do not imply that this charge was zero.',fontsize=6.2)
    save_csv(SRC/'Figure2a_curve.csv',curve);save_csv(SRC/'Figure2a_thresholds.csv',points);save_csv(SRC/'Figure2a_inputs.csv',probs)
    save_csv(SRC/'Figure2b.csv',bins);save_csv(SRC/'Figure2c.csv',scatter);save(fig,'Figure2')

def supplement1(counts,report):
    fig,axs=subplots(3,2,205);fig.subplots_adjust(left=.10,right=.98,top=.95,bottom=.09,wspace=.30,hspace=.60)
    cohorts=[('main_clear','Clear · 15 scenarios'),('main_edge','Ambiguous · 15 scenarios'),('main_full','All main · 30 scenarios'),('emergency','Emergency supplement · 4 scenarios'),('sensitivity_main_clear','Clear · flagged inputs excluded'),('sensitivity_main_full','All main · flagged inputs excluded')];data=[]
    for ax,(co,title),letter in zip(axs.ravel(),cohorts,'abcdef'):
        for role in ROLES:
            ys=[]
            for n in (1,2,3):
                if co=='main_edge':r=match(counts,round=n,role=role,cohort=co)[0];est=r['agreement_full_plan'];row=dict(r)
                else:
                    r=report['metrics'][str(n)][co][role];m=r['scenario_equal_full_plan'];est=m['estimate']
                    row={'round':n,'role':role,'cohort':co,'agreement_full_plan':est,'ci_lower':m['ci95'][0],'ci_upper':m['ci95'][1],'planned':r['planned_requests'],'scenarios':r['planned_scenarios']}
                data.append(row);ys.append(est*100)
            ax.plot([1,2,3],ys,color=COLORS[role],marker=MARKERS[role],ms=3,label=NAMES[role])
        ax.set(xticks=[1,2,3],xlabel='Round',ylabel='Agreement (%)',ylim=(50,102));panel(ax,letter,title)
        if co=='emergency':ax.text(.05,.84,'All four models: 100% in each round',transform=ax.transAxes,fontsize=6.5)
    model_legend(fig,.015);save_csv(SRC/'FigureS1.csv',data);save(fig,'FigureS1')

def supplement2(cases,scenario):
    ids=sorted({c['scenario_id'] for c in cases.values() if c['acceptable_labels']==['D']})
    labels={'s009':'Asthma exacerbation','s013':'Diabetic ketoacidosis','s036':'Acute ischemic stroke','s037':'Anaphylaxis','s038':'Bacterial meningitis','s039':'Aortic dissection'};data=[];matrix=[];annotations=[]
    for s in ids:
        rr=[];aa=[]
        for n in [1,2,3]:
            for role in ROLES:
                r=match(scenario,round=n,role=role,scenario_id=s)[0];d=int(r['explicit_d_valid']);miss=int(r['d_to_non_d_valid']);rr.append(miss/d*100 if d else np.nan);aa.append(f'{miss}/{d}');data.append({**r,'scenario_display_name':labels[s]})
        matrix.append(rr);annotations.append(aa)
    fig,axs=subplots(1,1,94);ax=axs[0,0];fig.subplots_adjust(left=.27,right=.91,bottom=.22,top=.82)
    im=ax.imshow(matrix,cmap='Oranges',vmin=0,vmax=100,aspect='auto')
    ylabels=[]
    for s in ids:
        c=next(c for c in cases.values() if c['scenario_id']==s);ylabels.append(labels[s]+(' (main)' if c['dataset_id']=='nm_main' else ' (supp.)'))
    ax.set(yticks=range(len(ids)),yticklabels=ylabels,xticks=range(12),xticklabels=[NAMES[r] for n in range(3) for r in ROLES]);ax.tick_params(axis='x',rotation=45)
    for i,row in enumerate(annotations):
        for j,txt in enumerate(row):ax.text(j,i,txt,ha='center',va='center',fontsize=6,color='white' if matrix[i][j]>65 else '#222222')
    for x in [3.5,7.5]:ax.axvline(x,color='white',lw=2)
    for x,n in [(1.5,1),(5.5,2),(9.5,3)]:ax.text(x,-.8,f'Round {n}',ha='center',fontsize=7)
    fig.colorbar(im,ax=ax,fraction=.035,pad=.02,label='D → non-D (%)')
    fig.text(.22,.96,'a',fontsize=8,fontweight='bold',va='top')
    fig.text(.27,.96,'Explicit emergency undertriage by base scenario',fontsize=7,va='top')
    fig.text(.24,.035,'Cells show undertriaged / valid D-labelled inputs (32 variants per scenario per round).',fontsize=6.3)
    save_csv(SRC/'FigureS2.csv',data);save(fig,'FigureS2')

def supplement3(factors):
    data=[r for r in factors if r['cohort']=='main' and r['factor']=='information_level']
    fig,axs=subplots(1,2,90);fig.subplots_adjust(left=.10,right=.97,bottom=.20,top=.86,wspace=.32)
    for ax,mask,letter,title in zip(axs.ravel(),['all','exclude_ai_flags'],'ab',['All complete pairs','Complete pairs after flag exclusion']):
        for i,role in enumerate(ROLES):
            rr=sorted(match(data,role=role,mask=mask),key=lambda r:int(r['round']))
            ys=[float(r['accuracy_difference'])*100 for r in rr]
            ax.plot([1,2,3],ys,color=COLORS[role],marker=MARKERS[role],ms=3)
        ax.axhline(0,color='#999999',lw=.7,ls='--');ax.set(xticks=[1,2,3],xlabel='Round',ylabel='Objective − subjective agreement (pp)',ylim=(-1,15));panel(ax,letter,title)
    model_legend(fig,.01);save_csv(SRC/'FigureS3.csv',data);save(fig,'FigureS3')

def supplement4(factors):
    groups=defaultdict(Counter)
    for r in factors:
        if r['cohort']!='main' or r['mask']!='all' or r['factor']=='information_level':continue
        for k in ['planned_pairs','valid_pairs','prediction_changed','lowered','correct_to_wrong']:groups[int(r['round']),r['role'],r['factor']][k]+=int(r[k])
    factor_names=[('anchor_type','Anchoring'),('barrier_type','Access barrier'),('gender','Gender wording'),('race','Explicit Black wording')]
    metrics=[('prediction_changed','Decision changed'),('lowered','Lower urgency'),('correct_to_wrong','Correct → incorrect')]
    fig,axs=subplots(3,3,166);fig.subplots_adjust(left=.18,right=.97,bottom=.16,top=.94,wspace=.20,hspace=.40);data=[]
    for i,n in enumerate([1,2,3]):
        for j,(metric,title) in enumerate(metrics):
            ax=axs[i,j];values=[]
            for factor,label in factor_names:
                row=[]
                for role in ROLES:
                    d=groups[n,role,factor];v=d[metric]/d['valid_pairs']*100;row.append(v)
                    data.append({'round':n,'role':role,'factor':factor,'metric':metric,'count':d[metric],'valid_pairs':d['valid_pairs'],'planned_pairs':d['planned_pairs'],'percent':v})
                values.append(row)
            ax.imshow(values,cmap='Blues',vmin=0,vmax=35,aspect='auto')
            for y,row in enumerate(values):
                for x,v in enumerate(row):ax.text(x,y,f'{v:.1f}',ha='center',va='center',fontsize=6.5,color='white' if v>23 else '#222222')
            ax.set(xticks=range(4),xticklabels=[NAMES[r] for r in ROLES] if i==2 else ['']*4,yticks=range(4),yticklabels=[label for f,label in factor_names] if j==0 else ['']*4)
            ax.tick_params(axis='x',rotation=45);panel(ax,chr(97+i*3+j),f'R{n} · {title}')
    fig.text(.18,.03,'Percent of valid single-factor pairs; each model/factor plans 480 pairs per round.\nCorrect → incorrect uses all valid pairs, not only initially correct answers.\nRace wording contrasts explicit Black with unspecified race; not a population fairness comparison.',fontsize=6.5)
    save_csv(SRC/'FigureS4.csv',data);save(fig,'FigureS4')

def supplement5(fmt):
    fig,axs=subplots(1,2,115);fig.subplots_adjust(left=.19,right=.97,bottom=.17,top=.90,wspace=.50);data=[]
    for ax,co,letter,title in zip(axs.ravel(),['main_clear','main_full'],'ab',['Clear · 30 reference inputs','All main · 60 reference inputs']):
        ticks=[]
        for i,(n,role) in enumerate((n,r) for r in ROLES[1:] for n in [1,2,3]):
            r=fmt['summary'][f'r{n}_{role}_{co}'];m=r['full_plan'];x=m['estimate']*100;lo,hi=np.array(m['ci95'])*100
            ax.errorbar(x,8-i,xerr=[[x-lo],[hi-x]],fmt=MARKERS[role],color=COLORS[role],capsize=2,ms=3)
            ticks.append(f'{NAMES[role]} · R{n}');data.append({'role':role,'baseline_round':n,'cohort':co,**r,'estimate_pp':x,'ci_lower_pp':lo,'ci_upper_pp':hi})
        ax.axvline(0,ls='--',color='#999999',lw=.7);ax.set(yticks=range(8,-1,-1),yticklabels=ticks,xlim=(-40,40),xlabel='Explanation − structured agreement (pp)');panel(ax,letter,title)
    fig.text(.19,.035,'One explanation sample compared with each of three baseline rounds; not three explanation replications.\n95% scenario-bootstrap intervals are descriptive; no multiplicity-adjusted superiority claim.',fontsize=6.3)
    save_csv(SRC/'FigureS5.csv',data);save(fig,'FigureS5')

def supplement6(natural):
    data=[];reasonrows=[]
    for role in ROLES[1:]:
        items=[natural['summary_by_source_role'][role][co] for co in ['main_full','emergency']]
        planned=sum(r['planned'] for r in items);valid=sum(r['consensus_valid'] for r in items);correct=sum(r['correct_valid'] for r in items)
        raw=Counter()
        for r in items:raw.update(r['reason_counts'])
        reasons=Counter()
        for reason,n in raw.items():
            reasonrows.append({'role':role,'reason':reason,'count':n})
            if reason=='consensus':continue
            if 'technical_failure' in reason:reasons['Coding contract failure']+=n
            elif reason=='unmapped':reasons['Unmapped']+=n
            elif reason=='disagreement':reasons['Coder disagreement']+=n
            else:raise ValueError(reason)
        assert sum(reasons.values())==planned-valid
        data.append({'role':role,'planned':planned,'consensus_valid':valid,'correct':correct,'incorrect':valid-correct,'unresolved':planned-valid,**dict(reasons)})
    assert sum(r['consensus_valid'] for r in data)==127
    fig,axs=subplots(1,2,95);a,b=axs.ravel();fig.subplots_adjust(left=.12,right=.98,top=.88,bottom=.30,wspace=.40)
    left=np.zeros(3)
    for key,label,color in [('correct','Consensus, acceptable','#8FB8BE'),('incorrect','Consensus, incorrect','#C77C65'),('unresolved','Unresolved','#CCCCCC')]:
        vals=[r[key]/68*100 for r in data];a.barh(range(3),vals,left=left,height=.6,color=color,label=label)
        for i,v in enumerate(vals):a.text(left[i]+v/2,i,str(data[i][key]),ha='center',va='center',fontsize=7)
        left+=vals
    a.set(yticks=range(3),yticklabels=[NAMES[r] for r in ROLES[1:]],xlim=(0,100),xlabel='Original answers (%)');a.invert_yaxis();a.legend(loc='upper left',bbox_to_anchor=(-.05,-.22),fontsize=6.3)
    panel(a,'a','Consensus coverage and scoring')
    left=np.zeros(3)
    for key,color in [('Coding contract failure','#747987'),('Unmapped','#C6B793'),('Coder disagreement','#C77C65')]:
        vals=[r.get(key,0) for r in data];b.barh(range(3),vals,left=left,color=color,label=key,height=.6);left+=vals
    b.set(yticks=range(3),yticklabels=[NAMES[r] for r in ROLES[1:]],xlabel='Unresolved original answers (count)',xlim=(0,35));b.invert_yaxis();b.legend(loc='upper left',bbox_to_anchor=(-.05,-.22),fontsize=6.3)
    panel(b,'b','Why original answers remain unresolved')
    save_csv(SRC/'FigureS6.csv',data);save_csv(SRC/'FigureS6_reasons.csv',reasonrows);save(fig,'FigureS6')

def supplement7(ecells):
    fig,axs=subplots(2,3,152);fig.subplots_adjust(left=.09,right=.975,bottom=.16,top=.94,wspace=.48,hspace=.58)
    fields=[('valid_http_p50_ms','HTTP p50 (ms)'),('valid_http_p95_ms','HTTP p95 (ms)'),('effective_outputs_per_second','Valid outputs / second'),('peak_http_inflight','Peak in-flight requests'),('admission_wait_p50_ms','Admission wait p50 (ms)'),('failure_percent','Technical failures (%)')]
    data=[]
    for r in ecells:data.append({**r,'failure_percent':int(r['failed'])/int(r['planned'])*100})
    for ax,(field,title),letter in zip(axs.ravel(),fields,'abcdef'):
        for role in ROLES:
            med=[];low=[];high=[]
            for level in [1,4,8,16,32]:
                rr=match(data,role=role,concurrency=level);vals=[float(r[field]) for r in rr]
                ax.scatter([level]*3,vals,s=8,color=COLORS[role],marker=MARKERS[role],alpha=.4)
                med.append(np.median(vals));low.append(min(vals));high.append(max(vals))
            ax.plot([1,4,8,16,32],med,color=COLORS[role],marker=MARKERS[role],ms=3)
            ax.fill_between([1,4,8,16,32],low,high,color=COLORS[role],alpha=.08)
        ax.set(xticks=[1,8,16,32],xlabel='Concurrency ceiling',ylabel=title);panel(ax,letter,title)
        if field=='effective_outputs_per_second':ax.axhline(1/.06,ls=':',color=COLORS['jev'],lw=.7);ax.text(.03,.9,'JEV admission ceiling ≈16.67/s',transform=ax.transAxes,fontsize=6)
    model_legend(fig,.025);fig.text(.09,.01,'Points: time blocks; lines: medians; shading: observed ranges, not confidence intervals. Warmups excluded.',fontsize=6.3)
    save_csv(SRC/'FigureS7.csv',data);save(fig,'FigureS7')

def supplement8(counts):
    path=ROOT/'reports/chatgpt_health_historical_v1/results.json'
    if not path.exists():return False
    hist=read_json(path)['cohorts']
    if any(hist[c]['agreement_full_plan'] is None for c in ['main_clear','main_full','main_edge']):
        raise ValueError('Historical reference is not fully matched; audit must be resolved before plotting.')
    fig,axs=subplots(2,2,143);fig.subplots_adjust(left=.15,right=.98,bottom=.17,top=.90,wspace=.40,hspace=.65);data=[]
    for ax,source,letter,title in [(axs[0,0],'historical','a','ChatGPT Health · January 2026'),(axs[0,1],'current','b','API models · September 2026')]:
        cs=['main_clear','main_full','main_edge'];roles=['health'] if source=='historical' else ROLES
        for i,role in enumerate(roles):
            ys=[]
            for co in cs:
                r=hist[co] if source=='historical' else match(counts,role=role,round=1,cohort=co)[0]
                ys.append(r['agreement_full_plan']*100);data.append({'source':source,'role':role,'cohort':co,**r})
            ax.scatter(np.arange(3)+(i-(len(roles)-1)/2)*.12,ys,color='#555555' if source=='historical' else COLORS[role],marker='o' if source=='historical' else MARKERS[role],s=22,label='Historical Health' if source=='historical' else NAMES[role])
        ax.set(xticks=range(3),xticklabels=['Clear','All main','Ambiguous'],ylabel='Source-label agreement (%)',ylim=(40,102));panel(ax,letter,title)
        if source=='current':ax.legend(loc='upper center',bbox_to_anchor=(.5,-.25),ncol=2,fontsize=6)
    categories=[('undertriage','Undertriage','#C77C65'),('correct','Agreement','#B9CDD0'),('overtriage','Overtriage','#C6B793'),('technical_failure','Unscorable','#444444')]
    for ax,source,letter,title in [(axs[1,0],'historical','c','Historical error direction'),(axs[1,1],'current','d','Current first-round error direction')]:
        rr=[hist['main_full']] if source=='historical' else [match(counts,role=r,round=1,cohort='main_full')[0] for r in ROLES]
        left=np.zeros(len(rr))
        for key,label,color in categories:
            vals=[r.get(key,r.get('invalid',0) if key=='technical_failure' else 0)/r['planned']*100 for r in rr];ax.barh(range(len(rr)),vals,left=left,color=color,label=label,height=.6);left+=vals
        ax.set(yticks=range(len(rr)),yticklabels=['Health'] if source=='historical' else [NAMES[r] for r in ROLES],xlim=(0,100),ylim=(-.5,3.5),xlabel='Planned inputs (%)');ax.invert_yaxis();panel(ax,letter,title)
    fig.legend(handles=[Patch(facecolor=color,label=label) for k,label,color in categories],loc='lower center',bbox_to_anchor=(.5,.04),ncol=4,fontsize=6.3)
    fig.text(.15,.01,'Historical product reference; not a contemporaneous fifth arm or an external clinical validation.',fontsize=6.3)
    save_csv(SRC/'FigureS8.csv',data);save(fig,'FigureS8');return True

def table1(counts,stable,ecells):
    data=[];lines=['# Table 1 | Benchmark and service characteristics','', '| Model | Clear agreement, n/N (%) | Three-round consistency, n/N (%) | Undertriage / valid main outputs | HTTP p50, s | Known US$ / 1,000 requests |','|---|---|---|---|---:|---:|']
    for role in ROLES:
        clear=match(counts,role=role,round=1,cohort='main_clear')[0];full=match(counts,role=role,round=1,cohort='main_full')[0];st=match(stable,role=role)[0];es=match(ecells,role=role,concurrency=1)
        latency=float(np.median([float(r['valid_http_p50_ms'])/1000 for r in es]));cost=float(np.median([float(r['known_usd_per_1000_planned']) for r in es]))
        row={'role':role,'clear_correct':clear['correct'],'clear_planned':480,'clear_agreement':clear['agreement_full_plan'],'stable_identical':st['identical'],'stable_valid':st['valid'],'stable_planned':960,'consistency':st['consistency_valid'],'undertriage':full['undertriage'],'main_valid':960-full['technical_failure'],'http_p50_s_median':latency,'known_usd_per_1000_requests_median':cost,'cost_type':'upper_estimate' if role=='deepseek' else 'provider_reported','efficiency_unknown_requests':sum(int(r['unknown_requests']) for r in es)};data.append(row)
        lines.append(f"| {NAMES[role]} | {clear['correct']}/480 ({clear['agreement_full_plan']*100:.2f}) | {st['identical']}/{st['valid']} ({st['consistency_valid']*100:.2f}) | {row['undertriage']}/{row['main_valid']} | {latency:.3f} | {cost:.6f}{'†' if role=='deepseek' else ''} |")
    lines+=['','Clear agreement: first round, 15 base scenarios and 480 planned inputs per model; failures remain in the denominator. Consistency: identical decisions across three valid rounds, among 960 planned main inputs. Undertriage: first round, 30 base scenarios, valid outputs only; failure counts are separately retained in Figure 1c. Service metrics: concurrency 1, medians of three complete time-block cells, 200 measured requests per cell; warmups excluded. HTTP latency excludes admission waiting. Cost includes known charges for failed requests; unknown charges are not zero. † DeepSeek uses a peak uncached upper estimate; other models use provider-reported charges. These are neither reconciled invoices nor clinical cost-effectiveness estimates.']
    lines+=['','Unknown charges in the concurrency-1 subset: JEV 1 request; other models 0. JEV cost values summarize known charges only, including in the affected block; the unknown amount is not imputed as zero.']
    save_csv(OUT/'Table1.csv',data);(OUT/'Table1.md').write_text('\n'.join(lines)+'\n')

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--only',nargs='*');args=ap.parse_args()
    OUT.mkdir(exist_ok=True);SRC.mkdir(exist_ok=True)
    protected=[p for d in ['freezes','runtime','analysis'] for p in (ROOT/d).rglob('*') if p.is_file() and '__pycache__' not in p.parts]+[ROOT/'configs/project_budget.json',ROOT/'reports/first_round_results.json']
    before={str(p.relative_to(ROOT)):sha(p) for p in protected}
    cases,results,response_digest=load_decisions();counts,stable,probs,stable_inputs=derive(cases,results)
    report=read_json(ROOT/'reports/three_round_v1/results.json');cal=read_json(ROOT/'reports/three_round_v1/calibration.json')
    factors=read_csv(ROOT/'reports/three_round_v1/factor_summary.csv');scenario=read_csv(ROOT/'reports/three_round_v1/scenario_metrics.csv');ecells=read_csv(ROOT/'reports/efficiency_v1/cells.csv')
    assert len(ecells)==60 and sum(int(r['planned']) for r in ecells)==12000
    for r in counts:
        assert sum(r[k] for k in ['correct','undertriage','overtriage','technical_failure'])==r['planned']
        if r['cohort']!='main_edge':assert abs(r['agreement_full_plan']-report['metrics'][str(r['round'])][r['cohort']][r['role']]['scenario_equal_full_plan']['estimate'])<1e-12
    save_csv(SRC/'stability_input_classification.csv',stable_inputs)
    calls={'Figure1':lambda:main_fig1(counts,stable,report),'Figure2':lambda:main_fig2(probs,cal,ecells),
           'FigureS1':lambda:supplement1(counts,report),'FigureS2':lambda:supplement2(cases,scenario),
           'FigureS3':lambda:supplement3(factors),'FigureS4':lambda:supplement4(factors),
           'FigureS5':lambda:supplement5(read_json(ROOT/'reports/format_explanation_v1/results.json')),
           'FigureS6':lambda:supplement6(read_json(ROOT/'reports/natural_coding_v1/results.json')),
           'FigureS7':lambda:supplement7(ecells),'FigureS8':lambda:supplement8(counts)}
    for name,fn in calls.items():
        if not args.only or name in args.only:fn();print(name,flush=True)
    table1(counts,stable,ecells)
    assert before=={str(p.relative_to(ROOT)):sha(p) for p in protected}
    (OUT/'render_checks.json').write_text(json.dumps(QA,indent=2)+'\n')
    inputs=[ROOT/p for p in ['reports/three_round_v1/results.json','reports/three_round_v1/calibration.json','reports/three_round_v1/factor_summary.csv','reports/three_round_v1/scenario_metrics.csv','reports/efficiency_v1/cells.csv','reports/format_explanation_v1/results.json','reports/natural_coding_v1/results.json']]
    if (ROOT/'reports/chatgpt_health_historical_v1/results.json').exists():inputs.append(ROOT/'reports/chatgpt_health_historical_v1/results.json')
    provenance={'analysis_timing':'post_collection','backend':'Python/Matplotlib','matplotlib_version':mpl.__version__,'protected_files_unchanged':True,'additional_api_calls':0,'response_hash_collection_digest':response_digest,'input_hashes':{str(p.relative_to(ROOT)):sha(p) for p in inputs},'software_hashes':{str(p.relative_to(ROOT)):sha(p) for p in [Path(__file__),ROOT/'scripts/manuscript_figure_data.py']},'source_data_hashes':{p.name:sha(p) for p in SRC.glob('*.csv')},'rendered_this_run':FIGURES}
    (OUT/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    print('Text outside canvas:',sum(len(q['text_outside_canvas']) for q in QA),flush=True)

if __name__=='__main__':main()
