"""Adaptador nativo de Ollama y agentes CrewAI con configuración editable."""
import os
from pathlib import Path
ROOT = Path(__file__).resolve().parent
os.environ['OTEL_SDK_DISABLED'] = 'true'
os.environ['CREWAI_TRACING_ENABLED'] = 'false'
os.environ['CREWAI_TELEMETRY_DISABLED'] = 'true'
os.environ['CREWAI_STORAGE_DIR'] = str(ROOT / '.state')
os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
os.environ['NO_PROXY'] = '127.0.0.1,localhost'

import json
import hashlib
import time
import uuid
import urllib.request
from datetime import datetime, timezone
import yaml
from crewai import Agent, Task, Crew, Process
from crewai.llms.base_llm import BaseLLM

CONFIG = yaml.safe_load((ROOT/'config/agents.yaml').read_text(encoding='utf-8'))
LOG_DIR = ROOT.parents[1]/'logs'/'agents'
LOG_DIR.mkdir(parents=True,exist_ok=True)

def event(run_id,kind,**data):
    record={'timestamp':datetime.now(timezone.utc).isoformat(),'run_id':run_id,'kind':kind,'backend':'Ollama local / ROCm observado','round':None,'task':None,'tool':None,**data}
    with (LOG_DIR/f'{run_id}.jsonl').open('a',encoding='utf-8') as f:
        f.write(json.dumps(record,ensure_ascii=False)+'\n')

class LocalOllama(BaseLLM):
    """Usa /api/chat para controlar think/num_ctx sin traducción de parámetros.

    Los agentes no reciben herramientas de ejecución; las ejecuta el Flow.
    Nunca acepta un destino remoto ni cae a un proveedor externo.
    """
    llm_type: str = 'local_ollama'
    provider: str = 'ollama'
    think: bool = False
    context: int = 32768
    run_id: str = 'smoke'
    role_name: str = 'local'
    output_schema: dict | None = None

    def supports_function_calling(self): return False
    def supports_stop_words(self): return False
    def get_context_window_size(self): return self.context

    def call(self,messages,tools=None,callbacks=None,available_functions=None,from_task=None,from_agent=None,response_model=None):
        if tools or available_functions:
            raise ValueError('La ejecución de herramientas pertenece al Flow')
        if isinstance(messages,str): messages=[{'role':'user','content':messages}]
        payload={'model':self.model,'messages':messages,'stream':False,'think':self.think,
                 'keep_alive':'10m','options':{'num_ctx':self.context,'num_predict':4096,'temperature':0,'seed':42}}
        if response_model is not None: payload['format']=response_model.model_json_schema()
        elif self.output_schema is not None: payload['format']=self.output_schema
        started=time.monotonic()
        req=urllib.request.Request('http://127.0.0.1:11434/api/chat',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=240) as r:
                result=json.load(r)
            if not result.get('done') or result.get('done_reason')=='length':
                raise RuntimeError('Generación incompleta: no se acepta como resultado')
            answer=result['message']['content']
            if not answer.strip(): raise RuntimeError('Respuesta vacía')
            event(self.run_id,'llm',agent=self.role_name,model=self.model,context=self.context,thinking=self.think,
                  duration_ms=round((time.monotonic()-started)*1000),
                  eval_count=result.get('eval_count'),eval_duration=result.get('eval_duration'),
                  prompt_eval_count=result.get('prompt_eval_count'),prompt_eval_duration=result.get('prompt_eval_duration'),
                  load_duration=result.get('load_duration'),total_duration=result.get('total_duration'),status='complete')
            return response_model.model_validate_json(answer) if response_model else answer
        except Exception as exc:
            event(self.run_id,'llm_error',agent=self.role_name,error_type=type(exc).__name__,duration_ms=round((time.monotonic()-started)*1000))
            raise

def run_agent(role,prompt,expected,run_id,schema=None):
    event(run_id,'agent_start',agent=role,task=hashlib.sha256(prompt.encode('utf-8')).hexdigest())
    cfg=CONFIG['agents'][role]
    agent=Agent(role=cfg['role'],goal=cfg['goal'],backstory=cfg['backstory'],
                llm=LocalOllama(model=CONFIG['model'],think=cfg['think'],context=CONFIG['context'],run_id=run_id,role_name=role,output_schema=schema),
                max_iter=CONFIG['max_iter'],max_execution_time=CONFIG['max_execution_time'],
                allow_delegation=False,reasoning=False,verbose=False,tools=[],max_retry_limit=0)
    task=Task(description=prompt,expected_output=expected,agent=agent)
    return Crew(agents=[agent],tasks=[task],process=Process.sequential,planning=False,
                memory=False,verbose=False,cache=False).kickoff().raw

if __name__=='__main__':
    print(run_agent('developer','Respondé exactamente CREWAI_OK. No escribas código.','CREWAI_OK',str(uuid.uuid4())))
