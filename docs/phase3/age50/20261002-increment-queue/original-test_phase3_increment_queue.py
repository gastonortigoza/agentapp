"""Queue protocol uses simulated models/Docker; never SaaS acceptance."""
import copy
import json
from pathlib import Path

import pytest
import controller_gate
import executor
import manifest
import phase3_auth_source as auth
import phase3_auth_materializer as factory
import phase3_auth_execution as ledger
import phase3_auth_sandbox as sandbox
import phase3_increment_queue as queue
import phase3_review as review
import phase3_roles as roles
import phase3_prepare as preparation
from test_phase3_auth_execution import scoped
from test_phase3_auth_materializer import tls_fixture,DockerProtocol,tar_bytes
from test_phase3_auth_source import markers,accepted
from test_phase3_review import result


@pytest.fixture
def queued(scoped,tls_fixture,tmp_path,monkeypatch):
    journal,_,_,_,_=scoped
    ca,cert,key,_,_=tls_fixture;tool=b'qualified synthetic NSS tool'
    monkeypatch.setattr(sandbox,'CERTUTIL_SHA256',sandbox.sha(tool))
    tls={'ca_sha256':sandbox.sha(ca),'server_sha256':sandbox.sha(cert),'certutil_sha256':sandbox.CERTUTIL_SHA256}
    recipe={'tls_public':tls,'synthetic_protocol_resource_binding':True}
    monkeypatch.setattr(queue,'inputs',lambda *a:(copy.deepcopy(recipe),(ca,cert,key,b'synthetic deb')))
    monkeypatch.setattr(factory,'certutil_from_deb',lambda *a:tool)
    monkeypatch.setattr(factory,'validate_nss_deb',lambda *a:True)
    monkeypatch.setattr(sandbox,'cached_tarballs',lambda *a:{'a'*64+'.tgz':b'synthetic cached dependency'})
    monkeypatch.setattr(executor,'WORKSPACE',tmp_path/'workspaces')
    row=queue.create(journal,'queue',tmp_path/'cache',tmp_path/'tls',tmp_path/'nss.deb')
    return journal,row,recipe


def caller(row,op,lock,docs):
    section=row['binding']['section'];candidate=row['candidate']
    request={'model':row['binding']['model'],'digest':row['binding']['digest'],
      'messages':[{'role':'user','content':roles.prompt(row,op,lock,docs)}],
      'format':review.output_schema(row,op,lock,docs)}
    op['_record_request'](request)
    if op['role']=='developer':value={'path':auth.PATHS[section],'content':markers(section)}
    elif candidate['content']:value=accepted(candidate,section)
    else:
        rules=auth.rules(section)
        value={'checks':{k:{'passed':False,'pointer':'/content','quote':''} for k in rules},
          'findings':[{'rule':k,'source':'public-source-rules/1','rule_quote':v,'pointer':'/content',
            'issue':'missing_module','fix':'Implement the complete declared module.'} for k,v in rules.items()]}
    return result(value,prompt_sha256=manifest.identity(request['messages']))


def drive(queued,failure=None):
    j,_,_=queued;transport=DockerProtocol(j,'queue-auth-exec-r0',failure)
    return queue.run(j,'queue',caller=caller,transport=transport),transport


def test_queue_binds_six_original_scopes_but_only_dispatches_supported_auth(queued):
    j,row,_=queued;assert len(row['items'])==6 and row['binding']['supported_dispatch']==['auth-session-core']
    assert row['binding']['deployment_authorized'] is False
    assert row['items'][3]['depends_on']==['owner-profile']
    assert row['items'][5]['depends_on']==[]
    assert all(not item['full_product_acceptance'] for item in row['items'])
    assert all(not item['supported'] for item in row['items'][1:])


def test_contract_cannot_get_a_new_queue_id_or_budget_after_creation(queued):
    j,row,_=queued;resources=row['binding']['resources']
    with pytest.raises(ValueError,match='already queued'):queue.create(j,'reset',resources['cache'],resources['tls_directory'],resources['nss_deb'])
    assert queue.create(j,'queue',resources['cache'],resources['tls_directory'],resources['nss_deb'])==row


def test_simulated_queue_generates_reviews_and_runs_fixed_original_recipe(queued):
    j,_,_=queued;row,transport=drive(queued)
    assert row['state']=='awaiting_supported_increment',row['reason']
    assert row['items'][0]['state']=='qualified_session_core' and row['product_tests_executed']
    assert not row['full_product_acceptance'] and len(row['operations'])==3
    assert row['used']['executions']==1 and row['used']['calls']==6
    assert not transport.images and not transport.containers
    queue.verify(j,row)
    calls=len(transport.calls);again=queue.run(j,'queue',caller=caller,transport=transport)
    assert again==row and len(transport.calls)==calls
    assert all(i['state']=='unsupported' for i in row['items'][1:])


