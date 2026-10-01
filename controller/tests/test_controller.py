from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import pytest

import controller
import controller_gate
import manifest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(controller_gate,'require_green',lambda:None)
    monkeypatch.setattr(manifest,'verify_base',lambda *a:None)
    contract=json.loads((ROOT/'config/pilot-manifest.json').read_text(encoding='utf-8'))
    contract['budgets']['rounds']=3
    (tmp_path/'source.py').write_text('old')
    store=controller.Store(tmp_path/'state.sqlite')
    row=store.create(contract,tmp_path,['source.py'])
    return store,row,tmp_path,contract


def advance(store,row,target='implementing'):
    for state in ['contract_pending','planning','implementing']:
        row=store.transition(row['id'],row['version'],state)
        if state==target:break
    return row


def test_state_and_event_commit_together_and_reopen(setup):
    store,row,_,_=setup
    row=advance(store,row)
    reopened=controller.Store(store.path)
    assert reopened.get(row['id'])['state']=='implementing'
    events=reopened.events(row['id'])
    assert events[-1]['version']==row['version']
    assert len(events)==row['version']


def test_event_failure_rolls_back_state(setup,monkeypatch):
    store,row,_,_=setup
    original=store._event
    def crash(*args,**kwargs):
        original(*args,**kwargs)
        raise RuntimeError('injected-before-commit')
    monkeypatch.setattr(store,'_event',crash)
    with pytest.raises(RuntimeError):store.transition(row['id'],row['version'],'contract_pending')
    assert store.get(row['id'])==row
    assert len(store.events(row['id']))==1


def test_workers_competing_transition_one_wins(setup):
    store,row,_,_=setup
    def worker(_):
        try:
            store.transition(row['id'],row['version'],'contract_pending')
            return True
        except controller.Conflict:return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(worker,range(2)))==[False,True]


def test_pause_blocks_dispatch_and_prepare(setup):
    store,row,_,_=setup
    row=advance(store,row)
    op=store.prepare(row['id'],row['version'],'one')
    row=store.get(row['id'])
    row=store.pause(row['id'],row['version'])
    with pytest.raises(controller.Conflict):store.claim(op)
    with pytest.raises(controller.Conflict):store.prepare(row['id'],row['version'],'two')


def test_operation_claim_has_single_winner(setup):
    store,row,_,_=setup
    row=advance(store,row)
    op=store.prepare(row['id'],row['version'],'one')
    def worker(_):
        try:store.claim(op);return True
        except controller.Conflict:return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(worker,range(2)))==[False,True]


def test_pause_after_dispatch_preserves_result(setup):
    store,row,root,_=setup
    row=advance(store,row);op=store.prepare(row['id'],row['version'],'one')
    store.claim(op);row=store.get(row['id']);row=store.pause(row['id'],row['version'])
    with pytest.raises(controller.Conflict):store.resume(row['id'],row['version'],root)
    store.confirm_stub(op,7)
    row=store.get(row['id'])
    assert row['state']=='paused' and row['active_ms']==7
    assert store.resume(row['id'],row['version'],root)['state']=='implementing'


def test_crash_after_dispatch_requires_reconciliation(setup):
    store,row,_,_=setup
    row=advance(store,row);op=store.prepare(row['id'],row['version'],'one')
    store.claim(op)
    store=controller.Store(store.path);store.recover(row['id'])
    row=store.get(row['id'])
    assert row['state']=='uncertain_operation'
    with pytest.raises(controller.Conflict):store.claim(op)
    with pytest.raises(controller.Conflict):store.transition(row['id'],row['version'],'planning')
    store.confirm_stub(op)
    row=store.get(row['id'])
    assert store.transition(row['id'],row['version'],'planning')['state']=='planning'


