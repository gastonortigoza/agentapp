"""Simulated source lineage, not PostgreSQL acceptance or SaaS completeness."""
import pytest
from jsonschema import Draft202012Validator,ValidationError
import controller_gate
import manifest
import phase3_geography_source as old
import phase3_geography_revalidation as protocol
import phase3_handoff as handoff
import phase3_increment_queue as queue
import phase3_queued_sources as base
import phase3_review as review
import phase3_roles as roles
import phase3_source as source
import phase3_source_corrections as correction
import phase3_source_revalidations as ledger
from test_phase3_source_corrections import correcting,call,FIXED
from test_phase3_increment_queue import queued
from test_phase3_auth_execution import scoped
from test_phase3_auth_materializer import tls_fixture
from test_phase3_review import result

LONG=FIXED+'\n//'+('x'*4200)


def overflow(row,op,lock,docs):
    request={'model':row['binding']['model'],'digest':row['binding']['digest'],
      'messages':[{'role':'user','content':roles.prompt(row,op,lock,docs)}],'format':review.output_schema(row,op,lock,docs)}
    op['_record_request'](request)
    return result({'path':old.PATH,'content':LONG},prompt_sha256=manifest.identity(request['messages']))


def approved(row,op,lock,docs):
    request={'model':row['binding']['model'],'digest':row['binding']['digest'],
      'messages':[{'role':'user','content':roles.prompt(row,op,lock,docs)}],'format':review.output_schema(row,op,lock,docs)}
    op['_record_request'](request)
    value={'checks':{k:{'passed':True,'pointer':'/content','quote':protocol.evidence(row['candidate'])[1]} for k in protocol.RULES},'findings':[]}
    return result(value,prompt_sha256=manifest.identity(request['messages']))


@pytest.fixture
def revalidating(correcting):
    j,parent,first,_=correcting
    prior=correction.run(j,'queue',caller=overflow)
    assert prior['pending_audit']['packet']['through_round']==3 and prior['state']=='awaiting_flow_audit'
    prior=correction.acknowledge_audit(j,'queue',prior['pending_audit']['sha256'],'continue','Original overflow confirmed; new prospective source evaluation only.')
    row=ledger.create(j,'queue',manifest.identity(prior))
    return j,parent,first,prior,row


def test_prospective_bound_preserves_historical_schema_and_raw_seed(revalidating):
    j,_,_,prior,row=revalidating
    assert j.get(prior['child_id'])['candidate']['content']!=LONG
    assert row['binding']['seed']['content']==LONG
    Draft202012Validator(protocol.writer_schema()).validate(row['binding']['seed'])
    with pytest.raises(ValidationError):Draft202012Validator(old.writer_schema()).validate(row['binding']['seed'])
    assert old.RULES['G03']!=protocol.RULES['G03'] and source.LIMITS==j.get(prior['child_id'])['binding']['limits']
    assert source.SECTIONS==('application','public_api','public_profile')


def test_reviewer_first_shared_budget_audit_four_and_immutable_history(revalidating):
    j,parent,first,prior,row=revalidating;calls=[]
    def checked(*a):
        live=ledger.get(j,'queue');assert live['used']['calls']==5 and live['operations'][0]['state']=='in_flight'
        calls.append(a[1]['role']);assert a[1]['reserve_output']==1000 and a[1]['timeout_seconds']<=150
        return approved(*a)
    row=ledger.run(j,'queue',caller=checked)
    assert row['state']=='reviewed_source' and row['used']['calls']==1 and calls==['reviewer']
    assert not row['pending_audit'] and not row['audit']
    assert queue.get(j,'queue')==parent and base.get(j,'queue')==first and correction.get(j,'queue')==prior
    with j.transaction() as db:
        assert base.aggregate(db,parent)['calls']==parent['used']['calls']+first['used']['calls']+prior['used']['calls']+1
        assert ledger.packet(row,parent,prior,first)['from_round']==4
        assert ledger.packet(row,parent,prior,first)['through_round']==4
    assert handoff.consume(j,row['child_id'],protocol.SECTION)[0]['candidate']['content']==LONG
    assert ledger.run(j,'queue',caller=lambda *a:pytest.fail('No replay'))==row
    assert ledger.create(j,'queue',manifest.identity(prior))==row
    assert not row['product_tests_executed'] and not row['full_product_acceptance']


