import copy
from pathlib import Path
import pytest
import controller_gate
import manifest
import phase3_prepare as preparation
import phase3_review as review
import phase3_contract as contract
from phase3_roles import prompt
from phase3_studio import StudioObserver


@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setattr(review,'ROOT',tmp_path)
    monkeypatch.setattr(controller_gate,'require_green',lambda:'frozen-source')
    lock,docs,_=preparation.load_bundle()
    return review.Journal(tmp_path/'journal.sqlite'),lock,docs


def approved(candidate,section,docs,failed=()):
    cards=contract.evidence(candidate,section,docs)
    checks={}
    for rule in contract.rules(section):
        ids={source:next(k for k,c in cards.items() if c['rule']==rule and (c['source']=='candidate')==is_candidate)
             for source,is_candidate in [('candidate_evidence',True),('accepted_evidence',False)]}
        checks[rule]={'passed':rule not in failed,**ids}
    return {'checks':checks,'findings':[{'rule':r,'issue':'Concrete synthetic deviation','fix':'Restore accepted field'} for r in failed]}


def result(value,**changes):
    return {'ok':True,'done':True,'done_reason':'stop','tool_calls':False,
            'model_digest':'25b843619e944cd0ae6069f94ff4e5e26a16e109ccbc0a66a0f05979ed70098e',
            'text':manifest.canonical(value),'input_tokens':40,'output_tokens':30,**changes}


@pytest.mark.parametrize('section',contract.SECTIONS)
def test_accepted_sections_have_no_deterministic_rejections_and_bound_citations(env,section):
    _,_,docs=env;candidate=docs['contract.json'][section]
    assert contract.defects(candidate,section,docs)==[]
    checked=contract.validate_review(approved(candidate,section,docs),candidate,section,docs)
    assert all(c['accepted_evidence']['source']=='contract.json' for c in checked['resolved_citations'].values())
    for cards in checked['resolved_citations'].values():
        for card in cards.values():
            value=review.resolve_pointer(candidate,card['pointer'])
            assert card['value_sha256']==manifest.identity(value)
            assert card['quote'] in (value if isinstance(value,str) else manifest.canonical(value))


@pytest.mark.parametrize('section,path,value,rule,code',[
    ('api','/routes/18/response/status','$status','API01','undefined_dto'),
    ('api','/dtos/PublicCard/email','string','API01','private_public_field'),
    ('api','/routes/0/rule','Apply filters to promoted only','API03','frozen_clause_changed'),
    ('api','/routes/18/status',201,'API02','route_access_status'),
    ('data','/tables/idempotency/primary_key',['key'],'DATA02','idempotency_scope'),
    ('data','/tables/idempotency/unique',[['payload_hash']],'DATA02','global_idempotency_unique'),
    ('data','/tables/idempotency/columns/payload_hash','text UNIQUE','DATA02','global_idempotency_unique'),
    ('data','/tables/zones/foreign_keys',['(country_id,province_id) REFERENCES provinces(id,country_id)'],'DATA01','accepted_fk_missing'),
    ('data','/tables/profiles/foreign_keys',[],'DATA01','accepted_fk_missing'),
    ('data','/main_photo_constraint','Add the missing main-photo constraint','DATA03','frozen_clause_changed'),
    ('subscriptions','/idempotency/ttl_hours',0,'SUB02','frozen_clause_changed'),
    ('subscriptions','/production_guard','check_client_simulated','SUB01','frozen_clause_changed'),
    ('subscriptions','/real_billing','Implement provider now','SUB03','frozen_clause_changed'),
    ('manifest','/commands/e2e/cwd','backend','MAN02','frozen_clause_changed'),
    ('manifest','/commands/unit/argv',['cmd','/c','bad'],'MAN02','frozen_clause_changed'),
    ('manifest','/enabled',True,'MAN01','frozen_clause_changed'),
    ('manifest','/note','Require separate backend E2E','MAN03','frozen_clause_changed'),
])
def test_concrete_mutations_are_actionable_with_exact_rule_citations(env,section,path,value,rule,code):
    _,_,docs=env;candidate=copy.deepcopy(docs['contract.json'][section])
    parent,leaf=path.rsplit('/',1);target=review.resolve_pointer(candidate,parent)
    target[int(leaf) if isinstance(target,list) else leaf]=value
    findings=contract.defects(candidate,section,docs)
    assert any(f['rule']==rule and f['issue']==code for f in findings)
    assert all(f['fix'] and f['rule_quote']==contract.rules(section)[f['rule']] for f in findings)


