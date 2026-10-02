"""Optional projection into existing Studio storage; no decision/control authority."""
from phase3_review import now

def configured_observers():
    """Use the installed local Studio store when explicitly configured/present."""
    import importlib.util
    import os
    from pathlib import Path
    path=Path(os.environ.get('CREW_AI_STUDIO_STORE','E:/IA/tools/crew-ai-studio/server/store.py'))
    if not path.is_file():return {}
    spec=importlib.util.spec_from_file_location('phase3_configured_studio',path)
    store=importlib.util.module_from_spec(spec);spec.loader.exec_module(store)
    return {'observer':WorkflowObserver(store),'review_observer':StudioObserver(store),'execution_observer':ExecutionObserver(store)}

class WorkflowObserver:
    """Project parent task transitions; children retain their own original usage."""
    def __init__(self,store):self.store=store

    def __call__(self,row):
        rec=self.store.get_run(row['id']);identity=row['binding_sha256']
        if rec and rec.get('inputs',{}).get('binding_sha256')!=identity:raise ValueError('Studio workflow identity drift')
        if not rec:
            rec={'id':row['id'],'workspace_id':'','spec_name':'AGE-32/50 · coordinación autónoma local',
                'started_at':row['created_at'],'finished_at':None,'dry_run':False,'trigger':'external:phase3_workflow',
                'tokens':0,'cost':None,'status':'observando','hitl':None,'error':None,
                'inputs':{'scope':row['scope'],'binding_sha256':identity},'result':None}
            self.store.create_run(rec)
        rec['status']='succeeded' if row['state']=='completed_slice' else 'awaiting_review' if row['state']=='awaiting_flow_audit' else 'observando' if row['state']=='active' else 'failed'
        rec['finished_at']=row['updated_at'] if row['state']!='active' else None
        rec['result']='Estado: '+row['state']+'. Ronda de producto: '+str(row['round'])+'. '+row['reason']+' Autonomía del corte local; aceptación del SaaS completo pendiente. Consumo original en los hijos; padre sin tokens duplicados.'
        self.store.update_run(rec)
        events=self.store.get_events(row['id'],0);known={e.get('evidence_key') for e in events};seq=max((e['seq'] for e in events),default=-1)+1
        for event in row['events']:
            key='phase3-workflow-'+str(event['seq'])
            if key in known:continue
            self.store.append_event(row['id'],{'seq':seq,'ts':event['at'],'kind':event['kind'],'evidence_key':key,
                'agent':None,'task':event.get('child_id') or event.get('reason') or event.get('stage') or event['kind'],'ms':None});seq+=1


class PreflightObserver:
    """Zero model calls here; do not count referenced review usage twice."""
    def __init__(self,store):self.store=store

    def __call__(self,result):
        rec=self.store.get_run(result['id'])
        identity=result['binding_sha256']
        if rec:
            if rec.get('inputs',{}).get('binding_sha256')!=identity:
                raise ValueError('Studio preflight identity drift')
            return
        at=now()
        rec={'id':result['id'],'workspace_id':'','spec_name':'AGE-32/50 · control previo del ejecutor',
             'started_at':at,'finished_at':at,'dry_run':False,'trigger':'external:execution_preflight',
             'tokens':0,'cost':None,'status':'failed','hitl':None,'error':None,
             'inputs':{'scope':'execution_preflight','binding_sha256':identity,'reviews':result['binding']['reviews']},
             'result':'Estado: '+result['state']+'. '+result['reason']+' Cero llamadas de modelo y cero comandos de producto en este preflight.'}
        self.store.create_run(rec)
        self.store.append_event(result['id'],{'seq':0,'ts':at,'kind':'run.blocked',
            'evidence_key':'phase3-execution-preflight','task':rec['result'],'agent':None,'ms':0})


