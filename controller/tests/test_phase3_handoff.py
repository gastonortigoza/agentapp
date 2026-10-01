import copy
from pathlib import Path
import shutil
import pytest
import controller_gate
import executor
import manifest
import phase3_contract as contract
import phase3_handoff as handoff
import phase3_prepare as preparation
import phase3_review as review
from test_phase3_contract import approved,result


@pytest.fixture(scope='module')
def originals(tmp_path_factory):
    root=tmp_path_factory.mktemp('accepted-reviews')
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(review,'ROOT',root/'control')
        monkeypatch.setattr(controller_gate,'require_green',lambda:'exact-source')
        return build_originals(root)


def build_originals(root):
    journal=review.Journal(root/'review.sqlite');lock,docs,_=preparation.load_bundle()
    plan=preparation.default_plan(lock,docs);ids={}
    for kind in handoff.KINDS:
        candidate=plan if kind=='plan' else docs['contract.json'][kind]
        row=journal.create('accepted-'+kind,candidate,section=None if kind=='plan' else kind)
        def call(row,op,*args):
            request={'model':row['binding']['model'],'digest':row['binding']['digest'],
                     'messages':[{'role':'user','content':'Bounded synthetic test'}],
                     'format':review.output_schema(row,op,lock,docs)}
            op['_record_request'](request)
            body=(approved(row['candidate'],kind,docs) if kind!='plan' else
                  {'checks':{rule:{'passed':True,'pointer':'/schema','quote':plan['schema']} for rule in review.RULES},'findings':[]})
            return result(body,prompt_sha256=manifest.identity(request['messages']))
        assert review.run(journal,row['id'],call)['state'].startswith('reviewed_')
        ids[kind]=row['id']
    return journal,ids,plan,docs


@pytest.fixture
def ready(tmp_path,monkeypatch,originals):
    source,ids,plan,docs=originals
    monkeypatch.setattr(controller_gate,'require_green',lambda:'exact-source')
    monkeypatch.setattr(executor,'WORKSPACE',tmp_path)
    shutil.copyfile(source.path,tmp_path/'review.sqlite')
    journal=review.Journal(tmp_path/'review.sqlite')
    snapshot=tmp_path/'snapshot'
    shutil.copytree(source.get(ids['plan'])['snapshot_directory'],snapshot)
    with journal.transaction() as db:
        for run_id in ids.values():
            row=manifest.parse(db.execute('SELECT body FROM plan_runs WHERE id=?',(run_id,)).fetchone()[0])
            row['snapshot_directory']=str(snapshot)
            db.execute('UPDATE plan_runs SET body=? WHERE id=?',(manifest.canonical(row),run_id))
    workspace=tmp_path/'application';workspace.mkdir()
    for entry in plan['files']:
        path=workspace/entry['path'];path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('{}' if path.suffix=='.json' else '// bounded synthetic input',encoding='utf-8')
    # No subprocess, including Docker healthcheck, may run through phase3.
    monkeypatch.setattr(executor.subprocess,'run',lambda *a,**k:pytest.fail('Unsupported command dispatched'))
    monkeypatch.setattr(executor.subprocess,'Popen',lambda *a,**k:pytest.fail('Unsupported command dispatched'))
    return journal,ids,workspace,docs


@pytest.mark.parametrize('command',['phase3-prepare','phase3-review','phase3-check-execution'])
def test_public_agent_entry_routes_phase3_commands(monkeypatch,command):
    import agent,controller_cli
    seen=[]
    monkeypatch.setattr(controller_cli,'main',lambda args:seen.append(args) or 2)
    assert agent.main([command])==2 and seen==[[command]]


def test_original_reviews_consumed_by_executor_and_resume_is_idempotent(ready):
    journal,ids,workspace,docs=ready
    result=executor.run_phase3(journal,'preflight',ids,workspace)
    assert result['state']=='blocked_execution_policy'
    assert result['contract']==docs['contract.json']
    assert not result['execution_authorized'] and not result['product_tests_executed']
    assert result['binding_sha256']==manifest.identity(result['binding'])
    assert len(result['binding']['reviews'])==5
    assert executor.run_phase3(review.Journal(journal.path),'preflight',ids,workspace)==result


