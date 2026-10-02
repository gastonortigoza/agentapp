"""Simulated source-only continuation; no model or product acceptance here."""
import copy
import pytest
import controller_gate
import manifest
import phase3_geography_source as geo
import phase3_handoff as handoff
import phase3_increment_queue as queue
import phase3_prepare as prep
import phase3_queued_sources as ext
import phase3_review as review
import phase3_roles as roles
import phase3_source as source
from test_phase3_increment_queue import queued
from test_phase3_auth_execution import scoped
from test_phase3_auth_materializer import tls_fixture
from test_phase3_review import result


MARKERS='registerGeography /api/catalog/geography catalog_unavailable agentapp.countries agentapp.provinces agentapp.zones country_id province_id geography_invalid request_id service_unavailable'


@pytest.fixture
def stopped(queued):
    journal,_,_=queued
    queue.run(journal,'queue',caller=lambda *a:{'ok':False,'sent':True,'error_type':'timeout'})
    parent=queue.get(journal,'queue')
    ext.create(journal,'queue',manifest.identity(parent))
    return journal,parent


def caller(row,op,lock,docs):
    assert op['reserve_output']==(1800 if op['role']=='developer' else 1000)
    request={'model':row['binding']['model'],'digest':row['binding']['digest'],
      'messages':[{'role':'user','content':roles.prompt(row,op,lock,docs)}],
      'format':review.output_schema(row,op,lock,docs)}
    op['_record_request'](request)
    value=({'path':geo.PATH,'content':MARKERS} if op['role']=='developer' else
      {'checks':{k:{'passed':True,'pointer':'/content','quote':'registerGeography'} for k in geo.RULES},'findings':[]})
    return result(value,prompt_sha256=manifest.identity(request['messages']))


def test_distinct_source_shares_aggregate_and_preserves_old_uncertain_originals(stopped):
    j,parent=stopped
    with j.transaction() as db:before=ext.child_originals(db,parent)
    row=ext.run(j,'queue',caller=caller)
    assert row['state']=='reviewed_source' and row['used']['calls']==2
    assert not row['product_tests_executed'] and not row['full_product_acceptance']
    assert queue.get(j,'queue')==parent
    with j.transaction() as db:
        assert ext.child_originals(db,parent)==before
        assert ext.aggregate(db,parent)['calls']==parent['used']['calls']+2
    assert handoff.consume(j,row['child_id'],geo.SECTION)[0]['candidate']['content']==MARKERS
    assert ext.run(j,'queue',caller=lambda *a:pytest.fail('No replay'))==row
    assert ext.create(j,'queue',manifest.identity(parent))==row


def test_new_source_cannot_reset_queue_id_or_binding(stopped,monkeypatch):
    j,parent=stopped
    with pytest.raises(ValueError):ext.create(j,'reset',manifest.identity(parent))
    monkeypatch.setattr(controller_gate,'require_green',lambda:'other-controller')
    with pytest.raises(ValueError,match='fresh budget'):ext.create(j,'queue',manifest.identity(parent))


def test_unknown_holds_entire_child_reservation_no_resend(stopped):
    j,parent=stopped;calls=[]
    def uncertain(*a):calls.append(1);return {'ok':False,'sent':True,'error_type':'timeout'}
    row=ext.run(j,'queue',caller=uncertain)
    assert row['state']=='uncertain_operation' and row['used']['calls']==5
    assert ext.run(j,'queue',caller=lambda *a:pytest.fail('No replay'))==row and calls==[1]
    with j.transaction() as db:assert ext.aggregate(db,parent)['active_ms']==parent['used']['active_ms']+600000


def test_truncated_completion_stops_source_and_retains_full_conservative_charge(stopped):
    j,_=stopped
    row=ext.run(j,'queue',caller=lambda *a:result({},done_reason='length'))
    assert row['state']=='awaiting_discrepancy' and row['used']['calls']==5
    assert not row['operations'][0]['source_accepted']


def test_reservation_is_durable_before_model_and_restart_can_settle_completed_child(stopped,monkeypatch):
    j,_=stopped;original=ext.settle_original
    def checked(*args):
        row=ext.get(j,'queue');assert row['operations'][0]['state']=='in_flight' and row['used']['calls']==5
        return caller(*args)
    monkeypatch.setattr(ext,'settle_original',lambda *a:(_ for _ in ()).throw(RuntimeError('parent interrupted')))
    with pytest.raises(RuntimeError):ext.run(j,'queue',caller=checked)
    monkeypatch.setattr(ext,'settle_original',original)
    row=ext.run(j,'queue',caller=lambda *a:pytest.fail('Read original, do not replay'))
    assert row['state']=='reviewed_source' and row['used']['calls']==2


