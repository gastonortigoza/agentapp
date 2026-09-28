"""Piloto real: planner/tester independientes, developer, Docker y reviewer."""
import difflib
import json
from pathlib import Path
import time

import lab  # desactiva telemetría antes de construir el Flow
from crewai.flow.flow import Flow, start
from pydantic import BaseModel
from agent_runtime import run_role
from local_control import LocalStore, sandbox, require_evidence
from local_pilot import TESTS, digest, validate_candidate
import executor
import manifest
from worker_lock import worker_lock


class LocalState(BaseModel):
    run_id: str = ''
    database: str = ''
    until: str = 'delivered'


def materialize(store,run_id):
    code=validate_candidate(store.output(run_id,'developer')['text'])
    row=store.get(run_id);contract=json.loads(row['manifest'])
    from local_pilot import pilot_spec
    pilot_tests=pilot_spec(contract)[1]
    path=sandbox(run_id)
    if not manifest.can_write(contract,path,'solution.py'):
        raise ValueError('Escritura de candidato denegada')
    op=store.prepare_local(run_id,'materialize')
    if not op:return
    store.dispatch_local(op)
    started=time.monotonic()
    try:
        path.mkdir(exist_ok=False)
        (path/'solution.py').write_text(code,encoding='utf-8',newline='\n')
        (path/'test_solution.py').write_text(pilot_tests,encoding='utf-8',newline='\n')
        store.finish_local(op,{'status':'complete','elapsed_ms':round((time.monotonic()-started)*1000),'input_tokens':0,'output_tokens':0},
            {'code':code,'code_sha256':digest(code),'tests_sha256':digest(pilot_tests),'workspace':str(path)})
    except OSError:
        store.finish_local(op,{'status':'failed','elapsed_ms':round((time.monotonic()-started)*1000),'input_tokens':0,'output_tokens':0},uncertain=True)
        raise


def validate_in_docker(store,run_id):
    row=store.get(run_id);contract=json.loads(row['manifest'])
    with store.transaction() as db:require_evidence(db,row,'validating')
    candidate=store.output(run_id,'materialize')
    op=store.prepare_local(run_id,'tests')
    if not op:return
    store.dispatch_local(op)
    started=time.monotonic()
    result=executor.run_tests(sandbox(run_id),timeout=contract['commands']['unit']['timeout_seconds'])
    elapsed=round((time.monotonic()-started)*1000)
    with store.transaction() as db:require_evidence(db,store._row(db,run_id),'validating')
    body={**result,'code_sha256':candidate['code_sha256'],'tests_sha256':candidate['tests_sha256'],'image':executor.IMAGE}
    store.finish_local(op,{'status':result['status'],'elapsed_ms':elapsed,'input_tokens':0,'output_tokens':0},body)


class ControlledLocalFlow(Flow[LocalState]):
    @start()
    def execute(self):
        with worker_lock(self.state.database, self.state.run_id) as acquired:
            if not acquired:
                row=LocalStore(self.state.database).get(self.state.run_id)
                return {'run_id':row['id'],'state':row['state'],'scope':'local_agents_docker',
                        'busy':True,'message':'Otro proceso está ejecutando este run'}
            return self.execute_owned()

    def execute_owned(self):
        store=LocalStore(self.state.database)
        try:
            while True:
                row=store.get(self.state.run_id)
                state=row['state']
                if row['execution_kind']!='local':raise ValueError('No es un run local real')
                if state in {'implementing','delivered'}:
                    from integration_linear_state import sync_run_sync
                    observations=sync_run_sync(store,row['id'])
                    from integration_closeout import progress_sync
                    progress_sync(store,row['id'],observations)
                if state==self.state.until or state in {'delivered','rejected','paused','blocked_budget','blocked_evidence','uncertain_operation','awaiting_human_approval'}:
                    return {'run_id':row['id'],'state':state,'scope':'local_agents_docker'}
                if state=='received':target='contract_pending'
                elif state=='contract_pending':target='planning'
                elif state=='planning':
                    for role in ('planner','tester'):
                        if not store.output(row['id'],role):run_role(store,row['id'],role)
                        if store.get(row['id'])['state']!='planning':break
                    else:
                        from integration_linear import dispatch_planner_sync
                        dispatch_planner_sync(store, row['id'])
                        store.transition(row['id'],store.get(row['id'])['version'],'implementing')
                    continue
                elif state=='implementing':
                    if not store.output(row['id'],'developer'):run_role(store,row['id'],'developer')
                    if store.get(row['id'])['state']!='implementing':continue
                    if not store.output(row['id'],'materialize'):materialize(store,row['id'])
                    if store.get(row['id'])['state']!='implementing':continue
                    target='validating'
                elif state=='validating':
                    if not store.output(row['id'],'tests'):validate_in_docker(store,row['id'])
                    if store.get(row['id'])['state']!='validating':continue
                    target='awaiting_review'
                elif state=='awaiting_review':
                    if not store.output(row['id'],'reviewer'):run_role(store,row['id'],'reviewer')
                    if store.get(row['id'])['state']!='awaiting_review':continue
                    review=store.output(row['id'],'reviewer')
                    if review.get('approved') is not True:
                        store.block(row['id'],'reviewer_rejected');continue
                    target='delivered'
                else:raise ValueError('Estado sin avance permitido')
                current=store.get(row['id'])
                store.transition(row['id'],current['version'],target)
        except Exception as exc:
            current=store.get(self.state.run_id)
            if current['state'] not in {'delivered','rejected','paused','blocked_budget','blocked_evidence','uncertain_operation'}:
                store.recover(current['id'])
                if store.get(current['id'])['state']!='uncertain_operation':
                    store.block(current['id'],type(exc).__name__)
            return {'run_id':current['id'],'state':store.get(current['id'])['state'],
                    'scope':'local_agents_docker','error_type':type(exc).__name__,
                    'integration_pending':current['state']=='delivered'}


def export_run(store,run_id):
    row=store.get(run_id)
    destination=Path(row['workspace'])/'.state/deliveries'/run_id
    destination.mkdir(parents=True,exist_ok=True)
    metrics=store.metrics(run_id)
    (destination/'metrics.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding='utf-8')
    evidence={name:store.output(run_id,name) for name in ('planner','tester','developer','materialize','tests','reviewer')}
    (destination/'evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    events=store.events(run_id)
    (destination/'events.json').write_text(json.dumps(events,ensure_ascii=False,indent=2),encoding='utf-8')
    (destination/'contract.json').write_text(json.dumps(json.loads(row['manifest']),ensure_ascii=False,indent=2),encoding='utf-8')
    checkpoint={'run_id':run_id,'manifest_hash':row['manifest_hash'],'fingerprint':json.loads(row['fingerprint']),
                'suite_identity':json.loads(events[0]['refs']).get('suite_identity')}
    (destination/'checkpoint.json').write_text(json.dumps(checkpoint,ensure_ascii=False,indent=2),encoding='utf-8')
    if row['state']=='delivered':
        with store.transaction() as db:require_evidence(db,row,'delivered')
        code=evidence['materialize']['code']
        from local_pilot import pilot_spec
        module=pilot_spec(json.loads(row['manifest']))[2]+'.py'
        (destination/module).write_text(code,encoding='utf-8',newline='\n')
        patch=''.join(difflib.unified_diff([],code.splitlines(keepends=True),fromfile='/dev/null',tofile=module))
        (destination/'proposal.diff').write_text(patch,encoding='utf-8')
    return destination
