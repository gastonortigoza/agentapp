import json
from pathlib import Path
import pytest
import flow
import executor

GOOD='def unique(items):\n    out=[]\n    for x in items:\n        if x not in out: out.append(x)\n    return out\n'

def prepare(monkeypatch,tmp_path,test_status,approved=True):
    monkeypatch.setattr(flow,'WORKSPACE',tmp_path/'workspace')
    monkeypatch.setattr(flow.lab,'ROOT',tmp_path)
    monkeypatch.setattr(flow.lab,'event',lambda *a,**kw:None)
    calls=[]
    def agent(role,*args):
        calls.append(role)
        return GOOD if role=='developer' else (json.dumps({'approved':approved,'findings':'fixture'}) if role=='reviewer' else 'Resultados recibidos')
    monkeypatch.setattr(flow.lab,'run_agent',agent)
    count=[]
    def test(*a):
        count.append(1)
        return {'status':test_status,'exit_code':0 if test_status=='pass' else (125 if test_status=='infrastructure_error' else 1),'stdout':'fixture','stderr':'','duration_ms':1}
    monkeypatch.setattr(flow,'run_tests',test)
    return calls,count

def test_pass_requires_real_test_success(monkeypatch,tmp_path):
    calls,count=prepare(monkeypatch,tmp_path,'fail')
    result=flow.DevelopmentFlow().kickoff()
    assert result['status']=='unresolved'
    assert result['current_round']==3 and len(count)==3
    assert 'tester' not in calls

def test_reviewer_can_veto(monkeypatch,tmp_path):
    _,count=prepare(monkeypatch,tmp_path,'pass',False)
    result=flow.DevelopmentFlow().kickoff()
    assert result['status']=='unresolved' and len(count)==3

def test_infrastructure_stops_without_retry(monkeypatch,tmp_path):
    _,count=prepare(monkeypatch,tmp_path,'infrastructure_error')
    result=flow.DevelopmentFlow().kickoff()
    assert result['status']=='blocked_infrastructure' and len(count)==1

def test_success_produces_diff(monkeypatch,tmp_path):
    calls,count=prepare(monkeypatch,tmp_path,'pass')
    result=flow.DevelopmentFlow().kickoff()
    assert result['status']=='pass' and len(count)==1 and calls[-1]=='tester'
    assert (tmp_path/'runs'/result['run_id']/'proposal.diff').exists()

def test_path_escape_is_rejected(tmp_path,monkeypatch):
    allowed=tmp_path/'allowed'
    allowed.mkdir()
    outside=tmp_path/'outside'
    outside.mkdir()
    monkeypatch.setattr(executor,'WORKSPACE',allowed)
    with pytest.raises(ValueError): executor.within_workspace(outside)
    with pytest.raises(ValueError): executor.within_workspace(allowed)

def test_unlisted_git_is_rejected(tmp_path,monkeypatch):
    allowed=tmp_path/'allowed'
    repo=allowed/'repo'
    repo.mkdir(parents=True)
    monkeypatch.setattr(executor,'WORKSPACE',allowed)
    for action in ('push','reset --hard','status;whoami','branch --delete'):
        with pytest.raises(ValueError): executor.git_read(repo,action)

def test_extra_files_are_rejected(tmp_path,monkeypatch):
    repo=tmp_path/'repo'
    repo.mkdir()
    monkeypatch.setattr(executor,'WORKSPACE',tmp_path)
    (repo/'secret.env').write_text('synthetic-test')
    with pytest.raises(ValueError): executor.run_tests(repo)


def test_oversized_input_blocks_before_docker(tmp_path,monkeypatch):
    repo=tmp_path/'candidate'
    repo.mkdir()
    monkeypatch.setattr(executor,'WORKSPACE',tmp_path)
    (repo/'solution.py').write_bytes(b' '*executor.MAX_INPUT_BYTES)
    (repo/'test_solution.py').write_text('x')
    monkeypatch.setattr(executor.subprocess,'run',lambda *a,**k:pytest.fail('Docker must not start'))
    with pytest.raises(ValueError,match='1 MiB'):executor.run_tests(repo)
