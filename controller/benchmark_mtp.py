"""Comparación corta A/B; no cambia el modelo predeterminado."""
import json
import statistics
import subprocess
import time
import urllib.request
from pathlib import Path
from rag import api

MODELS=['qwen3.8:27b-q4_K_M','qwen3.8:27b-mtp-q4_K_M']
TASKS=[('math','Respondé sólo el resultado de 17*23.','391'),('json','Devolvé sólo JSON con la lista [9,2,7,2] ordenada sin duplicados.','[2,7,9]'),('grounding','Contexto: El servidor se reinicia a las 03:15 UTC. Pregunta: ¿A qué hora se reinicia? Respondé sólo HH:MM UTC.','03:15 UTC')]

def sample(model,label,prompt,expected,iteration):
    body={'model':model,'messages':[{'role':'user','content':prompt}],'think':False,'stream':True,'keep_alive':'10m','options':{'num_ctx':32768,'num_predict':256,'temperature':0,'seed':42}}
    start=time.monotonic();first=None;answer='';final={}
    req=urllib.request.Request('http://127.0.0.1:11434/api/chat',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=300) as response:
        for line in response:
            r=json.loads(line)
            if r.get('error'): raise RuntimeError(r['error'])
            token=r.get('message',{}).get('content','')
            if token and first is None:first=time.monotonic()-start
            answer+=token
            if r.get('done'): final=r
    seconds=time.monotonic()-start
    normalized=answer.strip().replace(' ','')
    if label=='json':
        try:
            parsed=json.loads(answer)
            correct=parsed==[2,7,9] or parsed=={'lista':[2,7,9]}
        except ValueError: correct=False
    else: correct=normalized==expected.replace(' ','')
    with urllib.request.urlopen('http://127.0.0.1:11434/api/ps') as response: residency=json.load(response)
    ram=subprocess.run(['powershell','-NoProfile','-Command',"(Get-Process -Name 'ollama*' -ErrorAction SilentlyContinue | Measure-Object WorkingSet64 -Sum).Sum"],capture_output=True,text=True,timeout=15)
    return {'model':model,'task':label,'iteration':iteration,'answer':answer,'correct':correct,'complete':bool(final) and final.get('done_reason')!='length','ttft_seconds':first,'total_seconds':seconds,'tokens_per_second':final.get('eval_count',0)/(final.get('eval_duration',1)/1e9),'load_seconds':final.get('load_duration',0)/1e9,'residency':residency,'ollama_working_set_bytes_after':int(ram.stdout.strip() or 0)}

def run():
    rows=[]
    for model in MODELS:
        # Una muestra fría y nueve calientes por modelo; se guardan separadas.
        for iteration in range(3):
            for label,prompt,expected in TASKS:
                try: row=sample(model,label,prompt,expected,iteration)
                except Exception as exc: row={'model':model,'error':str(exc),'correct':False}
                rows.append(row)
                Path('E:/IA/logs/mtp-benchmark.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
        api('/api/generate',{'model':model,'keep_alive':0})
    summary={}
    for model in MODELS:
        subset=[r for r in rows if r['model']==model]
        warm=[r for r in subset[1:] if 'error' not in r]
        summary[model]={'samples':len(subset),'correct':sum(r['correct'] for r in subset),'warm_ttft_median':statistics.median(r['ttft_seconds'] for r in warm) if warm else None,'warm_tokens_per_second_median':statistics.median(r['tokens_per_second'] for r in warm) if warm else None}
    result={'results':summary,'decision':'Se conserva baseline. Este piloto de respuestas cortas no justifica cambiar el modelo de agentes.','limitation':'18 muestras cortas, sin código largo ni visión. RAM/VRAM después de respuesta, no pico.'}
    Path('E:/IA/logs/mtp-summary.json').write_text(json.dumps(result,indent=2)); print(json.dumps(result))

if __name__=='__main__':run()
