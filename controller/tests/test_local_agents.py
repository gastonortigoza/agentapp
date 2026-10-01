import json
from pathlib import Path
import subprocess
import pytest

import controller_gate
import executor
import manifest
from local_control import LocalStore, sandbox
from local_flow import ControlledLocalFlow
from local_pilot import validate_candidate,TESTS,digest
import agent_runtime

ROOT=Path(__file__).resolve().parents[1]
GOOD='''def summarize_runs(records):
    if not isinstance(records, list):
        raise TypeError("records")
    counts = {}
    for record in records:
        if not isinstance(record, dict):
            raise TypeError("record")
        status = record.get("recorded_status")
        status = status.strip() if isinstance(status, str) else "unknown"
        if not status:
            status = "unknown"
        counts[status] = counts.get(status, 0) + 1
    return {"total": len(records), "by_status": dict(sorted(counts.items()))}
'''


@pytest.fixture
def local(tmp_path,monkeypatch):
    monkeypatch.setattr(controller_gate,'require_green',lambda:None)
    monkeypatch.setattr(manifest,'verify_base',lambda *args:None)
    work=tmp_path/'sandbox';work.mkdir()
    monkeypatch.setattr(executor,'WORKSPACE',work)
    contract=json.loads((ROOT/'config/local-pilot.json').read_text(encoding='utf-8'))
    store=LocalStore(tmp_path/'db.sqlite')
    row=store.create(contract,tmp_path,[])
    monkeypatch.setattr(executor,'run_tests',lambda *a,**k:{'status':'pass','exit_code':0,'timed_out':False,'stdout':'','stderr':'12 fixture tests','duration_ms':1})
    answers=[json.dumps({'steps':['implementar','validar']}),json.dumps({'cases':['vacío','tipos inválidos']}),GOOD,json.dumps({'approved':True,'findings':[]})]
    calls=[]
    def transport(payload,timeout):
        calls.append(payload)
        return {'ok':True,'text':answers[len(calls)-1],'done':True,'done_reason':'stop','tool_calls':False,
                'input_tokens':100,'output_tokens':50,'model_digest':contract['roles']['developer']['resolved_digest']}
    monkeypatch.setattr(agent_runtime,'transport',transport)
    return store,row,contract,calls,answers


def run(local):
    store,row,*_=local
    return ControlledLocalFlow().kickoff(inputs={'run_id':row['id'],'database':str(store.path)})


def test_four_roles_checkpoint_and_digest_bound_tests(local):
    store,row,_,calls,_=local
    result=run(local)
    assert result['state']=='delivered', (result,store.events(row['id']))
    assert len(calls)==4
    assert store.metrics(row['id'])['input_tokens']==400
    assert set(store.metrics(row['id'])['role_metrics'])=={'planner','tester','developer','reviewer'}
    candidate=store.output(row['id'],'materialize');tests=store.output(row['id'],'tests')
    assert candidate['tests_sha256']==tests['tests_sha256']==digest(TESTS)
    assert candidate['code_sha256']==tests['code_sha256']
    assert GOOD not in json.dumps(calls[1]['messages'])  # tester antes de ver solución
    developer_context=json.dumps(calls[2]['messages'],ensure_ascii=False)
    assert 'implementar' in developer_context and 'tipos inválidos' in developer_context


def test_negative_review_blocks_delivery(local):
    store,row,_,_,answers=local
    answers[-1]=json.dumps({'approved':False,'findings':['fixture objection']})
    assert run(local)['state']=='blocked_evidence'
    assert store.output(row['id'],'reviewer')['approved'] is False


def test_failing_tests_never_call_reviewer(local,monkeypatch):
    store,row,_,calls,_=local
    monkeypatch.setattr(executor,'run_tests',lambda *a,**k:{'status':'fail','exit_code':1,'timed_out':False})
    assert run(local)['state']=='blocked_evidence'
    assert len(calls)==3 and store.output(row['id'],'reviewer') is None