def test_shape_error_never_hides_unrelated_fk_and_idempotency_failures(env):
    _,_,docs=env;c=copy.deepcopy(docs['contract.json']['data'])
    c['restart']=None;c['tables']['zones']['foreign_keys']=['province_id REFERENCES absent(id)']
    c['tables']['idempotency']['unique']=[['key']]
    findings=contract.defects(c,'data',docs)
    assert {'schema.type','invalid_fk','global_idempotency_unique'}<={f['issue'] for f in findings}


def test_nullable_array_empty_requests_and_health_primitive_are_valid(env):
    _,_,docs=env;c=copy.deepcopy(docs['contract.json']['api'])
    c['dtos']['OwnProfile']['photos']='$Photo[]|null'
    assert contract.defects(c,'api',docs)==[]
    assert c['routes'][6]['request']=={} and c['routes'][18]['response']['status']=='string: ok'


def test_unrelated_rule_or_stale_candidate_citation_cannot_approve(env):
    _,_,docs=env;c=docs['contract.json']['subscriptions'];body=approved(c,'subscriptions',docs)
    body['checks']['SUB01']['candidate_evidence']=body['checks']['SUB03']['candidate_evidence']
    with pytest.raises(Exception):contract.validate_review(body,c,'subscriptions',docs)
    body=approved(c,'subscriptions',docs);changed=copy.deepcopy(c);changed['production_guard']='check_client_simulated'
    with pytest.raises(Exception):contract.validate_review(body,changed,'subscriptions',docs)


def test_invented_business_rule_and_findings_for_passed_check_block(env):
    _,_,docs=env;c=docs['contract.json']['subscriptions'];body=approved(c,'subscriptions',docs)
    body['findings']=[{'rule':'SUB03','issue':'Need infinite TTL','fix':'Change business'}]
    with pytest.raises(ValueError,match='disagree'):contract.validate_review(body,c,'subscriptions',docs)
    body['findings'][0]['rule']='NEW_BUSINESS_REQUIREMENT'
    with pytest.raises(Exception):contract.validate_review(body,c,'subscriptions',docs)


def test_false_approval_veto_full_agent_section_replacement_then_acceptance(env):
    journal,_,docs=env;good=docs['contract.json']['data'];bad=copy.deepcopy(good)
    bad['tables']['idempotency']['unique']=[['payload_hash']]
    journal.create('section-cycle',bad,section='data');seen=[]
    def call(row,op,*args):
        seen.append(op['role'])
        if op['role']=='developer':
            assert any(f['issue']=='global_idempotency_unique' for f in row['findings'])
            return result(good)
        return result(approved(row['candidate'],'data',docs))
    row=review.run(journal,'section-cycle',call)
    assert seen==['reviewer','developer','reviewer'] and row['state']=='reviewed_contract_section'
    assert row['candidate']==good and row['corrections']==1 and not row['product_tests_executed']
    ops=journal.records(row['id'],'plan_ops')
    assert ops[0]['candidate']==bad and ops[0]['validated_review']['resolved_citations']
    assert ops[1]['result']['text']==manifest.canonical(good)
    assert review.run(review.Journal(journal.path),row['id'],lambda *args:pytest.fail('Resent'))==row


def test_copied_instruction_remains_defect_until_bounded_stop(env):
    journal,_,docs=env;c=copy.deepcopy(docs['contract.json']['data'])
    c['main_photo_constraint']='Please restore the missing unique main photo index.'
    journal.create('copied-instruction',c,section='data')
    row=review.run(journal,'copied-instruction',lambda row,op,*args:result(row['candidate'] if op['role']=='developer' else approved(row['candidate'],'data',docs)))
    assert row['state']=='blocked_budget' and row['calls']==5 and row['corrections']==2
    assert any(f['pointer']=='/main_photo_constraint' for f in row['findings'])


