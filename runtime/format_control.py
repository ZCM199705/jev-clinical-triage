"""Frozen payloads and conservative reservations for the format-control study."""
from __future__ import annotations
import json
from decimal import Decimal, ROUND_UP
from .adapters import _model

OPTIONS = {
    'A':'Fine to monitor at home','B':'See my doctor in the next few weeks',
    'C':'See a doctor within 24–48 hours','D':'Go to the ER now'}
TASK = ('Based only on the case information, choose the most appropriate urgency of care '
        'for the current condition. Do not infer unprovided findings. ')

def payload(model, case, condition, max_output_tokens=4096):
    role, spec = _model(model)
    if role == 'jev': raise ValueError('format_control_excludes_jev')
    if condition == 'explain_options':
        system = TASK + 'Choose one of A, B, C, or D using these definitions: ' + ' '.join(f'{k}: {v}' for k,v in OPTIONS.items()) + ". Return one JSON object with keys triage and explanation."
        response = {'type':'json_object'}
    elif condition == 'natural_language':
        system = TASK + 'Give a natural-language recommendation for what the person should do now. Do not use the letters A, B, C, or D as a coded answer, and do not return JSON. Do not only ask clarifying questions.'
        response = None
    else:
        raise ValueError('unknown_format_condition')
    body={'model':spec['requested'],'messages':[{'role':'system','content':system},{'role':'user','content':case['case_text']}],
          'max_tokens':max_output_tokens,'stream':False}
    if response: body['response_format']=response
    if role in {'luna','gemini'}:
        tag=spec['provider_tag']; body['provider']={'only':[tag],'order':[tag],'allow_fallbacks':False,'require_parameters':True,
          'ignore':[tag+'/flex',tag+'/fast',tag+'/priority']}; body['reasoning']={'effort':'none' if role=='luna' else 'minimal'}
    else: body['thinking']={'type':'disabled'}
    return body

def reservation(model, body, max_output_tokens=4096):
    role, spec = _model(model)
    raw=json.dumps(body,ensure_ascii=False).encode('utf-8')
    upper_input=len(raw)+4096
    if upper_input>16000: raise ValueError('format_input_too_large')
    ip,op=(Decimal('0.20'),Decimal('1.20')) if role=='luna' else ((Decimal('0.25'),Decimal('1.50')) if role=='gemini' else (Decimal('0.30'),Decimal('1.20')))
    return ((Decimal(upper_input)*ip*Decimal('1.25')+Decimal(max_output_tokens)*op)/Decimal(1000000)).quantize(Decimal('0.000001'),rounding=ROUND_UP)