class ExecutionObserver:
    """Faithful dispatcher evidence; no duplicate model-call/token attribution."""
    def __init__(self,store):self.store=store

    def __call__(self,row):
        rec=self.store.get_run(row['id']);identity=row['binding_sha256'];at=now()
        if rec and rec.get('inputs',{}).get('binding_sha256')!=identity:raise ValueError('Studio execution identity drift')
        if not rec:
            rec={'id':row['id'],'workspace_id':'','spec_name':'AGE-32/50 · ejecución local del directorio',
                 'started_at':at,'finished_at':None,'dry_run':False,'trigger':'external:isolated_directory_slice',
                 'tokens':0,'cost':None,'status':'observando','hitl':None,'error':None,
                 'inputs':{'scope':'isolated_directory_slice','binding_sha256':identity},'result':None}
            self.store.create_run(rec)
        terminal=row['state']!='active';rec['status']='succeeded' if row['state']=='completed' else 'failed' if terminal else 'observando'
        rec['finished_at']=at if terminal else None
        rec['result']='Estado: '+row['state']+'. Etapas: '+', '.join(s['name'] for s in row['stages'])+'. '+row['reason']+' Cero inferencias en este ejecutor; pruebas limitadas al directorio sintético.'
        self.store.update_run(rec)
        events=self.store.get_events(row['id'],0);known={e.get('evidence_key') for e in events};seq=max((e['seq'] for e in events),default=-1)+1
        for stage in row['stages']:
            key='phase3-execution-stage-'+stage['name']
            if key in known:continue
            self.store.append_event(row['id'],{'seq':seq,'ts':at,'kind':'tool.execution.completed','evidence_key':key,
                'agent':None,'task':stage['name']+' · código '+str(stage['exit_code'])+' · pruebas '+str(stage.get('tests','no aplica')),
                'ms':stage['duration_ms']});seq+=1


class StudioObserver:
    def __init__(self,store):self.store=store

    def __call__(self,journal,row):
        rec=self.store.get_run(row['id'])
        identity=row['binding_sha256']
        if rec and rec.get('inputs',{}).get('binding_sha256')!=identity:
            raise ValueError('Studio run ID belongs to another identity')
        if not rec:
            label=('archivo fuente '+row['binding']['section'] if row['scope']=='application_file_review' else ('evaluación de fixture ' if row['scope']=='contract_fixture_evaluation' else 'sección contractual ')+row['binding']['section'] if row['binding'].get('section') else 'estructura de plan')
            rec={'id':row['id'],'workspace_id':'','spec_name':'AGE-32/50 · '+label,
                 'started_at':row['created_at'],'finished_at':None,'dry_run':False,
                 'trigger':'external:'+row['scope'],'tokens':0,'cost':None,
                 'inputs':{'scope':row['scope'],'binding_sha256':identity},
                 'hitl':None,'result':None,'error':None,'status':'observando'}
            self.store.create_run(rec)
        terminal=row['state']!='active'
        rec['status']='succeeded' if row['state'] in {'reviewed_plan','reviewed_contract_section','reviewed_application_file'} or (row['state']=='evaluated_fixture' and row.get('fixture_passed')) else 'failed' if terminal else 'observando'
        rec['finished_at']=row['updated_at'] if terminal else None
        ops=journal.records(row['id'],'plan_ops')
        rec['tokens']=sum((op.get('result',{}).get('input_tokens') or 0)+(op.get('result',{}).get('output_tokens') or 0)
                          for op in ops if op['state']=='confirmed'
                          and type(op.get('result',{}).get('input_tokens')) is int
                          and type(op.get('result',{}).get('output_tokens')) is int)
        label=('archivo fuente '+row['binding']['section'] if row['scope']=='application_file_review' else ('evaluación de fixture ' if row['scope']=='contract_fixture_evaluation' else 'sección contractual ')+row['binding']['section'] if row['binding'].get('section') else 'estructura de plan')
        rec['result']=(f"Estado: {row['state']}. {row['calls']} llamadas, {row['corrections']} correcciones. "
                       'Sólo '+label+'. Producto y comandos no ejecutados. '+row['reason']) if terminal else None
        self.store.update_run(rec)
        existing=self.store.get_events(row['id'],0)
        known={e.get('evidence_key') for e in existing}
        seq=max((e['seq'] for e in existing),default=-1)+1
        for event in journal.records(row['id'],'plan_events'):
            key='phase3-plan-'+str(event['seq'])
            if key in known:continue
            kind={'agent.started':'agent.execution.started','agent.finished':'agent.execution.completed'}.get(event['kind'],event['kind'])
            if event['state']!='active' and event['kind']!='observer.failed':kind='hitl.status.changed'
            payload={'seq':seq,'ts':event['at'],'kind':kind,'evidence_key':key,
                     'agent':event.get('agent'),'task':f"{event['state']} · llamada {event['calls']} · corrección {event['corrections']} · {label}",
                     'ms':event.get('elapsed_ms')}
            self.store.append_event(row['id'],payload);seq+=1