def test_drift_invalidates_evidence_but_preserves_history(setup):
    store,row,root,_=setup
    row=advance(store,row);op=store.prepare(row['id'],row['version'],'one')
    store.claim(op);store.confirm_stub(op)
    row=store.get(row['id']);row=store.pause(row['id'],row['version'])
    (root/'source.py').write_text('changed')
    row=store.resume(row['id'],row['version'],root)
    assert row['state']=='planning'
    row=store.transition(row['id'],row['version'],'implementing')
    with pytest.raises(ValueError):store.transition(row['id'],row['version'],'validating')
    assert any(e['kind']=='drift_detected' for e in store.events(row['id']))


def test_terminal_cannot_be_reopened(setup):
    store,row,_,_=setup
    row=advance(store,row);op=store.prepare(row['id'],row['version'],'one')
    store.claim(op);store.confirm_stub(op)
    row=store.get(row['id'])
    for target in ['validating','awaiting_review','delivered']:
        row=store.transition(row['id'],row['version'],target)
    for target in ['planning','received','rejected']:
        with pytest.raises(controller.Conflict):store.transition(row['id'],row['version'],target)
    with pytest.raises(controller.Conflict):store.pause(row['id'],row['version'])


def test_time_budget_blocks_completion(setup):
    store,row,_,contract=setup
    row=advance(store,row);op=store.prepare(row['id'],row['version'],'one')
    store.claim(op);store.confirm_stub(op,contract['budgets']['active_seconds']*1000)
    row=store.get(row['id'])
    assert row['state']=='blocked_budget'
    with pytest.raises(controller.Conflict):store.transition(row['id'],row['version'],'validating')


def test_missing_evidence_and_duplicate_operation(setup):
    store,row,_,_=setup
    row=advance(store,row)
    with pytest.raises(ValueError):store.transition(row['id'],row['version'],'validating')
    store.prepare(row['id'],row['version'],'one');row=store.get(row['id'])
    with pytest.raises(sqlite3.IntegrityError):store.prepare(row['id'],row['version'],'one')


def test_read_only_query_does_not_create_database(tmp_path):
    path=tmp_path/'missing.db'
    assert controller.read_runs(path)==[] and not path.exists()


def test_crewai_stub_flow(setup):
    from controlled_flow import ControlledStubFlow
    store,row,_,_=setup
    result=ControlledStubFlow().kickoff(inputs={'run_id':row['id'],'database':str(store.path)})
    assert result['state']=='delivered' and result['scope']=='stub_only'
    assert controller.read_runs(store.path)[0]['remaining_budget']['active_ms'] < 120000


def test_dispatch_persistence_failure_never_claims(setup,monkeypatch):
    store,row,_,_=setup
    row=advance(store,row);op=store.prepare(row['id'],row['version'],'one')
    original=store._event
    def fail(*a,**k):
        original(*a,**k)
        raise OSError('disk error')
    monkeypatch.setattr(store,'_event',fail)
    with pytest.raises(OSError):store.claim(op)
    with store.transaction() as db:
        assert db.execute('SELECT state FROM operations WHERE id=?',(op,)).fetchone()[0]=='prepared'


def test_actual_process_exit_before_and_after_commit(setup):
    import subprocess,sys
    store,row,_,_=setup
    script = '''import os,sys
from controller import Store
s=Store(sys.argv[1])
with s.transaction() as db:
    row=s._row(db,sys.argv[2])
    s._event(db,row,'contract_pending','crash_fixture')
    if sys.argv[3]=='before': os._exit(9)
os._exit(9)
'''
    for when,expected in [('before','received'),('after','contract_pending')]:
        result=subprocess.run([sys.executable,'-c',script,str(store.path),row['id'],when],cwd=ROOT,timeout=20)
        assert result.returncode==9
        current=store.get(row['id'])
        assert current['state']==expected
        assert store.events(row['id'])[-1]['new_state']==expected


