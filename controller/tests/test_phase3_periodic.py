"""Periodic operator audits must neither resend calls nor override evidence gates."""
import copy
import pytest
import manifest
import phase3_workflow as workflow
import phase3_review as review
import phase3_source as source
from test_phase3_workflow import flow


def enable(j):
    row=workflow.get(j,'flow');row['binding']['review_every']=3
    row['binding_sha256']=manifest.identity(row['binding']);workflow.save(j,row)


def acknowledge(j,row,decision='continue'):
    return workflow.acknowledge_audit(j,'flow',row['pending_audit']['sha256'],decision,'Reviewed originals, budget and gates; no new authority.')


def test_audits_at_three_and_six_rounds_resume_without_new_permission_or_duplicate_work(flow):
    j,_,seen,dispatched,dispatch,*_=flow;enable(j)
    first=workflow.run(j,'flow',dispatcher=dispatch)
    assert first['state']=='awaiting_flow_audit' and len(seen)==3 and not dispatched
    assert first['pending_audit']['packet']['through_round']==3
    assert workflow.run(j,'flow',dispatcher=dispatch)==first and len(seen)==3
    used=copy.deepcopy(first['used']);released=acknowledge(j,first)
    assert released['state']=='active' and released['used']==used
    assert acknowledge(j,first)==released # Idempotent audit acknowledgement.
    second=workflow.run(j,'flow',dispatcher=dispatch)
    assert second['state']=='awaiting_flow_audit' and len(seen)==6 and not dispatched
    assert second['pending_audit']['packet']['from_round']==4
    acknowledge(j,second)
    completed=workflow.run(j,'flow',dispatcher=dispatch)
    assert completed['state']=='completed_slice' and len(seen)==8 and len(dispatched)==1
    assert completed['last_audited_round']==6 and len(completed['audits'])==2
    assert completed['used']['calls']==8 and completed['used']['executions']==1


def test_stale_or_altered_packet_cannot_resume_agents(flow):
    j,_,seen,_,dispatch,*_=flow;enable(j)
    row=workflow.run(j,'flow',dispatcher=dispatch)
    with pytest.raises(ValueError):workflow.acknowledge_audit(j,'flow','0'*64,'continue','Checked')
    assert workflow.get(j,'flow')==row and len(seen)==3
    row['pending_audit']['packet']['used']['calls']=0;workflow.save(j,row)
    with pytest.raises(ValueError):acknowledge(j,row)
    assert workflow.get(j,'flow')['state']=='awaiting_flow_audit'


def test_operator_discrepancy_stops_with_evidence_and_preserves_budget(flow):
    j,_,seen,dispatched,dispatch,*_=flow;enable(j)
    row=workflow.run(j,'flow',dispatcher=dispatch);used=copy.deepcopy(row['used'])
    stopped=acknowledge(j,row,'discrepancy')
    assert stopped['state']=='awaiting_discrepancy' and stopped['used']==used
    assert stopped['attention']['review_ids']==row['children']
    assert workflow.run(j,'flow',dispatcher=dispatch)==stopped and len(seen)==3 and not dispatched


def test_modified_original_and_counter_cannot_fake_a_completed_inspection(flow):
    j,_,_,_,dispatch,*_=flow;enable(j)
    row=workflow.run(j,'flow',dispatcher=dispatch)
    child=row['pending_audit']['packet']['originals'][0]['id']
    j.change(child,'blocked_evidence','Tampered original')
    with pytest.raises(ValueError):acknowledge(j,row)
    row['last_audited_round']=3;workflow.save(j,row)
    with pytest.raises(ValueError):workflow.run(j,'flow',dispatcher=dispatch)


def test_completed_audit_cannot_be_rewritten_and_never_resets_usage(flow):
    j,_,_,_,dispatch,*_=flow;enable(j)
    row=workflow.run(j,'flow',dispatcher=dispatch);acknowledge(j,row)
    with pytest.raises(ValueError):workflow.acknowledge_audit(j,'flow',row['pending_audit']['sha256'],'continue','Different summary')
    with pytest.raises(ValueError):workflow.acknowledge_audit(j,'flow',row['pending_audit']['sha256'],'discrepancy','Reviewed originals, budget and gates; no new authority.')


def test_general_agent_routes_periodic_audit(monkeypatch):
    import agent,controller_cli
    seen=[];monkeypatch.setattr(controller_cli,'main',lambda args:seen.append(args) or 0)
    assert agent.main(['phase3-flow-audit'])==0 and seen==[['phase3-flow-audit']]