def test_product_failure_goes_to_agents_and_stops_for_audit_at_three_completed_attempts(queued):
    j,_,_=queued;row,transport=drive(queued,'api')
    assert row['state']=='awaiting_flow_audit' and row['review_index']==1 and row['round']==1
    assert row['pending_audit']['packet']['through_round']==3
    assert row['pending_audit']['packet']['product_feedback']['stage']=='unit'
    assert row['used']['executions']==1 and row['used']['calls']==8
    digest=row['pending_audit']['sha256']
    continued=queue.acknowledge_audit(j,'queue',digest,'continue','Inspect original failure, corrections, usage and isolation.')
    assert continued['last_audited_round']==3
    final=queue.run(j,'queue',caller=caller,transport=transport)
    assert final['state']=='awaiting_discrepancy' and 'unchanged' in final['reason']
    assert final['used']['executions']==1 and not transport.containers and not transport.images
    assert queue.acknowledge_audit(j,'queue',digest,'continue','Inspect original failure, corrections, usage and isolation.')==final
    with pytest.raises(ValueError,match='replay'):queue.acknowledge_audit(j,'queue',digest,'discrepancy','changed')


def test_budget_blocks_before_any_new_model_call(queued,monkeypatch):
    j,_,_=queued
    monkeypatch.setattr(queue,'LIMITS',queue.LIMITS|{'calls':4})
    amount={'calls':5,'input_tokens':163840,'output_tokens':20000,'active_ms':600000}
    assert queue.reserve(j,'queue','held','review',amount) is None
    row=queue.get(j,'queue');assert row['state']=='blocked_budget' and row['used']['calls']==0


def test_model_uncertainty_keeps_parent_reservation_and_never_resends(queued):
    j,_,_=queued;calls=[]
    def uncertain(*args):calls.append(1);return {'ok':False,'sent':True,'error_type':'transport_timeout'}
    row=queue.run(j,'queue',caller=uncertain)
    assert row['state']=='uncertain_operation' and len(calls)==1
    assert row['operations'][0]['state']=='in_flight' and row['used']['calls']==5
    assert queue.run(j,'queue',caller=uncertain)==row and len(calls)==1


def test_restart_of_child_in_flight_does_not_resend_model(queued):
    j,_,_=queued;rid='queue-r0-auth-api'
    seed={'path':auth.PATHS['auth_api'],'content':''}
    queue.reserve(j,'queue',rid,'review',{'calls':5,'input_tokens':163840,'output_tokens':20000,'active_ms':600000})
    j.create(rid,seed,section='auth_api');j.reserve(rid)
    row=queue.run(j,'queue',caller=lambda *a:pytest.fail('No replay'))
    assert row['state']=='uncertain_operation' and row['used']['active_ms']==600000


@pytest.mark.parametrize('mutation',['binding','accounting','source_identity','resource','original'])
def test_drift_blocks_dispatch_and_preserves_originals(queued,monkeypatch,mutation):
    j,_,recipe=queued
    if mutation=='original':
        row,_=drive(queued,'api');queue.acknowledge_audit(j,'queue',row['pending_audit']['sha256'],'continue','checked')
        with j.transaction() as db:
            child=manifest.parse(db.execute('SELECT body FROM plan_runs WHERE id=?',(row['operations'][0]['child_id'],)).fetchone()[0]);child['candidate']['content']+='drift'
            db.execute('UPDATE plan_runs SET body=? WHERE id=?',(manifest.canonical(child),child['id']))
    elif mutation=='source_identity':monkeypatch.setattr(controller_gate,'require_green',lambda:'new-controller')
    elif mutation=='resource':recipe['tls_public']['ca_sha256']='b'*64
    else:
        with j.transaction() as db:
            row=queue.load(db,'queue')
            if mutation=='binding':row['binding']['review_every']=0
            else:row['used']['calls']=1
            queue.save(db,row)
    row=queue.run(j,'queue',caller=lambda *a:pytest.fail('Drift must prevent dispatch'))
    assert row['state']=='blocked_drift'


def test_recovery_of_invalid_complete_review_is_charged_then_audited(queued):
    j,_,_=queued;bad=[]
    def call(row,op,lock,docs):
        if not bad and op['role']=='reviewer':
            bad.append(1)
            request={'model':row['binding']['model'],'digest':row['binding']['digest'],
              'messages':[{'role':'user','content':roles.prompt(row,op,lock,docs)}],'format':review.output_schema(row,op,lock,docs)}
            op['_record_request'](request)
            value=accepted({'content':markers('auth_api')},'auth_api')
            return result(value,prompt_sha256=manifest.identity(request['messages']))
        return caller(row,op,lock,docs)
    row=queue.run(j,'queue',caller=call)
    assert row['state']=='awaiting_flow_audit' and row['used']['calls']==7
    assert len(row['operations'])==3 and row['used']['executions']==0
    assert row['operations'][0]['child_state']=='blocked_evidence'
    assert row['pending_audit']['packet']['through_round']==3