def test_no_stub_can_certify_local_run(local):
    store,row,*_=local
    row=store.transition(row['id'],row['version'],'contract_pending')
    row=store.transition(row['id'],row['version'],'planning')
    op=store.prepare_local(row['id'],'planner');store.dispatch_local(op)
    with pytest.raises(ValueError):store.confirm_stub(op)


def test_reservation_denied_before_transport(local):
    store,row,contract,calls,_=local
    contract['budgets']['input_tokens']=1
    row=store.create(contract,row['workspace'],[])
    result=ControlledLocalFlow().kickoff(inputs={'run_id':row['id'],'database':str(store.path)})
    assert result['state']=='blocked_budget' and calls==[]


def test_timeout_stays_uncertain_no_retry(local,monkeypatch):
    store,row,*_=local
    attempts=[]
    def timeout(*args):
        attempts.append(1)
        raise subprocess.TimeoutExpired('fixture',1)
    monkeypatch.setattr(agent_runtime,'transport',timeout)
    assert run(local)['state']=='uncertain_operation'
    assert attempts==[1]
    run(local)
    assert attempts==[1]


def test_truncation_is_not_approval(local,monkeypatch):
    store,row,contract,*_=local
    monkeypatch.setattr(agent_runtime,'transport',lambda *a:{'ok':True,'text':'{"steps":[]}',
        'done':True,'done_reason':'length','input_tokens':10,'output_tokens':384,'model_digest':contract['roles']['planner']['resolved_digest']})
    assert run(local)['state']=='blocked_evidence'
    assert store.metrics(row['id'])['output_tokens']==384


@pytest.mark.parametrize('code', [
    'import os\ndef summarize_runs(records): return {}',
    'def summarize_runs(records):\n    return __import__("os").system("echo bad")',
    'def summarize_runs(records):\n    return records.__class__',
    '@print("bad")\ndef summarize_runs(records): return {}',
    'def summarize_runs(records=open("file")): return {}',
    'def summarize_runs(records):\n    exec("bad")',
])
def test_disallowed_code_never_executes(code):
    with pytest.raises(ValueError):validate_candidate(code)


def test_valid_candidate_not_executed_on_host():
    assert validate_candidate(GOOD)==GOOD


def test_artifact_changed_after_validation_blocks_delivery(local):
    store,row,*_=local
    result=ControlledLocalFlow().kickoff(inputs={'run_id':row['id'],'database':str(store.path),'until':'awaiting_review'})
    assert result['state']=='awaiting_review'
    (sandbox(row['id'])/'solution.py').write_text(GOOD+'\n# changed',encoding='utf-8')
    assert run(local)['state']=='blocked_evidence'


def test_pause_during_model_preserves_result_without_next_role(local,monkeypatch):
    store,row,contract,calls,answers=local
    def transport(payload,timeout):
        current=store.get(row['id']);store.pause(row['id'],current['version'])
        return {'ok':True,'text':answers[0],'done':True,'done_reason':'stop','input_tokens':100,'output_tokens':50,'model_digest':contract['roles']['planner']['resolved_digest']}
    monkeypatch.setattr(agent_runtime,'transport',transport)
    assert run(local)['state']=='paused'
    assert store.output(row['id'],'planner') is not None
    assert store.output(row['id'],'tester') is None


def test_changed_model_digest_never_passes(local,monkeypatch):
    monkeypatch.setattr(agent_runtime,'transport',lambda *a:{'ok':True,'text':'{"steps":["fixture"]}',
        'done':True,'done_reason':'stop','input_tokens':10,'output_tokens':10,'model_digest':'0'*64})
    assert run(local)['state']=='blocked_evidence'


def test_local_manifest_cannot_change_actual_executor(local,tmp_path):
    _,_,contract,*_=local
    contract['commands']['unit']['argv']=['python','-c','print(1)']
    with pytest.raises(ValueError):manifest.validate(contract,tmp_path)


def test_pending_reservation_is_visible(local):
    from controller import read_runs
    store,row,contract,*_=local
    row=store.transition(row['id'],row['version'],'contract_pending')
    row=store.transition(row['id'],row['version'],'planning')
    op=store.prepare_local(row['id'],'planner');store.dispatch_local(op)
    result=read_runs(store.path)[0]
    assert result['pending_reservations']['input_tokens']==contract['roles']['planner']['context_tokens']