def test_test_correction_keeps_original_usage_and_audit_origin(flow):
    j,files,_,_,dispatch,_,fingerprint,*_=flow;enable(j)
    parent=acknowledge(j,workflow.run(j,'flow',dispatcher=dispatch),'discrepancy')
    original=manifest.identity(parent)
    path=next(p for p in files if p.startswith('backend/tests/') and p.endswith('.test.ts'))
    files[path]=b'corrected assertion';fingerprint['files'][path]=manifest.identity(files[path].hex())
    amendment={'reason':'Correct operator test expectation','paths':[path],
               'parent_fingerprint_sha256':manifest.identity(parent['binding']['workspace'])}
    child=workflow.create(j,'corrected',fingerprint['root'],parent['binding']['plan'],parent['binding']['cache'],
                          parent_id='flow',review_every=3,input_amendment=amendment)
    assert child['used']==parent['used'] and child['operations']==parent['operations']
    assert child['audit_start']==len(parent['operations']) and workflow.closed_rounds(child)==[]
    assert manifest.identity(workflow.get(j,'flow'))==original
    assert workflow.verify(child)[path]==b'corrected assertion'


@pytest.mark.parametrize('path',['frontend/src/PublicProfile.tsx','package-lock.json'])
def test_amendment_cannot_change_product_or_dependency_inputs(path):
    old={'root':'old','files':{path:'original'},'bytes':1}
    parent={'binding':{'workspace':old}}
    amendment={'reason':'Operator correction','paths':[path],'parent_fingerprint_sha256':manifest.identity(old)}
    with pytest.raises(ValueError):workflow.validate_amendment(parent,{'files':{path:'changed'}},amendment)


def test_amendment_requires_exact_changed_set_and_parent_identity():
    path='frontend/e2e/race.spec.ts';other='backend/tests/contract.test.ts'
    old={'root':'old','files':{path:'old',other:'same'},'bytes':1};parent={'binding':{'workspace':old}}
    amendment={'reason':'Correct assertion','paths':[path],'parent_fingerprint_sha256':manifest.identity(old)}
    workflow.validate_amendment(parent,{'files':{path:'new',other:'same'}},amendment)
    for candidate in ({path:'new',other:'also changed'},{path:'new'},{path:'old',other:'same'}):
        with pytest.raises(ValueError):workflow.validate_amendment(parent,{'files':candidate},amendment)
    amendment['parent_fingerprint_sha256']='0'*64
    with pytest.raises(ValueError):workflow.validate_amendment(parent,{'files':{path:'new',other:'same'}},amendment)


def test_unchanged_agent_outputs_after_failure_stop_before_another_execution(flow,monkeypatch):
    j,_,_,dispatched,_,result,_,good=flow
    def unchanged(j,run,**kw):
        original=j.get(run)['candidate'];child=good(j,run,**kw)
        child['candidate']=original;child['candidate_sha256']=manifest.identity(original)
        with j.transaction() as db:j.save(db,child,{'kind':'mock.unchanged'})
        return j.get(run)
    monkeypatch.setattr(review,'run',unchanged)
    def dispatch(j,run,*a,**kw):dispatched.append(run);return result(run,False)
    row=workflow.run(j,'flow',dispatcher=dispatch)
    assert row['state']=='awaiting_discrepancy' and 'identical sources' in row['reason']
    assert len(dispatched)==1 and row['used']['executions']==1
    assert workflow.run(j,'flow',dispatcher=dispatch)==row and len(dispatched)==1


def test_failure_prompt_requires_diagnosis_despite_empty_documentary_findings():
    row={'binding':{'section':'public_profile','product_feedback':{'execution_id':'verified-failure',
         'execution_sha256':'a'*64,'stage':'e2e','output':'Assertion failed after obsolete retry'}},
         'candidate':{'path':source.PATHS['public_profile'],'content':'source'},'findings':[]}
    text=source.prompt(row,{'role':'reviewer'},{'contract.json':{'api':{'dtos':{'PublicProfile':{},'Photo':{}}}}})
    assert 'confirmed product check FAILED' in text and 'reject unresolved failed behavior' in text


def test_aggregate_execution_limit_stops_before_another_repair_round(flow,monkeypatch):
    j,_,seen,dispatched,_,result,*_=flow
    monkeypatch.setattr(workflow,'LIMITS',workflow.LIMITS|{'executions':1})
    row=workflow.get(j,'flow');row['binding']['limits']=workflow.LIMITS
    row['binding_sha256']=manifest.identity(row['binding']);workflow.save(j,row)
    def dispatch(j,run,*a,**kw):dispatched.append(run);return result(run,False)
    row=workflow.run(j,'flow',dispatcher=dispatch)
    assert row['state']=='awaiting_discrepancy' and len(seen)==len(workflow.KINDS)
    assert len(dispatched)==1 and row['round']==0 and row['used']['executions']==1