@pytest.mark.parametrize('mutation',['parent','child','binding','scope','counts','acceptance'])
def test_drift_blocks_dispatch(stopped,mutation):
    j,parent=stopped
    with j.transaction() as db:
        if mutation=='parent':
            parent['reason']='drift';queue.save(db,parent)
        elif mutation=='child':
            cid=parent['operations'][0]['child_id'];child=manifest.parse(db.execute('SELECT body FROM plan_runs WHERE id=?',(cid,)).fetchone()[0])
            child['reason']='drift';db.execute('UPDATE plan_runs SET body=? WHERE id=?',(manifest.canonical(child),cid))
        else:
            row=ext.load(db,'queue')
            if mutation=='binding':row['binding']['suite_identity']='changed'
            elif mutation=='scope':row['scope']='auth-session-core'
            elif mutation=='counts':row['used']['calls']=1
            else:row['full_product_acceptance']=True
            ext.save(db,row)
    with pytest.raises(ValueError):ext.run(j,'queue',caller=lambda *a:pytest.fail('Drift dispatch'))


def test_budget_block_occurs_before_new_child(stopped,monkeypatch):
    j,parent=stopped
    # Freeze a lower cap in both original and extension, only inside simulation.
    caps=queue.LIMITS|{'calls':parent['used']['calls']+4}
    monkeypatch.setattr(queue,'LIMITS',caps)
    with j.transaction() as db:
        parent['binding']['limits']=caps;parent['binding_sha256']=manifest.identity(parent['binding']);queue.save(db,parent)
        row=ext.load(db,'queue');row['binding']['parent_snapshot']=parent;row['binding']['parent_sha256']=manifest.identity(parent)
        row['binding_sha256']=manifest.identity(row['binding']);ext.save(db,row)
    row=ext.run(j,'queue',caller=lambda *a:pytest.fail('No model capacity'))
    assert row['state']=='blocked_budget' and not row['operations']
    with pytest.raises(ValueError):j.get(row['child_id'])


def test_third_completed_round_stops_for_immutable_shared_audit(queued):
    j,_,_=queued
    # Two original completed failures, then one uncertain original, all source-only.
    for n in range(2):
        rid='queue-history-'+str(n);j.create(rid,{'path':'backend/src/auth.ts','content':''},section='auth_api',initial_proposal=True)
        child=review.run(j,rid,caller=lambda *a:result({},done_reason='length'))
        amount={'calls':5,'input_tokens':163840,'output_tokens':20000,'active_ms':600000}
        queue.reserve(j,'queue',rid,'review',amount);queue.settle(j,'queue',child,amount)
    queue.run(j,'queue',caller=lambda *a:{'ok':False,'sent':True,'error_type':'timeout'})
    parent=queue.get(j,'queue');ext.create(j,'queue',manifest.identity(parent))
    row=ext.run(j,'queue',caller=caller)
    assert row['state']=='awaiting_flow_audit' and row['pending_audit']['packet']['through_round']==3
    assert len(row['pending_audit']['packet']['originals'])==3
    digest=row['pending_audit']['sha256']
    row=ext.acknowledge_audit(j,'queue',digest,'continue','Reviewed original failures, source review, corrections, conserved accounting and no product acceptance.')
    assert row['state']=='reviewed_source' and queue.get(j,'queue')==parent
    assert ext.acknowledge_audit(j,'queue',digest,'continue',row['audit']['notes'])==row
    with pytest.raises(ValueError):ext.acknowledge_audit(j,'queue',digest,'discrepancy','Changed decision')


def test_geography_schema_citations_and_static_markers_are_only_documentary():
    candidate={'path':geo.PATH,'content':MARKERS}
    assert not source.defects(candidate,geo.SECTION)
    assert geo.SECTION not in source.SECTIONS and geo.PATH not in source.PATHS.values()
    assert all(len(v)<=800 for v in geo.RULES.values())
    value={'checks':{k:{'passed':True,'pointer':'/content','quote':'registerGeography'} for k in geo.RULES},'findings':[]}
    source.validate_review(value,candidate,geo.SECTION)
    value['checks']['G01']['quote']='invented'
    with pytest.raises(ValueError):source.validate_review(value,candidate,geo.SECTION)
    assert source.defects({'path':geo.PATH,'content':MARKERS+' process.env.SECRET'},geo.SECTION)
    _,docs,_=prep.load_bundle();row={'candidate':candidate,'findings':[],'binding':{'section':geo.SECTION}}
    assert 'explicitly disposable synthetic' in source.prompt(row,{'role':'developer'},docs)


@pytest.mark.parametrize('table',['plan_ops','plan_events'])
def test_settled_source_keeps_every_original_operation_and_event(stopped,table):
    j,_=stopped;row=ext.run(j,'queue',caller=caller)
    with j.transaction() as db:
        record=manifest.parse(db.execute(f'SELECT body FROM {table} WHERE run_id=? AND seq=0',(row['child_id'],)).fetchone()[0])
        record['tampered']=True
        db.execute(f'UPDATE {table} SET body=? WHERE run_id=? AND seq=0',(manifest.canonical(record),row['child_id']))
    with pytest.raises(ValueError,match='original changed'):ext.run(j,'queue',caller=lambda *a:pytest.fail('No replay'))