def test_studio_preflight_is_blocked_with_no_duplicate_model_usage(ready):
    from phase3_studio import PreflightObserver
    journal,ids,workspace,_=ready
    result=executor.run_phase3(journal,'preflight',ids,workspace)
    class Store:
        row=None;events=[]
        def get_run(self,*args):return self.row
        def create_run(self,r):self.row=r
        def append_event(self,*args):self.events.append(args[-1])
    store=Store();observer=PreflightObserver(store)
    observer(result);observer(result)
    assert store.row['status']=='failed' and store.row['tokens']==0 and len(store.events)==1
    assert 'blocked_execution_policy' in store.row['result']
    assert store.row['inputs']['reviews']==result['binding']['reviews']
    altered=copy.deepcopy(result);altered['binding_sha256']='other'
    with pytest.raises(ValueError):observer(altered)


@pytest.mark.parametrize('path',['backend/src/app.ts','frontend/src/App.tsx','package-lock.json','frontend/package.json'])
def test_code_lock_and_package_changes_invalidate_preflight(ready,path):
    journal,ids,workspace,_=ready
    before=executor.run_phase3(journal,'preflight',ids,workspace)
    (workspace/path).write_text('changed',encoding='utf-8')
    with pytest.raises(ValueError,match='Preflight drift'):executor.run_phase3(journal,'preflight',ids,workspace)
    with journal.transaction() as db:
        record=manifest.parse(db.execute('SELECT body FROM phase3_preflights WHERE id=?',('preflight',)).fetchone()[0])
    assert record==before


@pytest.mark.parametrize('change',['fixture','false-approval','uncertain','raw-citation','request','seed','candidate','snapshot','controller'])
def test_forged_or_stale_acceptance_never_reaches_dispatch(ready,monkeypatch,change):
    journal,ids,workspace,docs=ready;run_id=ids['api'];row=journal.get(run_id)
    if change=='controller':monkeypatch.setattr(controller_gate,'require_green',lambda:'different-source')
    elif change=='snapshot':Path(row['snapshot_directory'],'contract.json').write_text('{}')
    elif change in {'uncertain','raw-citation','request','seed'}:
        with journal.transaction() as db:
            op=manifest.parse(db.execute('SELECT body FROM plan_ops WHERE run_id=? AND seq=0',(run_id,)).fetchone()[0])
            if change=='uncertain':op['state']='in_flight'
            elif change=='request':op['request']['messages'][0]['content']='Changed request'
            elif change=='seed':op['candidate_sha256']='0'*64
            else:op['result']['text']=op['result']['text'].replace('candidate-', 'invented-')
            db.execute('UPDATE plan_ops SET body=? WHERE run_id=? AND seq=0',(manifest.canonical(op),run_id))
    else:
        if change=='fixture':row['scope']='contract_fixture_evaluation'
        elif change=='candidate':row['candidate']['dtos']['PublicProfile']['photos']='$MissingPhoto[]|null'
        else:
            # Even synchronized row/operation hashes and an all-true cited review
            # cannot hide an undefined DTO from deterministic validation.
            row['candidate']['dtos']['PublicProfile']['photos']='$MissingPhoto[]|null'
            row['candidate_sha256']=manifest.identity(row['candidate'])
            with journal.transaction() as db:
                op=manifest.parse(db.execute('SELECT body FROM plan_ops WHERE run_id=? AND seq=0',(run_id,)).fetchone()[0])
                op.update(candidate=row['candidate'],candidate_sha256=row['candidate_sha256'])
                body=approved(row['candidate'],'api',docs)
                op['result']['text']=manifest.canonical(body)
                op['validated_review']=contract.validate_review(body,row['candidate'],'api',docs)
                row['binding']['seed_sha256']=row['candidate_sha256']
                row['binding_sha256']=manifest.identity(row['binding'])
                op['request']['format']=review.output_schema(row,op,None,docs)
                op['request_sha256']=manifest.identity(op['request'])
                db.execute('UPDATE plan_ops SET body=? WHERE run_id=? AND seq=0',(manifest.canonical(op),run_id))
        with journal.transaction() as db:db.execute('UPDATE plan_runs SET body=? WHERE id=?',(manifest.canonical(row),run_id))
    with pytest.raises(Exception):executor.run_phase3(journal,'preflight',ids,workspace)