def test_round_budget_not_expanded_on_replan(setup):
    store,row,root,contract=setup
    contract['budgets']['rounds']=1
    row=store.create(contract,root,['source.py']);row=advance(store,row)
    row=store.pause(row['id'],row['version'])
    (root/'source.py').write_text('changed')
    row=store.resume(row['id'],row['version'],root)
    assert store.transition(row['id'],row['version'],'implementing')['state']=='blocked_budget'


def test_source_drift_before_dispatch_blocks(setup):
    store,row,root,_=setup
    row=advance(store,row);op=store.prepare(row['id'],row['version'],'one')
    (root/'source.py').write_text('changed')
    with pytest.raises(controller.Conflict):store.claim(op)


def test_lockfile_and_policy_drift_keep_frozen_contract(setup):
    store,_,root,contract=setup
    for name in ['uv.lock','policy.json']:
        (root/name).write_text('original')
        row=store.create(contract,root,[name]);row=advance(store,row,'planning')
        row=store.pause(row['id'],row['version'])
        (root/name).write_text('modified')
        row=store.resume(row['id'],row['version'],root)
        assert row['state']=='planning'
        assert json.loads(row['manifest'])==contract
        assert store.events(row['id'])[-1]['kind']=='drift_detected'


def test_red_suite_blocks_create_but_preserves_diagnostics_and_pause(setup,monkeypatch):
    store,row,root,contract=setup
    def fail():raise ValueError('red suite')
    monkeypatch.setattr(controller_gate,'require_green',fail)
    with pytest.raises(ValueError):store.create(contract,root,['source.py'])
    with pytest.raises(ValueError):store.transition(row['id'],row['version'],'contract_pending')
    assert store.get(row['id'])['state']=='received'
    assert store.pause(row['id'],row['version'])['state']=='paused'


def test_pause_competing_with_dispatch_is_serialized(setup):
    store,row,_,_=setup
    row=advance(store,row);op=store.prepare(row['id'],row['version'],'one')
    def pause():
        while True:
            current=store.get(row['id'])
            try:store.pause(row['id'],current['version']);return
            except controller.Conflict:continue
    def claim():
        try:store.claim(op)
        except controller.Conflict:pass
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=[pool.submit(pause),pool.submit(claim)]
        for future in results:future.result(timeout=10)
    events=store.events(row['id'])
    paused=next(e['seq'] for e in events if e['kind']=='paused')
    assert all(e['seq']<paused for e in events if e['kind']=='operation_dispatched')


@pytest.mark.parametrize('target',['implementing','validating','delivered','paused','unknown'])
def test_invalid_transitions_preserve_state(setup,target):
    store,row,_,_=setup
    with pytest.raises(controller.Conflict):store.transition(row['id'],row['version'],target)
    assert store.get(row['id'])==row


def test_metrics_are_explicitly_simulated(setup):
    store,row,_,_=setup
    metrics=store.metrics(row['id'])
    assert metrics['mode']=='stub' and metrics['input_tokens']==0
    assert metrics['role_metrics']=={} and metrics['total_wall_ms']>=0


def test_flow_can_continue_same_run_after_pause(setup):
    from controlled_flow import ControlledStubFlow
    store,row,root,_=setup
    inputs={'run_id':row['id'],'database':str(store.path),'until':'planning'}
    assert ControlledStubFlow().kickoff(inputs=inputs)['state']=='planning'
    row=store.get(row['id']);row=store.pause(row['id'],row['version'])
    row=store.resume(row['id'],row['version'],root)
    inputs['until']='delivered'
    assert ControlledStubFlow().kickoff(inputs=inputs)['state']=='delivered'


def test_status_elapsed_time_matches_terminal_metrics(setup):
    from controlled_flow import ControlledStubFlow
    store,row,_,_=setup
    ControlledStubFlow().kickoff(inputs={'run_id':row['id'],'database':str(store.path)})
    status=controller.read_runs(store.path)[0]
    assert status['elapsed_ms']==store.metrics(row['id'])['total_wall_ms']
    assert status['last_event'] is not None