def test_restart_checks_contract_source_identity_before_continuing(env):
    journal,_,docs=env;c=docs['contract.json']['data'];row=journal.create('restart',c,section='data')
    journal.reserve('restart')
    restarted=review.Journal(journal.path)
    row=review.run(restarted,row['id'],lambda *args:pytest.fail('Uncertain inference resent'))
    assert row['state']=='uncertain_operation' and row['calls']==1
    row=journal.create('input-drift',c,section='data')
    Path(row['snapshot_directory'],'contract.json').write_text('{}')
    row=review.run(review.Journal(journal.path),row['id'],lambda *args:pytest.fail('Drift dispatched'))
    assert row['state']=='blocked_evidence' and row['calls']==0


def test_observer_failure_does_not_change_control_or_lose_evidence(env):
    journal,_,docs=env;c=docs['contract.json']['data'];journal.create('observer',c,section='data')
    def observer(*args):raise OSError('Studio temporarily unavailable')
    row=review.run(journal,'observer',lambda *args:result(approved(c,'data',docs)),observer)
    assert row['state']=='reviewed_contract_section' and row['calls']==1
    assert any(e['kind']=='observer.failed' for e in journal.records(row['id'],'plan_events'))


def test_compact_prompt_preserves_first_and_last_corrections(env):
    journal,lock,docs=env;c=docs['contract.json']['data'];row=journal.create('feedback',c,section='data')
    row['findings']=[{'issue':'first'},{'issue':'last'}]
    text=prompt(row,{'role':'developer'},lock,docs)
    assert 'first' in text and 'last' in text and 'ENTIRE' in text and 'evidence_cards' not in text


def test_studio_tracks_doc_acceptance_and_confirmed_usage_without_product_claims(env):
    journal,_,docs=env;c=docs['contract.json']['data'];journal.create('studio',c,section='data')
    class Store:
        row=None;events=[]
        def get_run(self,*args):return self.row
        def create_run(self,r):self.row=r
        def update_run(self,r):self.row=r
        def get_events(self,*args):return self.events
        def append_event(self,run_id,e):self.events.append(e)
    store=Store();observer=StudioObserver(store)
    row=review.run(journal,'studio',lambda *args:result(approved(c,'data',docs)),observer)
    observer(journal,row)
    assert store.row['status']=='succeeded' and store.row['tokens']==70
    assert 'Producto y comandos no ejecutados' in store.row['result'] and 'data' in store.row['result']
    assert len(store.events)==len(journal.records(row['id'],'plan_events'))


def test_compact_accepted_context_is_lossless_and_fits_api_call_bound(env):
    journal,lock,docs=env
    source=docs['contract.json']['api'];candidate=copy.deepcopy(source)
    candidate['routes'].append(copy.deepcopy(source['routes'][6]))
    candidate['dtos']['OwnProfile']['photos']='$Photo[]|null'
    candidate['extra']='synthetic'
    changes=contract.accepted_delta(candidate,source)
    reconstructed=copy.deepcopy(candidate)
    for change in changes:
        parent,leaf=change['path'].rsplit('/',1);target=review.resolve_pointer(reconstructed,parent)
        key=int(leaf) if isinstance(target,list) else leaf
        if change['op']=='remove':del target[key]
        elif change['op']=='add' and isinstance(target,list):target.insert(key,change['value'])
        else:target[key]=change['value']
    assert reconstructed==source
    row=journal.create('compact-api',candidate,section='api')
    text=prompt(row,{'role':'reviewer'},lock,docs)
    # CrewAI adds its own role/format wrapper, so retain conservative headroom.
    assert len(text.encode())+2000+1024+4000<row['binding']['limits']['context_tokens']
    assert 'not automatically defects' in text


def test_fixture_evaluation_is_terminal_separate_from_document_approval_and_golden_is_not_in_prompt(env):
    journal,lock,docs=env;c=docs['contract.json']['data']
    expected={k:False for k in contract.rules('data')}
    row=journal.create('fixture',c,section='data',expected_checks=expected)
    assert 'expected_checks' not in prompt(row,{'role':'reviewer'},lock,docs)
    row=review.run(journal,row['id'],lambda *args:result(approved(c,'data',docs)))
    assert row['scope']=='contract_fixture_evaluation' and row['state']=='evaluated_fixture'
    assert row['fixture_passed'] is False and row['calls']==1 and row['corrections']==0
    assert review.run(journal,row['id'],lambda *args:pytest.fail('Fixture retried'))==row
