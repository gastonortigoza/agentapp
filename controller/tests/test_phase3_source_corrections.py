"""Fake roles exercise preservation/correction/audit; never product proof."""
import copy
import pytest
from jsonschema import Draft202012Validator,ValidationError
import controller_gate
import manifest
import phase3_geography_source as old
import phase3_geography_correction as protocol
import phase3_handoff as handoff
import phase3_increment_queue as queue
import phase3_queued_sources as base
import phase3_review as review
import phase3_roles as roles
import phase3_source as source
import phase3_source_corrections as correction
from test_phase3_increment_queue import queued
from test_phase3_auth_execution import scoped
from test_phase3_auth_materializer import tls_fixture
from test_phase3_queued_sources import MARKERS
from test_phase3_review import result

BROKEN=MARKERS+'\nif (cid) { provinces = []; } else if (pid) {}\nif ((cid && !UUID_RE.test(cid)) || (pid && !UUID_RE.test(pid))) {}'
FIXED=MARKERS+'\n// synthetic source markers, not an implementation or product test'


def call(row,op,lock,docs):
    request={'model':row['binding']['model'],'digest':row['binding']['digest'],
      'messages':[{'role':'user','content':roles.prompt(row,op,lock,docs)}],'format':review.output_schema(row,op,lock,docs)}
    op['_record_request'](request)
    if op['role']=='developer':value={'path':old.PATH,'content':BROKEN if row['binding']['section']==old.SECTION else FIXED}
    else:
        quote='invented' if row['binding']['section']==old.SECTION else protocol.evidence(row['candidate'])[1]
        value={'checks':{k:{'passed':True,'pointer':'/content','quote':quote} for k in old.RULES},'findings':[]}
    return result(value,prompt_sha256=manifest.identity(request['messages']))


@pytest.fixture
def correcting(queued):
    j,_,_=queued
    rid='queue-completed-auth';j.create(rid,{'path':'backend/src/auth.ts','content':''},section='auth_api',initial_proposal=True)
    c=review.run(j,rid,caller=lambda *a:result({},done_reason='length'))
    queue.reserve(j,'queue',rid,'review',correction.amount());queue.settle(j,'queue',c,correction.amount())
    queue.run(j,'queue',caller=lambda *a:{'ok':False,'sent':True,'error_type':'timeout'})
    parent=queue.get(j,'queue');base.create(j,'queue',manifest.identity(parent));previous=base.run(j,'queue',caller=call)
    assert previous['state']=='awaiting_discrepancy'
    row=correction.create(j,'queue',manifest.identity(previous))
    return j,parent,previous,row


def test_exact_evidence_schema_prevents_joined_or_invented_quotes_and_preserves_old_protocol():
    candidate={'path':old.PATH,'content':'registerGeography\n  pool.query'}
    value={'checks':{k:{'passed':True,'pointer':'/content','quote':'registerGeography pool.query'} for k in old.RULES},'findings':[]}
    Draft202012Validator(old.review_schema()).validate(value)
    with pytest.raises(ValidationError):Draft202012Validator(protocol.review_schema(candidate)).validate(value)
    value['checks']={k:{'passed':True,'pointer':'/content','quote':'pool.query'} for k in old.RULES}
    protocol.validate_review(value,candidate)
    assert source.review_schema(old.SECTION,candidate)==old.review_schema()
    assert source.SECTIONS==('application','public_api','public_profile')


def test_original_branch_recognizer_does_not_reject_a_new_preceding_joint_branch():
    candidate={'path':old.PATH,'content':BROKEN}
    assert any(f['issue']=='combined_selectors_ignored' for f in protocol.findings(candidate))
    candidate['content']='if (cid && pid) { validateBoth(); } else '+BROKEN
    assert not any(f['issue']=='combined_selectors_ignored' for f in protocol.findings(candidate))
    assert any(f['issue']=='empty_selector_bypasses_validation' for f in protocol.findings(candidate))


def test_real_grounded_findings_require_exact_original_source_and_cannot_invent_product_feedback(correcting):
    j,_,previous,row=correcting;original=j.get(previous['child_id'])
    assert [f['issue'] for f in row['binding']['feedback']['findings']]==['combined_selectors_ignored','empty_selector_bypasses_validation']
    protocol.validate_feedback(row['binding']['feedback'],original['candidate'])
    changed=copy.deepcopy(row['binding']['feedback']);changed['findings'][0]['fix']='invented'
    with pytest.raises(ValueError):protocol.validate_feedback(changed,original['candidate'])
    with pytest.raises(ValueError):j.create('fake',original['candidate'],section=protocol.SECTION)
    with pytest.raises(ValueError):j.create('fake',original['candidate'],section=protocol.SECTION,source_feedback=row['binding']['feedback'],product_feedback={})


def test_correction_preserves_parent_and_previous_source_sums_budget_and_audits_third_attempt(correcting):
    j,parent,previous,_=correcting
    def checked(*a):
        row=correction.get(j,'queue');assert row['used']['calls']==5 and row['operations'][0]['state']=='in_flight'
        return call(*a)
    row=correction.run(j,'queue',caller=checked)
    assert row['state']=='awaiting_flow_audit' and row['used']['calls']==2
    assert row['pending_audit']['packet']['through_round']==3
    assert queue.get(j,'queue')==parent and base.get(j,'queue')==previous
    with j.transaction() as db:assert base.aggregate(db,parent)['calls']==parent['used']['calls']+previous['used']['calls']+2
    assert handoff.consume(j,row['child_id'],protocol.SECTION)[0]['candidate']['content']==FIXED
    row=correction.acknowledge_audit(j,'queue',row['pending_audit']['sha256'],'continue','Inspected originals, feedback, correction roles, usage and no product proof.')
    assert row['state']=='reviewed_source' and not row['product_tests_executed']
    assert correction.run(j,'queue',caller=lambda *a:pytest.fail('No replay'))==row
    assert correction.create(j,'queue',manifest.identity(previous))==row
    with pytest.raises(ValueError):correction.acknowledge_audit(j,'queue',row['audit']['sha256'],'discrepancy','Changed')


