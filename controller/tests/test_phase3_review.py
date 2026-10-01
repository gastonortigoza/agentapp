import copy
import json
from pathlib import Path
import pytest
import controller_cli
import controller_gate
import manifest
import phase3_prepare as preparation
import phase3_review as review
from phase3_roles import prompt


@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setattr(review,'ROOT',tmp_path)
    monkeypatch.setattr(controller_gate,'require_green',lambda:'frozen-source')
    lock,docs,_=preparation.load_bundle()
    plan=preparation.default_plan(lock,docs)
    return review.Journal(tmp_path/'journal.sqlite'),plan,lock,docs


def approved(plan):
    return {'checks':{k:{'passed':True,'pointer':'/schema','quote':plan['schema']} for k in review.RULES},'findings':[]}


def result(value,**changes):
    return {'ok':True,'done':True,'done_reason':'stop','tool_calls':False,
            'model_digest':'25b843619e944cd0ae6069f94ff4e5e26a16e109ccbc0a66a0f05979ed70098e',
            'text':manifest.canonical(value),'input_tokens':40,'output_tokens':30,**changes}


def test_false_approval_is_vetoed_then_agent_replaces_plan_and_is_reviewed(env):
    journal,plan,lock,docs=env
    defective=copy.deepcopy(plan)
    defective['files']=[f for f in defective['files'] if f['path']!='backend/package-lock.json']
    journal.create('automatic-correction',defective)
    seen=[]
    def call(row,op,*args):
        seen.append(op['role'])
        if op['role']=='developer':
            assert any(f['issue']=='scaffold_missing' for f in row['findings'])
            assert all(f['rule_quote']==review.RULES[f['rule']] for f in row['findings'])
            return result(plan)
        return result(approved(row['candidate']))
    final=review.run(journal,'automatic-correction',call)
    assert seen==['reviewer','developer','reviewer']
    assert final['state']=='reviewed_plan'
    assert final['candidate']==plan and final['corrections']==1
    ops=journal.records(final['id'],'plan_ops')
    assert ops[0]['candidate']==defective
    assert json.loads(ops[1]['result']['text'])==plan
    assert final['execution_authorized'] is False and final['product_tests_executed'] is False
    assert final['input_tokens']==120 and final['output_tokens']==90
    assert review.run(journal,final['id'],lambda *args:pytest.fail('Terminal run resent'))==final


def test_in_flight_interruption_never_resends_even_after_restart(env):
    journal,plan,_,_=env
    journal.create('crash',plan)
    op=journal.reserve('crash')
    payload={'messages':[{'role':'user','content':'Evidence preserved before dispatch'}]}
    journal.record_request('crash',op['seq'],payload)
    with pytest.raises(ValueError):journal.record_request('crash',op['seq'],payload)
    restarted=review.Journal(journal.path)
    row=review.run(restarted,'crash',lambda *args:pytest.fail('Uncertain operation resent'))
    assert row['state']=='uncertain_operation' and row['calls']==1
    preserved=restarted.records('crash','plan_ops')[0]
    assert preserved['request']==payload and preserved['state']=='in_flight'
    assert preserved['request_sha256']==manifest.identity(payload)


@pytest.mark.parametrize('change',[
    {'done_reason':'length'}, {'model_digest':'wrong'}, {'tool_calls':True},
    {'input_tokens':None}, {'output_tokens':30000}, {'done':False}, {'input_tokens':True}])
def test_bad_completion_is_preserved_and_blocks_without_retry(env,change):
    journal,plan,_,_=env
    journal.create('bad-evidence',plan)
    raw=result(approved(plan),**change)
    row=review.run(journal,'bad-evidence',lambda *args:raw)
    assert row['state']=='blocked_evidence' and row['calls']==1
    assert journal.records(row['id'],'plan_ops')[0]['result']==raw


@pytest.mark.parametrize('sent,expected',[(True,'uncertain_operation'),(False,'blocked_evidence')])
def test_failed_transport_does_not_resend(env,sent,expected):
    journal,plan,_,_=env;journal.create('failure',plan)
    row=review.run(journal,'failure',lambda *args:{'ok':False,'sent':sent})
    assert row['state']==expected and row['calls']==1


def test_invalid_citation_blocks_even_if_reviewer_approves(env):
    journal,plan,_,_=env;journal.create('quote',plan)
    fake=approved(plan);fake['checks']['P03']['quote']='Invented quote'
    row=review.run(journal,'quote',lambda *args:result(fake))
    assert row['state']=='blocked_evidence' and row['calls']==1


def test_false_rejection_cannot_cite_invented_business_requirement(env):
    _,plan,_,_=env
    fake=approved(plan);fake['checks']['P03']['passed']=False
    fake['findings']=[{'rule':'P03','source':'planning-rules/1','rule_quote':'Require infinite TTL',
                      'pointer':'','issue':'Synthetic false rejection','fix':'Change frozen commercial terms'}]
    with pytest.raises(ValueError,match='citation'):review.validate_review(fake,plan)


def test_rejected_check_requires_actionable_finding(env):
    _,plan,_,_=env;fake=approved(plan);fake['checks']['P03']['passed']=False
    with pytest.raises(ValueError,match='disagree'):review.validate_review(fake,plan)


def test_pointer_does_not_join_unrelated_evidence(env):
    _,plan,_,_=env;fake=approved(plan)
    fake['checks']['P01'].update(pointer='/files/0/path',quote='backend/package.json')
    with pytest.raises(ValueError,match='quote'):review.validate_review(fake,plan)


