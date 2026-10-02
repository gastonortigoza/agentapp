import copy
import json
from pathlib import Path
import shutil

import pytest
import controller_cli
import controller_gate
import phase3_prepare as phase3


@pytest.fixture
def plan():
    return phase3.prepare()['plan']


def test_preparation_uses_accepted_contract_without_claiming_execution(plan):
    result = phase3.prepare(plan=plan)
    assert result['execution_authorized'] is False
    assert result['product_tests_executed'] is False
    assert result['limits_claimed_enforced'] is False
    assert result['integrated_base']=='7e20c4426663d265b2d2a70e7af0aa4bed9e5e4b'
    assert len(result['criteria'])==36
    assert result['plan']['contract_sha256']=='c94f752ed8b08eefb16fa5dc840e8c04fd947070e773cd3139d7bbd7465475dc'
    assert not any(s.startswith('AGE-30 ') for s in result['unresolved_execution_blockers'])
    assert result['unresolved_execution_blockers']


@pytest.mark.parametrize('name',['contract.json','contract.schema.json','manifest.json','policy.json','business-decisions.json','business-source.json','acceptance.json','ui-contract.json'])
def test_modified_input_is_not_silently_adopted(tmp_path,name):
    bundle = tmp_path/'input'
    shutil.copytree(phase3.FIXTURE,bundle)
    original = (bundle/name).read_bytes()
    (bundle/name).write_bytes(original+b'\n')
    with pytest.raises(phase3.PreparationError,match='input_drift'):
        phase3.snapshot(bundle,tmp_path/'snapshots')
    assert not (tmp_path/'snapshots').exists()


@pytest.mark.parametrize('path',[
    '../controller/agent.py','backend/../controller.py','backend//src/app.ts','/backend/app.ts',
    'C:/backend/app.ts','backend/app.ts:token','backend/.env','backend/.ENV.local',
    'backend/.git/config','backend/.github/workflows/build.yml','backend/secrets/token',
    'backend/node_modules/package/index.js','backend/CON.ts','backend/aux','backend/a./b.ts',
    'controller/agent.py','infra/postgres/migrate.sql','frontend/app tsx'])
def test_protected_and_ambiguous_paths_are_denied(plan,path):
    plan['files'].append({'path':path,'purpose':'Synthetic adversarial path','criteria':[]})
    with pytest.raises(phase3.PreparationError,match='path_denied'):
        phase3.prepare(plan=plan)


@pytest.mark.parametrize('value',['something-else','status-summary','attention-summary'])
def test_requirement_not_supported_by_this_adapter_is_rejected(plan,value):
    plan['requirement_id']=value
    with pytest.raises(phase3.PreparationError,match='plan_identity'):
        phase3.prepare(plan=plan)


def test_command_injection_is_not_a_plan_capability(plan):
    plan['commands']={'unit':['powershell','-Command','Synthetic denied launcher']}
    with pytest.raises(phase3.PreparationError,match='plan_shape'):
        phase3.prepare(plan=plan)


def test_stage_cannot_select_arbitrary_argv_or_omit_a_check(plan):
    plan['stages'][-1]='npm install'
    with pytest.raises(phase3.PreparationError,match='stage_denied'):
        phase3.prepare(plan=plan)


def test_file_output_cannot_supply_generated_code(plan):
    plan['files'][0]['content']='Synthetic unexecuted code'
    with pytest.raises(phase3.PreparationError,match='file_shape'):
        phase3.prepare(plan=plan)


def test_criterion_citation_cannot_invent_a_requirement(plan):
    plan['files'][0]['criteria'].append('A99')
    with pytest.raises(phase3.PreparationError,match='criterion_reference'):
        phase3.prepare(plan=plan)


def test_missing_criterion_is_reported_actionably(plan):
    for entry in plan['files']:
        entry['criteria']=[c for c in entry['criteria'] if c!='UI02']
    with pytest.raises(phase3.PreparationError) as error:
        phase3.prepare(plan=plan)
    assert error.value.finding['code']=='criteria_missing'
    assert error.value.finding['pointer']=='/files'
    assert error.value.finding['fix']