def test_migration_preserves_existing_v1_rows(tmp_path):
    import sqlite3
    from controller import Store
    path=tmp_path/'old.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript("CREATE TABLE meta(version INTEGER); INSERT INTO meta VALUES(1); CREATE TABLE runs(id TEXT PRIMARY KEY,mode TEXT); INSERT INTO runs VALUES('old','stub'); CREATE TABLE operations(id TEXT PRIMARY KEY);")
    store=Store(path)
    with store.transaction() as db:
        assert db.execute('SELECT version FROM meta').fetchone()[0]==2
        row=db.execute('SELECT * FROM runs').fetchone()
        assert row['id']=='old' and row['execution_kind']=='stub'


def test_frozen_contract_incompatible_with_current_requirement_blocks_before_model(local,monkeypatch):
    import local_pilot
    _,_,_,calls,_=local
    changed=local_pilot.REQUIREMENT+'\nNuevo requisito no autorizado en este run.'
    monkeypatch.setattr(local_pilot,'REQUIREMENT',changed)
    monkeypatch.setattr(agent_runtime,'REQUIREMENT',changed)
    assert run(local)['state']=='blocked_evidence'
    assert calls==[]


def test_resume_reuses_prepared_operation_without_duplicate_reservation(local, monkeypatch):
    store, row, _, calls, _ = local
    original = store.dispatch_local
    prepared = []
    def pause_before_dispatch(op_id):
        prepared.append(op_id)
        current = store.get(row['id'])
        store.pause(row['id'], current['version'])
        return original(op_id)
    monkeypatch.setattr(store.__class__, 'dispatch_local', lambda self, op: pause_before_dispatch(op))
    assert run(local)['state'] == 'paused'
    assert calls == []
    monkeypatch.setattr(store.__class__, 'dispatch_local', lambda self, op: original(op))
    current = store.get(row['id'])
    store.resume(row['id'], current['version'], row['workspace'])
    assert run(local)['state'] == 'delivered'
    assert len(calls) == 4
    with store.transaction() as db:
        ops = db.execute("SELECT id,state FROM operations WHERE run_id=? AND role='planner'", (row['id'],)).fetchall()
    assert [(op['id'], op['state']) for op in ops] == [(prepared[0], 'confirmed')]


@pytest.mark.parametrize('state', ['in_flight', 'uncertain'])
def test_dispatched_operation_never_becomes_retryable(local, state):
    store, row, _, calls, _ = local
    row = store.transition(row['id'], row['version'], 'contract_pending')
    row = store.transition(row['id'], row['version'], 'planning')
    op = store.prepare_local(row['id'], 'planner')
    store.dispatch_local(op)
    if state == 'uncertain':
        store.recover(row['id'])
    with pytest.raises(ValueError):
        store.prepare_local(row['id'], 'planner')
    with pytest.raises(ValueError):
        store.dispatch_local(op)
    assert calls == []


def test_prepared_operation_rejects_changed_identity(local):
    store, row, _, _, _ = local
    row = store.transition(row['id'], row['version'], 'contract_pending')
    row = store.transition(row['id'], row['version'], 'planning')
    op = store.prepare_local(row['id'], 'planner')
    with store.transaction() as db:
        db.execute('UPDATE operations SET manifest_hash=? WHERE id=?', ('0'*64, op))
    with pytest.raises(ValueError):
        store.prepare_local(row['id'], 'planner')


def test_second_worker_does_not_recover_live_operation(local, monkeypatch):
    store, row, _, calls, _ = local
    transport = agent_runtime.transport
    contenders = []
    def competing_transport(payload, timeout):
        if not contenders:
            before = store.events(row['id'])
            contenders.append(run(local))
            assert store.events(row['id']) == before
        return transport(payload, timeout)
    monkeypatch.setattr(agent_runtime, 'transport', competing_transport)
    assert run(local)['state'] == 'delivered'
    assert contenders[0]['busy'] is True
    assert len(calls) == 4