def test_missing_and_unplanned_files_are_not_product_passes(ready):
    journal,ids,workspace,_=ready
    (workspace/'backend/package-lock.json').unlink()
    (workspace/'backend/extra.ts').write_text('extra')
    result=executor.run_phase3(journal,'preflight',ids,workspace)
    assert {'planned_file_missing','unplanned_file','documentary_policy'}=={f['code'] for f in result['findings']}
    assert not result['product_tests_executed']


@pytest.mark.parametrize('path',['.env','backend/secrets/password','backend/node_modules/unsafe.js','.git/config'])
def test_protected_input_files_are_denied_without_dispatch(ready,path):
    journal,ids,workspace,_=ready
    target=workspace/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text('not emitted')
    with pytest.raises(ValueError):executor.run_phase3(journal,'preflight',ids,workspace)


def test_workspace_outside_authorized_root_and_byte_limits_are_denied(ready,tmp_path,monkeypatch):
    journal,ids,workspace,_=ready
    monkeypatch.setattr(executor,'WORKSPACE',workspace)
    with pytest.raises(ValueError):executor.run_phase3(journal,'preflight',ids,workspace)
    monkeypatch.setattr(executor,'WORKSPACE',tmp_path)
    monkeypatch.setattr(handoff,'MAX_BYTES',1)
    with pytest.raises(ValueError,match='byte limit'):executor.run_phase3(journal,'preflight',ids,workspace)


def test_cli_returns_blocked_evidence_with_immutable_export(ready,monkeypatch,tmp_path,capsys):
    import controller_cli
    journal,ids,workspace,_=ready
    monkeypatch.setattr(review,'Journal',lambda:journal)
    reviews=tmp_path/'ids.json';reviews.write_text(manifest.canonical(ids))
    export=tmp_path/'preflight.json'
    argv=['phase3-check-execution','--preflight-id','cli','--reviews',str(reviews),'--workspace',str(workspace),'--export',str(export)]
    assert controller_cli.main(argv)==2
    content=export.read_bytes();assert b'blocked_execution_policy' in content
    assert controller_cli.main(argv)==2 and export.read_bytes()==content
    (workspace/'package-lock.json').write_text('drift')
    assert controller_cli.main(argv)==2 and export.read_bytes()==content
    capsys.readouterr()


@pytest.mark.parametrize('section,container,field,code',[
    ('api',['dtos','PublicCard'],'main_photo','required_dto_field'),
    ('api',['routes',0,'response'],'items','required_route_field'),
    ('data',['tables','users','columns'],'email','required_column'),
    ('data',['tables'],'photos','required_table'),
])
def test_required_contract_members_cannot_disappear(ready,section,container,field,code):
    *_,docs=ready;candidate=copy.deepcopy(docs['contract.json'][section]);target=candidate
    for part in container:target=target[part]
    del target[field]
    assert code in {f['issue'] for f in contract.defects(candidate,section,docs)}


def test_malformed_reference_suffix_is_denied_but_alias_still_valid(ready):
    *_,docs=ready;candidate=copy.deepcopy(docs['contract.json']['api'])
    candidate['dtos']['PublicProfile']['photos']='$Photo|null[]'
    assert 'undefined_dto' in {f['issue'] for f in contract.defects(candidate,'api',docs)}
    candidate['dtos']['PublicProfile']['photos']='$Photo[]|null'
    candidate['dtos']['Photo']['created_at']='RFC3339 UTC timestamp'
    assert contract.defects(candidate,'api',docs)==[]
