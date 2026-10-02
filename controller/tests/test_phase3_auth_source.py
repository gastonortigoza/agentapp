import copy
import json
import pytest
from jsonschema import ValidationError
import controller_gate
import manifest
import phase3_auth_source as auth
import phase3_source as source
import phase3_review as review
import phase3_roles as roles
import phase3_handoff as handoff
import phase3_prepare as preparation
import phase3_workflow as workflow
from test_phase3_review import result


def markers(section):
    # Deliberately non-executable: documentary marker checks must never imply product success.
    return ('registerAuth requireOwner AuthConfig /api/auth/register /api/auth/login /api/auth/refresh /api/auth/logout '
      '@node-rs/argon2 Argon2id 65536 memoryCost timeCost parallelism birth_date America/Argentina/Buenos_Aires '
      'jose jwtVerify invalid_credentials revoked_at expires_at randomBytes sha256 family_id setCookie clearCookie '
      'BEGIN COMMIT ROLLBACK FOR UPDATE') if section=='auth_api' else (
      'AuthPages onSession /api/auth/register /api/auth/login birth_date AbortController aria-busy alert')

def accepted(candidate,section):
    return {'checks':{k:{'passed':True,'pointer':'/content','quote':candidate['content'][:15]} for k in auth.rules(section)},'findings':[]}

@pytest.mark.parametrize('section',auth.SECTIONS)
def test_auth_registered_source_corrects_seed_keeps_original_and_never_authorizes_product(tmp_path,monkeypatch,section):
    monkeypatch.setattr(controller_gate,'require_green',lambda:'qualified-controller')
    monkeypatch.setattr(review,'ROOT',tmp_path)
    journal=review.Journal(tmp_path/'review.sqlite')
    seed={'path':auth.PATHS[section],'content':'incomplete original'}
    gold={'path':auth.PATHS[section],'content':markers(section)}
    journal.create('auth-review',seed,section=section)
    lock,docs,_=preparation.load_bundle()
    def call(row,op,*args):
        instruction=roles.prompt(row,op,lock,docs)
        assert auth.CONTRACT_SHA in instruction and 'SOURCE' in instruction
        request={'model':row['binding']['model'],'digest':row['binding']['digest'],
          'messages':[{'role':'user','content':instruction}],'format':review.output_schema(row,op,lock,docs)}
        op['_record_request'](request)
        body=gold if op['role']=='developer' else accepted(row['candidate'],section)
        return result(body,prompt_sha256=manifest.identity(request['messages']))
    final=review.run(journal,'auth-review',call)
    assert final['state']=='reviewed_application_file' and final['candidate']==gold
    assert final['calls']==3 and final['corrections']==1
    assert final['execution_authorized'] is False and final['product_tests_executed'] is False
    assert journal.records('auth-review','plan_ops')[0]['candidate']==seed
    assert handoff.consume(journal,'auth-review',section)[0]==final
    with pytest.raises(ValueError):handoff.consume(journal,'auth-review','public_api')
    assert review.run(journal,'auth-review',lambda *a:pytest.fail('Terminal review resent'))==final

@pytest.mark.parametrize('section',auth.SECTIONS)
def test_wrong_path_is_rejected_even_if_content_has_all_markers(section):
    candidate={'path':'backend/tests/replace.test.ts','content':markers(section)}
    assert auth.defects(candidate,section)
    with pytest.raises(ValidationError):
        from jsonschema import Draft202012Validator
        Draft202012Validator(source.writer_schema(section)).validate(candidate)

@pytest.mark.parametrize('section',auth.SECTIONS)
def test_auth_evidence_and_exact_rule_citations_cannot_be_forged(section):
    candidate={'path':auth.PATHS[section],'content':markers(section)}
    value=accepted(candidate,section)
    first=next(iter(auth.rules(section)))
    fabricated=copy.deepcopy(value);fabricated['checks'][first]['quote']='not present original source'
    with pytest.raises(ValueError,match='Invented'):source.validate_review(fabricated,candidate,section)
    fabricated=copy.deepcopy(value);fabricated['checks'][first]['quote']=''
    with pytest.raises(ValueError,match='requires evidence'):source.validate_review(fabricated,candidate,section)
    fabricated=copy.deepcopy(value);fabricated['checks'][first]['passed']=False
    with pytest.raises(ValueError,match='disagree'):source.validate_review(fabricated,candidate,section)
    fabricated['findings']=[{'rule':first,'source':auth.SOURCE,'rule_quote':'invented rule','pointer':'/content','issue':'missing','fix':'Correct module'}]
    with pytest.raises(ValueError,match='citation'):source.validate_review(fabricated,candidate,section)

