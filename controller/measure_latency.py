"""20 ejecuciones completas del pipeline; fallos incluidos y percentiles sin inventar."""
import argparse
import json
import math
import time
from pathlib import Path
from osint_flow import run

QUESTIONS=[
 '¿Qué distingue vishing de smishing?',
 '¿Qué señales de urgencia aparecen en fraudes por mensajería?',
 '¿Por qué OTP manual no equivale a resistencia al phishing?',
 '¿Qué límite tiene basarse en varias páginas del mismo editor?',
 '¿Cómo verificar una solicitud sensible por un canal conocido?']

def percentile(values,p):
    a=sorted(values);x=(len(a)-1)*p;lo=math.floor(x);hi=math.ceil(x)
    return a[lo]+(a[hi]-a[lo])*(x-lo)

def measure(n=20):
    records=[]; target=Path('E:/IA/logs/osint-latency.json')
    for i in range(n):
        q=QUESTIONS[i%len(QUESTIONS)]+' Limitá la investigación a esta pregunta y a una afirmación central. No amplíes el alcance.'
        result=run(q)
        records.append({k:result.get(k) for k in ['run_id','objective','status','error','total_latency_seconds','generation_seconds','prompt_eval_seconds','model_load_seconds','network_time_seconds','tool_time_seconds','retries']})
        summary={'requested_runs':n,'completed_runs':len(records),'pass_count':sum(r['status']=='pass' for r in records),'scope':'Pipeline completo de cinco roles, cinco preguntas repetidas cuatro veces, fuentes cacheadas. No mide búsqueda web abierta ni red en vivo.','runs':records}
        for metric in ['total_latency_seconds','generation_seconds','prompt_eval_seconds']:
            values=[r[metric] for r in records if r.get(metric) is not None]
            summary[metric]={f'p{p}':percentile(values,p/100) for p in [50,90,95]}
        target.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'completed':len(records),'status':result['status'],'seconds':result['total_latency_seconds']}),flush=True)
        # Tres fallos consecutivos indican que hay que corregir, no seguir gastando cómputo.
        if len(records)>=3 and all(r['status']=='fail' for r in records[-3:]): break
    return summary

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--runs',type=int,default=20);a=p.parse_args()
    if not 1<=a.runs<=30:raise SystemExit('runs debe estar entre 1 y 30')
    print(json.dumps(measure(a.runs),indent=2))
