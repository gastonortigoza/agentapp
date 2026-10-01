"""Optional projection into existing Studio storage; no decision/control authority."""
from phase3_review import now


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


class StudioObserver:
    def __init__(self,store):self.store=store

    def __call__(self,journal,row):
        rec=self.store.get_run(row['id'])
        identity=row['binding_sha256']
        if rec and rec.get('inputs',{}).get('binding_sha256')!=identity:
            raise ValueError('Studio run ID belongs to another identity')
        if not rec:
            label=(('evaluación de fixture ' if row['scope']=='contract_fixture_evaluation' else 'sección contractual ')+row['binding']['section'] if row['binding'].get('section') else 'estructura de plan')
            rec={'id':row['id'],'workspace_id':'','spec_name':'AGE-32/50 · '+label,
                 'started_at':row['created_at'],'finished_at':None,'dry_run':False,
                 'trigger':'external:'+row['scope'],'tokens':0,'cost':None,
                 'inputs':{'scope':row['scope'],'binding_sha256':identity},
                 'hitl':None,'result':None,'error':None,'status':'observando'}
            self.store.create_run(rec)
        terminal=row['state']!='active'
        rec['status']='succeeded' if row['state'] in {'reviewed_plan','reviewed_contract_section'} or (row['state']=='evaluated_fixture' and row.get('fixture_passed')) else 'failed' if terminal else 'observando'
        rec['finished_at']=row['updated_at'] if terminal else None
        ops=journal.records(row['id'],'plan_ops')
        rec['tokens']=sum((op.get('result',{}).get('input_tokens') or 0)+(op.get('result',{}).get('output_tokens') or 0)
                          for op in ops if op['state']=='confirmed'
                          and type(op.get('result',{}).get('input_tokens')) is int
                          and type(op.get('result',{}).get('output_tokens')) is int)
        label=(('evaluación de fixture ' if row['scope']=='contract_fixture_evaluation' else 'sección contractual ')+row['binding']['section'] if row['binding'].get('section') else 'estructura de plan')
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