@pytest.mark.parametrize('section,bad',[(s,b) for s in auth.SECTIONS for b in (
 'import {exec} from "node:child_process";', 'import fs from "node:fs";', 'process.env.SECRET', 'console.log(token)', 'eval(input)')])
def test_host_access_and_credential_logging_are_vetoed(section,bad):
    assert auth.defects({'path':auth.PATHS[section],'content':markers(section)+' '+bad},section)

@pytest.mark.parametrize('bad',['localStorage.setItem("token",token)','sessionStorage.token=token','indexedDB.open("auth")','document.cookie=token','dangerouslySetInnerHTML'])
def test_frontend_persistence_and_raw_html_are_vetoed(bad):
    findings=auth.defects({'path':auth.PATHS['auth_pages'],'content':markers('auth_pages')+' '+bad},'auth_pages')
    assert any(f['rule']=='AU02' for f in findings)

@pytest.mark.parametrize('func',[source.rules,source.writer_schema,source.review_schema])
def test_unknown_source_section_fails_closed(func):
    with pytest.raises(ValueError,match='Unsupported'):func('unregistered_privileged_module')

def test_auth_registry_does_not_expand_directory_execution_or_its_frozen_paths():
    assert source.SECTIONS==('application','public_api','public_profile')
    assert not set(auth.SECTIONS)&set(workflow.KINDS)
    assert not set(auth.PATHS.values())&set(source.PATHS.values())
    assert set(auth.SECTIONS)<=set(review.SOURCE_SECTIONS)
    for section in auth.SECTIONS:
        assert all(len(rule)<=1000 for rule in auth.rules(section).values())
    lock,docs,_=preparation.load_bundle()
    assert lock['files']['contract.json']==auth.CONTRACT_SHA
    # Refresh errors remain the frozen route set; no new403 contract status introduced.
    refresh=next(r for r in docs['contract.json']['api']['routes'] if r['path']=='/api/auth/refresh')
    assert refresh['errors']==[401,429,503]

def test_product_feedback_reaches_auth_writer_and_reviewer_with_fixed_test_constraint():
    _,docs,_=preparation.load_bundle()
    feedback={'execution_id':'auth-failure','execution_sha256':'0'*64,'stage':'unit','output':'Concurrent reuse left a live session'}
    row={'binding':{'section':'auth_api','product_feedback':feedback},'candidate':{'path':auth.PATHS['auth_api'],'content':markers('auth_api')},'findings':[]}
    for role in ('developer','reviewer'):
        prompt=source.prompt(row,{'role':role},docs)
        assert 'Concurrent reuse left a live session' in prompt and 'preserve tests/dependencies' in prompt


@pytest.mark.parametrize('section',auth.SECTIONS)
def test_absent_module_starts_with_original_writer_without_invented_failure(tmp_path,monkeypatch,section):
    monkeypatch.setattr(controller_gate,'require_green',lambda:'qualified-controller')
    monkeypatch.setattr(review,'ROOT',tmp_path)
    j=review.Journal(tmp_path/'review.sqlite');seed={'path':auth.PATHS[section],'content':''}
    row=j.create('first-proposal',seed,section=section,initial_proposal=True)
    assert row['role']=='developer' and row['findings']==[] and 'product_feedback' not in row['binding']
    from test_phase3_increment_queue import caller
    final=review.run(j,row['id'],caller)
    assert final['state']=='reviewed_application_file' and final['calls']==2
    assert [op['role'] for op in j.records(row['id'],'plan_ops')]==['developer','reviewer']
    assert handoff.consume(j,row['id'],section)[0]==final
    assert not final['product_tests_executed']


@pytest.mark.parametrize('section,content,feedback',[
    ('public_api','',None),('auth_api','existing source',None),
    ('auth_api','',{'execution_id':'failure','execution_sha256':'0'*64,'stage':'unit','output':'failed'})])
def test_initial_proposal_cannot_bypass_existing_source_review(tmp_path,monkeypatch,section,content,feedback):
    monkeypatch.setattr(controller_gate,'require_green',lambda:'qualified-controller')
    monkeypatch.setattr(review,'ROOT',tmp_path)
    j=review.Journal(tmp_path/'review.sqlite')
    path=auth.PATHS.get(section,source.PATHS.get(section))
    with pytest.raises(ValueError,match='Initial proposal'):
        j.create('invalid-proposal',{'path':path,'content':content},section=section,initial_proposal=True,product_feedback=feedback)