def test_uncertain_correction_holds_full_reservation_no_replay_no_audit_of_incomplete(correcting):
    j,parent,previous,_=correcting;calls=[]
    def unknown(*a):calls.append(1);return {'ok':False,'sent':True,'error_type':'timeout'}
    row=correction.run(j,'queue',caller=unknown)
    assert row['state']=='uncertain_operation' and row['used']['calls']==5 and not row['pending_audit']
    assert correction.run(j,'queue',caller=lambda *a:pytest.fail('No replay'))==row and len(calls)==1
    with j.transaction() as db:assert base.aggregate(db,parent)['active_ms']==parent['used']['active_ms']+previous['used']['active_ms']+600000


def test_restart_after_complete_child_settles_originals_without_new_model(correcting,monkeypatch):
    j,_,_,_=correcting;settle=correction.settle
    monkeypatch.setattr(correction,'settle',lambda *a:(_ for _ in ()).throw(RuntimeError('parent interrupted')))
    with pytest.raises(RuntimeError):correction.run(j,'queue',caller=call)
    monkeypatch.setattr(correction,'settle',settle)
    row=correction.run(j,'queue',caller=lambda *a:pytest.fail('Original only'))
    assert row['state']=='awaiting_flow_audit' and row['used']['calls']==2


@pytest.mark.parametrize('mutation',['previous','old_ops','old_events','binding','feedback','counts','acceptance','controller'])
def test_drift_prevents_new_correction_model_call(correcting,mutation,monkeypatch):
    j,_,previous,_=correcting
    if mutation=='controller':monkeypatch.setattr(controller_gate,'require_green',lambda:'other-controller')
    else:
        with j.transaction() as db:
            if mutation=='previous':previous['reason']='drift';base.save(db,previous)
            elif mutation in ('old_ops','old_events'):
                table='plan_ops' if mutation=='old_ops' else 'plan_events'
                record=manifest.parse(db.execute(f'SELECT body FROM {table} WHERE run_id=? AND seq=0',(previous['child_id'],)).fetchone()[0]);record['drift']=True
                db.execute(f'UPDATE {table} SET body=? WHERE run_id=? AND seq=0',(manifest.canonical(record),previous['child_id']))
            else:
                row=correction.load(db,'queue')
                if mutation=='binding':row['binding']['suite_identity']='drift'
                elif mutation=='feedback':row['binding']['feedback']['findings'][0]['fix']='drift';row['binding_sha256']=manifest.identity(row['binding'])
                elif mutation=='counts':row['used']['calls']=1
                else:row['full_product_acceptance']=True
                correction.save(db,row)
    with pytest.raises(ValueError):correction.run(j,'queue',caller=lambda *a:pytest.fail('No dispatch on drift'))


def test_incomplete_original_cannot_create_correction(queued):
    j,_,_=queued;queue.run(j,'queue',caller=lambda *a:{'ok':False,'sent':True,'error_type':'timeout'})
    parent=queue.get(j,'queue');base.create(j,'queue',manifest.identity(parent))
    previous=base.run(j,'queue',caller=lambda *a:{'ok':False,'sent':True,'error_type':'timeout'})
    with pytest.raises(ValueError):correction.create(j,'queue',manifest.identity(previous))


def test_truncation_retains_full_charge_and_requires_third_round_audit(correcting):
    j,_,_,_=correcting
    row=correction.run(j,'queue',caller=lambda *a:result({},done_reason='length'))
    assert row['state']=='awaiting_flow_audit' and row['used']['calls']==5
    assert not row['operations'][0]['source_accepted'] and row['pending_audit']['packet']['through_round']==3


def test_budget_blocks_before_model_or_child_creation(correcting,monkeypatch):
    j,parent,previous,_=correcting;caps=queue.LIMITS|{'calls':parent['used']['calls']+previous['used']['calls']+4}
    monkeypatch.setattr(queue,'LIMITS',caps)
    with j.transaction() as db:
        parent['binding']['limits']=caps;parent['binding_sha256']=manifest.identity(parent['binding']);queue.save(db,parent)
        previous['binding']['parent_snapshot']=copy.deepcopy(parent);previous['binding']['parent_sha256']=manifest.identity(parent)
        previous['binding_sha256']=manifest.identity(previous['binding']);base.save(db,previous)
        row=correction.load(db,'queue');row['binding']['limits']=caps;row['binding']['parent_sha256']=manifest.identity(parent)
        row['binding']['previous_snapshot']=copy.deepcopy(previous);row['binding']['previous_sha256']=manifest.identity(previous)
        row['binding_sha256']=manifest.identity(row['binding']);correction.save(db,row)
    row=correction.run(j,'queue',caller=lambda *a:pytest.fail('No capacity'))
    assert row['state']=='blocked_budget' and row['operations']==[]
    with pytest.raises(ValueError):j.get(row['child_id'])