def test_semantic_rejection_encounters_bounded_developer_and_reviewer_without_codex_steps(revalidating):
    j,_,_,_,_=revalidating;seen=[]
    def rejecting(row,op,lock,docs):
        seen.append(op['role'])
        if len(seen)==1:
            value=approved(row,op,lock,docs)
            parsed=manifest.parse(value['text']);parsed['checks']['G02']['passed']=False
            parsed['findings']=[{'rule':'G02','source':protocol.SOURCE,'rule_quote':protocol.RULES['G02'],
                'pointer':'/content','issue':'incomplete_catalogue','fix':'Check empty zones before returning200.'}]
            return value|{'text':manifest.canonical(parsed)}
        if op['role']=='developer':return overflow(row,op,lock,docs)
        return approved(row,op,lock,docs)
    row=ledger.run(j,'queue',caller=rejecting)
    assert seen==['reviewer','developer','reviewer'] and row['state']=='reviewed_source'
    assert handoff.consume(j,row['child_id'],protocol.SECTION)[0]['calls']==3


@pytest.mark.parametrize('fix',sorted(protocol.CONTRADICTORY_FIXES))
def test_known_contract_contradictions_stop_before_writing_and_preserve_raw_response(revalidating,fix):
    j,_,_,prior,_=revalidating;raw=[]
    def contradictory(row,op,lock,docs):
        assert op['role']=='reviewer'
        value=approved(row,op,lock,docs);parsed=manifest.parse(value['text'])
        parsed['checks']['G02']['passed']=False
        parsed['findings']=[{'rule':'G02','source':protocol.SOURCE,'rule_quote':protocol.RULES['G02'],
            'pointer':'/content','issue':'Unsupported demand contradicting the frozen rule','fix':fix}]
        value['text']=manifest.canonical(parsed);raw.append(value['text']);return value
    row=ledger.run(j,'queue',caller=contradictory)
    assert row['state']=='awaiting_discrepancy' and row['used']['calls']==1
    child=j.get(row['child_id']);ops=j.records(child['id'],'plan_ops')
    assert child['state']=='blocked_evidence' and child['corrections']==0
    assert len(ops)==1 and ops[0]['result']['text']==raw[0]
    assert correction.get(j,'queue')==prior and not row['operations'][0]['source_accepted']


@pytest.fixture
def original_overflow():
    candidate={'path':old.PATH,'content':FIXED}
    binding={'section':'geography_correction','rules':old.RULES,'digest':'a'*64,'model':'pinned-local'}
    request={'format':old.writer_schema(),'model':binding['model'],'digest':binding['digest'],'messages':[]}
    child={'id':'original','state':'blocked_evidence','reason':'Invalid output schema: ValidationError',
      'binding':binding,'binding_sha256':manifest.identity(binding),'calls':1,'execution_authorized':False,'product_tests_executed':False,
      'candidate':candidate,'candidate_sha256':manifest.identity(candidate)}
    op={'seq':0,'role':'developer','state':'confirmed','candidate':candidate,'reserve_input':32768,'reserve_output':1800,
      'request':request,'request_sha256':manifest.identity(request),
      'result':result({'path':old.PATH,'content':LONG},model_digest=binding['digest'],prompt_sha256=manifest.identity([]))}
    return child,[op]


@pytest.mark.parametrize('mutation',['truncated','partial','path','extra','short','too_long','request','digest','counts','binding','not_overflow'])
def test_only_complete_pinned_output_with_sole_length_failure_can_be_adopted(original_overflow,mutation):
    child,ops=original_overflow
    r=ops[0]['result'];parsed=manifest.parse(r['text'])
    if mutation=='truncated':r['done_reason']='length'
    elif mutation=='partial':r.pop('text');r['partial_text']='unconfirmed'
    elif mutation=='path':parsed['path']='backend/src/auth.ts'
    elif mutation=='extra':parsed['extra']=True
    elif mutation=='short':parsed['content']=FIXED
    elif mutation=='too_long':parsed['content']='x'*6001
    elif mutation=='request':ops[0]['request']['format']={}
    elif mutation=='digest':r['model_digest']='b'*64
    elif mutation=='counts':r['output_tokens']=1801
    elif mutation=='binding':child['binding']['rules']={}
    else:child['reason']='other'
    if mutation in ('path','extra','short','too_long'):r['text']=manifest.canonical(parsed)
    with pytest.raises((ValueError,ValidationError)):protocol.origin(child,ops)