@pytest.mark.parametrize('path',['/files/-1','/files/00','/schema/~2','files/0'])
def test_noncanonical_evidence_pointers_rejected(env,path):
    _,plan,_,_=env
    with pytest.raises((ValueError,KeyError)):review.resolve_pointer(plan,path)


def test_budget_stops_repeated_false_approval_of_uncorrected_plan(env):
    journal,plan,_,_=env;plan['files']=plan['files'][:-1]
    for file in plan['files']:file['criteria']=[c for c in file['criteria'] if c!='UI11']
    journal.create('bounded',plan)
    def call(row,op,*args):return result(row['candidate'] if op['role']=='developer' else approved(row['candidate']))
    row=review.run(journal,'bounded',call)
    assert row['state']=='blocked_budget' and row['calls']==4
    assert row['corrections']==2 and row['findings']


def test_snapshot_drift_blocks_before_any_call(env):
    journal,plan,_,_=env;row=journal.create('input-drift',plan)
    path=Path(row['snapshot_directory'])/'policy.json';path.write_bytes(path.read_bytes()+b'\n')
    row=review.run(journal,row['id'],lambda *args:pytest.fail('Drift dispatched'))
    assert row['state']=='blocked_evidence' and row['calls']==0


def test_controller_drift_blocks_before_any_call(env,monkeypatch):
    journal,plan,_,_=env;journal.create('source-drift',plan)
    monkeypatch.setattr(controller_gate,'require_green',lambda:'another-source')
    row=review.run(journal,'source-drift',lambda *args:pytest.fail('Drift dispatched'))
    assert row['state']=='blocked_evidence' and row['calls']==0


def test_drift_during_inference_preserves_output_but_prevents_acceptance(env,monkeypatch):
    journal,plan,_,_=env;journal.create('during',plan)
    def call(*args):
        monkeypatch.setattr(controller_gate,'require_green',lambda:'another-source')
        return result(approved(plan))
    row=review.run(journal,'during',call)
    assert row['state']=='blocked_evidence'
    op=journal.records('during','plan_ops')[0]
    assert op['result']['text']==manifest.canonical(approved(plan))


def test_duplicate_run_cannot_replace_seed(env):
    journal,plan,_,_=env;journal.create('same',plan)
    with pytest.raises(Exception):journal.create('same',{'different':'seed'})
    assert journal.get('same')['candidate']==plan


def test_feedback_keeps_every_schema_defect_and_no_fake_business_rejection(env):
    _,plan,lock,docs=env
    plan['files'][0].update(argv=['bad'],code='synthetic')
    plan['requirement_id']='unsupported'
    errors=review.defects(plan,lock,docs)
    assert len(errors)>=2
    assert review.defects(preparation.default_plan(lock,docs),lock,docs)==[]


def test_developer_output_cannot_silently_rewrite_contract(env):
    journal,plan,_,_=env
    bad=copy.deepcopy(plan);bad['files']=[f for f in bad['files'] if f['path']!='package-lock.json']
    journal.create('rewrite',bad)
    changed=copy.deepcopy(plan);changed['contract_sha256']='0'*64
    row=review.run(journal,'rewrite',lambda row,op,*args:result(changed if op['role']=='developer' else approved(row['candidate'])))
    assert row['state']=='blocked_evidence' and row['calls']==2
    assert row['candidate']==bad


def test_prompt_preserves_defects_without_requests_to_change_frozen_contract(env):
    journal,plan,lock,docs=env;row=journal.create('prompt',plan)
    row['findings']=[{'issue':'first defect'},{'issue':'last defect'}]
    text=prompt(row,{'role':'developer'},lock,docs)
    assert 'first defect' in text and 'last defect' in text
    assert 'cannot be rewritten' in text and 'no code or argv' in text


def test_cli_terminal_state_is_not_reported_as_success(env,monkeypatch,capsys):
    journal,plan,_,_=env;journal.create('stopped',plan);journal.change('stopped','uncertain_operation','test interruption')
    monkeypatch.setattr(review,'DATABASE',journal.path)
    # The default is bound at function definition, so supply our journal explicitly.
    monkeypatch.setattr(review,'Journal',lambda:journal)
    assert controller_cli.main(['phase3-review','--run-id','stopped'])==2
    assert 'uncertain_operation' in capsys.readouterr().out


def test_export_retains_exact_original_model_text_and_scope(env,tmp_path):
    journal,plan,_,_=env;journal.create('export',plan)
    raw=json.dumps(approved(plan),indent=3)
    review.run(journal,'export',lambda *args:result({},text=raw))
    path=review.export(journal,'export',tmp_path/'evidence.json')
    data=json.loads(path.read_text())
    assert data['operations'][0]['result']['text']==raw
    assert data['run']['scope']=='file_plan_structural_review'


def test_long_run_id_uses_bounded_windows_snapshot_component(env):
    journal,plan,_,_=env
    row=journal.create('a'*100,plan)
    path=Path(row['snapshot_directory'])
    assert path.parent.name==manifest.identity({'run_id':'a'*100})[:16]
    assert path.parent.parent.name=='p3r'
    assert review.verify_binding(row)


def test_next_call_timeout_uses_remaining_active_time(env):
    journal,plan,_,_=env;journal.create('time',plan)
    with journal.transaction() as db:
        row=manifest.parse(db.execute('SELECT body FROM plan_runs WHERE id=?',('time',)).fetchone()[0])
        row['active_ms']=590000
        journal.save(db,row,{'kind':'test.controlled_clock'})
    assert journal.reserve('time')['timeout_seconds']==10