def test_false_success_without_original_api_browser_reports_cannot_accept():
    assert not queue.accepted({'state':'qualified_session_core','product_tests_executed':True})


def test_acceptance_packet_cannot_grant_unsupported_scopes_or_full_ui_criteria(queued):
    j,_,_=queued;row,_=drive(queued)
    assert row['items'][0]['criteria_status']=='scoped_session_tests_passed_not_full_criterion_coverage'
    assert 'phone' in row['items'][0]['ui_acceptance'][0]
    assert all(i['criteria_status']=='specified_not_executed' for i in row['items'][1:])


def test_queue_export_keeps_original_operations_and_does_not_claim_full_product(queued,tmp_path):
    j,_,_=queued;row,_=drive(queued)
    path=tmp_path/'export.json';queue.export(j,'queue',path);value=json.loads(path.read_bytes())
    assert value['queue']==row and len(value['originals'])==3
    assert value['originals'][row['operations'][0]['child_id']]['operations']
    assert not value['full_product_acceptance']


@pytest.mark.parametrize('boundary',['review','execution'])
def test_restart_after_parent_settlement_advances_without_repeating_child(queued,monkeypatch,boundary):
    j,_,_=queued;original=queue.change;interrupted=[];transport=DockerProtocol(j,'queue-auth-exec-r0')
    def interrupt(journal,rid,event=None,**changes):
        target=changes.get('review_index')==1 if boundary=='review' else 'product_tests_executed' in changes
        if target and not interrupted:interrupted.append(1);raise RuntimeError('Simulated process interruption after durable settlement')
        return original(journal,rid,event,**changes)
    monkeypatch.setattr(queue,'change',interrupt)
    with pytest.raises(RuntimeError):queue.run(j,'queue',caller=caller,transport=transport)
    before=queue.get(j,'queue');assert before['operations'][-1]['state']=='confirmed'
    calls=len(transport.calls);monkeypatch.setattr(queue,'change',original)
    row=queue.run(j,'queue',caller=caller,transport=transport)
    assert row['state']=='awaiting_supported_increment' and row['used']['calls']==6 and row['used']['executions']==1
    if boundary=='execution':assert len(transport.calls)==calls


def test_terminal_third_attempt_is_audited_before_escalation_without_more_calls(queued):
    j,_,_=queued;calls=[];transport=DockerProtocol(j,'queue-auth-exec-r0','api')
    def call(row,op,lock,docs):
        calls.append(row['id'])
        if row['binding'].get('product_feedback'):
            request={'model':row['binding']['model'],'digest':row['binding']['digest'],
              'messages':[{'role':'user','content':roles.prompt(row,op,lock,docs)}],'format':review.output_schema(row,op,lock,docs)}
            op['_record_request'](request)
            return result({'malformed':'complete but invalid proposal'},prompt_sha256=manifest.identity(request['messages']))
        return caller(row,op,lock,docs)
    row=queue.run(j,'queue',caller=call,transport=transport)
    assert row['state']=='awaiting_flow_audit' and row['pending_audit']['packet']['through_round']==3
    assert row['pending_audit']['packet']['pending_discrepancy']['child_id']=='queue-r1-auth-api'
    n=len(calls);queue.acknowledge_audit(j,'queue',row['pending_audit']['sha256'],'continue','Inspected invalid original; preserve and escalate discrepancy.')
    final=queue.run(j,'queue',caller=call,transport=transport)
    assert final['state']=='awaiting_discrepancy' and len(calls)==n


def test_unsupported_scope_cannot_be_marked_accepted_in_mutable_queue_state(queued):
    j,_,_=queued
    with j.transaction() as db:
        row=queue.load(db,'queue');row['items'][2]['state']='qualified_session_core';queue.save(db,row)
    row=queue.run(j,'queue',caller=lambda *a:pytest.fail('No dispatch from forged acceptance'))
    assert row['state']=='blocked_drift'


def test_reservation_cannot_reduce_real_worker_cost(queued):
    j,_,_=queued
    with pytest.raises(ValueError,match='fixed child recipe'):queue.reserve(j,'queue','low-cost','review',{'calls':1,'active_ms':1})
    assert not queue.get(j,'queue')['operations']


def test_malformed_or_duplicate_cleanup_success_is_rejected(queued):
    j,_,_=queued;row,_=drive(queued);child=ledger.get(j,row['items'][0]['execution_id'])
    broken=copy.deepcopy(child);broken['browser_report']='invalid JSON'
    assert not queue.accepted(broken)
    broken=copy.deepcopy(child);broken['cleanup'][-1]=copy.deepcopy(broken['cleanup'][0])
    assert not queue.accepted(broken)