def test_uncertainty_keeps_reservation_no_replay_or_spurious_round(revalidating):
    j,parent,_,_,_=revalidating
    row=ledger.run(j,'queue',caller=lambda *a:{'ok':False,'sent':True,'error_type':'timeout'})
    assert row['state']=='uncertain_operation' and row['used']['calls']==5 and not row['pending_audit']
    assert ledger.run(j,'queue',caller=lambda *a:pytest.fail('No replay'))==row
    with j.transaction() as db:assert base.aggregate(db,parent)['calls']>=15


def test_completed_child_after_interruption_is_settled_without_another_call(revalidating,monkeypatch):
    j,_,_,_,_=revalidating;settle=ledger.settle
    monkeypatch.setattr(ledger,'settle',lambda *a:(_ for _ in ()).throw(RuntimeError('interrupted')))
    with pytest.raises(RuntimeError):ledger.run(j,'queue',caller=approved)
    monkeypatch.setattr(ledger,'settle',settle)
    assert ledger.run(j,'queue',caller=lambda *a:pytest.fail('No new call'))['state']=='reviewed_source'


@pytest.mark.parametrize('mutation',['audit','old_ops','old_events','seed','counts','controller'])
def test_preserved_history_and_current_controller_drift_prevent_dispatch(revalidating,mutation,monkeypatch):
    j,_,_,prior,_=revalidating
    if mutation=='controller':monkeypatch.setattr(controller_gate,'require_green',lambda:'changed')
    else:
        with j.transaction() as db:
            if mutation=='audit':prior['audit']['notes']='changed';correction.save(db,prior)
            elif mutation in ('old_ops','old_events'):
                table='plan_ops' if mutation=='old_ops' else 'plan_events'
                value=manifest.parse(db.execute(f'SELECT body FROM {table} WHERE run_id=? AND seq=0',(prior['child_id'],)).fetchone()[0]);value['changed']=True
                db.execute(f'UPDATE {table} SET body=? WHERE run_id=? AND seq=0',(manifest.canonical(value),prior['child_id']))
            else:
                row=ledger.load(db,'queue')
                if mutation=='seed':row['binding']['seed']['content']+='changed';row['binding_sha256']=manifest.identity(row['binding'])
                else:row['used']['calls']=1
                ledger.save(db,row)
    with pytest.raises(ValueError):ledger.run(j,'queue',caller=lambda *a:pytest.fail('No dispatch'))


def test_controls_cannot_be_mixed_and_origin_drift_blocks_handoff(revalidating):
    j,_,_,prior,row=revalidating;b=row['binding']
    with pytest.raises(ValueError):j.create('bad',b['seed'],section=protocol.SECTION,source_origin=b['origin'],expected_checks={})
    with pytest.raises(ValueError):j.create('bad',b['seed'],section=protocol.SECTION)
    row=ledger.run(j,'queue',caller=approved)
    child=j.get(prior['child_id']);child['reason']='changed'
    with j.transaction() as db:
        j.save(db,child,{'kind':'test.original_drift'})
    with pytest.raises(ValueError):handoff.consume(j,row['child_id'],protocol.SECTION)


def test_aggregate_cap_blocks_before_creating_child_or_call(revalidating,monkeypatch):
    j,_,_,_,row=revalidating
    monkeypatch.setattr(ledger,'amount',lambda:correction.amount()|{'calls':queue.LIMITS['calls']})
    row=ledger.run(j,'queue',caller=lambda *a:pytest.fail('No capacity'))
    assert row['state']=='blocked_budget' and not row['operations']
    with pytest.raises(ValueError):j.get(row['child_id'])