def test_case_colliding_path_cannot_create_two_windows_files(plan):
    entry=copy.deepcopy(next(f for f in plan['files'] if f['path']=='backend/src/app.ts'))
    entry['path']='backend/src/App.ts'
    plan['files'].append(entry)
    with pytest.raises(phase3.PreparationError,match='duplicate_path'):
        phase3.prepare(plan=plan)


def test_lockfile_must_be_planned(plan):
    plan['files']=[f for f in plan['files'] if f['path']!='backend/package-lock.json']
    with pytest.raises(phase3.PreparationError,match='scaffold_missing'):
        phase3.prepare(plan=plan)


def test_snapshot_repeats_idempotently_and_does_not_replace_modified_evidence(tmp_path):
    first=phase3.snapshot(phase3.FIXTURE,tmp_path)
    assert phase3.snapshot(phase3.FIXTURE,tmp_path)==first
    folder=Path(first['snapshot_directory'])
    assert (folder/'contract.json').read_bytes()==(phase3.FIXTURE/'contract.json').read_bytes()
    (folder/'preparation.json').write_text('Preserve this altered evidence',encoding='utf-8')
    with pytest.raises(phase3.PreparationError,match='snapshot_conflict'):
        phase3.snapshot(phase3.FIXTURE,tmp_path)
    assert (folder/'preparation.json').read_text()=='Preserve this altered evidence'


def test_old_plan_cannot_be_rebound_to_a_different_revision(plan):
    plan['contract_sha256']='0'*64
    with pytest.raises(phase3.PreparationError,match='plan_identity'):
        phase3.prepare(plan=plan)


def test_strict_json_rejects_duplicate_fields(tmp_path):
    path=tmp_path/'plan.json'
    path.write_text('{"schema":"one","schema":"two"}',encoding='utf-8')
    with pytest.raises(ValueError):phase3.read_json(path)


def test_cli_requires_current_controller_suite(tmp_path,monkeypatch,capsys):
    monkeypatch.setattr(controller_cli,'ROOT',tmp_path)
    def deny():raise ValueError('Synthetic stale suite receipt')
    monkeypatch.setattr(controller_gate,'require_green',deny)
    assert controller_cli.main(['phase3-prepare','--bundle',str(phase3.FIXTURE)])==2
    assert 'stale suite' in capsys.readouterr().out
    assert not (tmp_path/'.state').exists()


def test_cli_only_prepares_without_dispatching_commands(tmp_path,monkeypatch,capsys):
    monkeypatch.setattr(controller_cli,'ROOT',tmp_path)
    monkeypatch.setattr(controller_gate,'require_green',lambda:'synthetic current receipt')
    def forbidden(*args,**kwargs):raise AssertionError('Preparation cannot execute commands')
    monkeypatch.setattr(controller_cli.subprocess,'run',forbidden)
    assert controller_cli.main(['phase3-prepare','--bundle',str(phase3.FIXTURE)])==0
    result=json.loads(capsys.readouterr().out)
    assert result['execution_authorized'] is False
    assert Path(result['snapshot_directory']).is_relative_to(tmp_path)


def test_phase3_inputs_invalidate_controller_suite_identity(tmp_path):
    root=tmp_path/'controller'
    root.mkdir()
    for name in ('pyproject.toml','uv.lock'):(root/name).write_text('Synthetic manifest',encoding='utf-8')
    (root/'config').mkdir()
    (root/'fixtures/phase3-contract-v1').mkdir(parents=True)
    lock=root/'config/phase3-input-lock.json'
    fixture=root/'fixtures/phase3-contract-v1/contract.json'
    lock.write_text('{}',encoding='utf-8'); fixture.write_text('{}',encoding='utf-8')
    first=controller_gate.source_identity(root)
    lock.write_text('{"changed":true}',encoding='utf-8')
    second=controller_gate.source_identity(root)
    assert first!=second
    fixture.write_text('{"changed":true}',encoding='utf-8')
    assert second!=controller_gate.source_identity(root)
