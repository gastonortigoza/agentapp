"""Controller protocol tests; fake model transport is never product acceptance."""
import copy
import io
from pathlib import Path
import tarfile
import pytest
import controller_gate
import manifest
import phase3_auth_sandbox as sandbox
import phase3_auth_execution as execution
import phase3_auth_source as auth
import phase3_review as review
import phase3_roles as roles
import phase3_prepare as preparation
from test_phase3_auth_source import markers,accepted
from test_phase3_review import result


@pytest.fixture
def scoped(tmp_path,monkeypatch):
    monkeypatch.setattr(controller_gate,'require_green',lambda:'qualified-controller')
    monkeypatch.setattr(review,'ROOT',tmp_path/'controller')
    journal=review.Journal(tmp_path/'review.sqlite');workspace=tmp_path/'inputs';workspace.mkdir()
    for n,d in sandbox.seed_files().items():
        p=workspace/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(d)
    historical={};public_rows={}
    for kind,pin in sandbox.PUBLIC_ORIGINALS.items():
        row={'id':'historical-'+kind,'state':'reviewed_application_file','findings':[],
             'binding':{'suite_identity':'historical-controller'},'candidate':{'path':pin['path'],'content':(workspace/pin['path']).read_text()}}
        ops=[{'state':'confirmed'}];public_rows[row['id']]=(row,ops)
        historical[kind]={'id':row['id'],'row_sha256':manifest.identity(row),'ops_sha256':manifest.identity(ops),
                         'path':pin['path'],'suite_identity':'historical-controller','file_sha256':sandbox.FILES[pin['path']]}
    monkeypatch.setattr(sandbox,'PUBLIC_ORIGINALS',historical)
    original_get,original_records=journal.get,journal.records
    monkeypatch.setattr(journal,'get',lambda rid:copy.deepcopy(public_rows[rid][0]) if rid in public_rows else original_get(rid))
    monkeypatch.setattr(journal,'records',lambda rid,t:copy.deepcopy(public_rows[rid][1]) if rid in public_rows else original_records(rid,t))
    lock,docs,_=preparation.load_bundle()
    def call(row,op,*args):
        request={'model':row['binding']['model'],'digest':row['binding']['digest'],
          'messages':[{'role':'user','content':roles.prompt(row,op,lock,docs)}],'format':review.output_schema(row,op,lock,docs)}
        op['_record_request'](request)
        return result(accepted(row['candidate'],row['binding']['section']),prompt_sha256=manifest.identity(request['messages']))
    ids={}
    for kind in auth.SECTIONS:
        candidate={'path':auth.PATHS[kind],'content':markers(kind)};rid='auth-original-'+kind.replace('_','-')
        journal.create(rid,candidate,section=kind);review.run(journal,rid,call)
        (workspace/candidate['path']).write_text(candidate['content'],encoding='utf-8');ids[kind]=rid
    tls={'ca_sha256':'a'*64,'server_sha256':'b'*64,'certutil_sha256':sandbox.CERTUTIL_SHA256}
    return journal,workspace,ids,tls,public_rows


def assembled(scoped):
    j,w,ids,tls,_=scoped
    return sandbox.assemble(j,ids,w,tls)


def test_auth_manifest_binds_exact_originals_and_keeps_acceptance_false(scoped):
    value=assembled(scoped);j,w,ids,tls,_=scoped
    assert value['binding']['stages']['api']['argv'][-1]=='auth-acceptance.test.mjs'
    assert value['binding']['policy']['minimum_api_tests']==9 and value['binding']['policy']['minimum_browser_tests']==5
    assert value['runtime_ready'] is False and value['product_tests_executed'] is False
    assert sandbox.validate(value,j,ids,w,tls)==value
    assert value['binding']['historical_public_inputs']['application']['suite_identity']=='historical-controller'
    assert value['binding']['suite_identity']=='qualified-controller'


@pytest.mark.parametrize('path',['auth-acceptance.test.mjs','frontend/e2e/auth-session.spec.ts','backend/src/session-server.ts',
                                'frontend/src/session-main.tsx','frontend/playwright.config.ts','schema.sql','package-lock.json'])
def test_frozen_tests_entries_schema_lock_cannot_be_substituted(scoped,path):
    j,w,ids,tls,_=scoped;(w/path).write_bytes(b'replaced fixture or package')
    with pytest.raises(ValueError,match='substitution'):sandbox.assemble(j,ids,w,tls)


@pytest.mark.parametrize('path',['frontend/fixtures/wiring-main.tsx','backend/.env','server-key.pem'])
def test_fixture_entry_secret_and_extra_path_are_denied(scoped,path):
    j,w,ids,tls,_=scoped;p=w/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'extra')
    with pytest.raises(ValueError,match='Unplanned'):sandbox.assemble(j,ids,w,tls)


def test_reviewed_auth_bytes_cannot_be_changed(scoped):
    j,w,ids,tls,_=scoped;(w/auth.PATHS['auth_api']).write_text(markers('auth_api')+'\nchanged')
    with pytest.raises(ValueError,match='original bytes'):sandbox.assemble(j,ids,w,tls)


def test_historical_source_cannot_replace_current_auth_review(scoped):
    j,w,ids,tls,_=scoped;ids['auth_api']='historical-application'
    with pytest.raises((ValueError,KeyError)):sandbox.assemble(j,ids,w,tls)


def test_historical_original_is_rechecked_without_rebinding(scoped):
    j,w,ids,tls,public=scoped;public['historical-application'][0]['candidate']['content']+='changed'
    with pytest.raises(ValueError,match='Historical'):sandbox.assemble(j,ids,w,tls)


@pytest.mark.parametrize('mutation',['uncertain','fixture','controller','candidate'])
def test_uncertain_or_stale_auth_originals_fail_handoff(scoped,mutation):
    j,w,ids,tls,_=scoped;rid=ids['auth_api']
    with j.transaction() as db:
        row=manifest.parse(db.execute('SELECT body FROM plan_runs WHERE id=?',(rid,)).fetchone()[0])
        if mutation=='uncertain':row['state']='uncertain_operation'
        elif mutation=='fixture':row['binding']['expected_checks']={}
        elif mutation=='controller':row['binding']['suite_identity']='stale';row['binding_sha256']=manifest.identity(row['binding'])
        else:row['candidate']['content']+='replacement'
        db.execute('UPDATE plan_runs SET body=? WHERE id=?',(manifest.canonical(row),rid))
    with pytest.raises(ValueError):sandbox.assemble(j,ids,w,tls)


def test_manifest_drift_cannot_change_commands_or_claim_execution(scoped):
    j,w,ids,tls,_=scoped;value=assembled(scoped);value['binding']['stages']['api']['argv']=['sh','-c','arbitrary']
    with pytest.raises(ValueError,match='manifest'):sandbox.validate(value,j,ids,w,tls)
    value=assembled(scoped);value['product_tests_executed']=True
    with pytest.raises(ValueError,match='manifest'):sandbox.validate(value,j,ids,w,tls)


def test_sources_missing_and_links_are_denied(scoped):
    j,w,ids,tls,_=scoped;(w/auth.PATHS['auth_api']).unlink()
    with pytest.raises(ValueError,match='Missing'):sandbox.assemble(j,ids,w,tls)


def test_archive_has_inert_files_and_rejects_traversal():
    raw=sandbox.archive({'schema.sql':b'sql'})
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        member=tar.getmembers()[0];assert member.isfile() and member.mode==0o444
    for path in ('../secret','C:/private','/private','backend//secret','backend/./secret'):
        with pytest.raises(ValueError):sandbox.archive({path:b'secret'})


def test_gateway_and_browser_never_receive_generated_backend_or_fixture_credentials():
    files=sandbox.seed_files()|{auth.PATHS['auth_api']:b'generated',auth.PATHS['auth_pages']:b'generated-ui'}
    gateway=sandbox.worker_files('gateway',files,{'index.html':b'html','assets/main.js':b'js'})
    browser=sandbox.worker_files('browser',files)
    assert set(gateway)=={'auth-gateway.mjs','dist/index.html','dist/assets/main.js'}
    assert not any(p.startswith('backend/') or 'key' in p for p in gateway|browser)
    assert 'frontend/e2e/auth-session.spec.ts' in browser and 'frontend/fixtures/wiring-main.tsx' not in browser
    with pytest.raises(ValueError):sandbox.worker_files('gateway',files,{'index.html':b'html','server-key.pem':b'secret'})


def test_atomic_auth_roles_never_reuse_directory_select_only_role():
    schema=sandbox.seed_files()['schema.sql'];sql=sandbox.database_sql(schema,'a'*64,'b'*64).decode()
    assert sql.startswith('BEGIN;') and sql.endswith('COMMIT;\n')
    assert 'UPDATE(id)' in sql and 'age50_auth_fixture' in sql and 'REVOKE CONNECT,TEMP' in sql
    with pytest.raises(ValueError):sandbox.database_sql(schema,'a'*64,'a'*64)
    with pytest.raises(ValueError):sandbox.database_sql(schema+b'changed','a'*64,'b'*64)


def new_execution(scoped,run_id='auth-attempt'):
    j,w,ids,tls,_=scoped;return execution.create(j,run_id,assembled(scoped),ids,w,tls)


def request():return {'action':'build-copy-only-image','resource':'age50-auth-attempt-image'}


def outcome(**changes):
    return {'exit_code':0,'duration_ms':100,'timed_out':False,'truncated':False,'stdout_sha256':'a'*64,'stderr_sha256':'b'*64}|changes


def test_execution_is_not_dispatchable_before_resource_materializer(scoped):
    j,*_=scoped;row=new_execution(scoped)
    assert row['state']=='awaiting_resource_materializer' and row['runtime_ready'] is False
    with pytest.raises(ValueError,match='materializer'):execution.reserve(j,row['id'],'api','stage',60000,request())


def test_reservation_is_durable_before_dispatch_and_refunds_confirmed_usage_only(scoped):
    j,*_=scoped;row=new_execution(scoped);op=execution.reserve(j,row['id'],'build','setup',60000,request())
    assert execution.get(j,row['id'])['used']['active_ms']==60000 and op['state']=='in_flight'
    settled=execution.settle(j,row['id'],'build',outcome())
    assert settled['used']=={'active_ms':100,'operations':1}
    with j.transaction() as db:assert execution.budget(db)['used']=={'active_ms':100,'operations':1,'executions':1}
    with pytest.raises(ValueError,match='already recorded'):execution.reserve(j,row['id'],'build','setup',60000,request())
    with pytest.raises(ValueError,match='settled'):execution.settle(j,row['id'],'build',outcome())


@pytest.mark.parametrize('changes',[{'timed_out':True},{'truncated':True},{'duration_ms':60001}])
def test_uncertain_completion_retains_full_reservation_and_stops_new_work(scoped,changes):
    j,*_=scoped;row=new_execution(scoped);execution.reserve(j,row['id'],'build','setup',60000,request())
    value=execution.settle(j,row['id'],'build',outcome(**changes))
    assert value['state']=='uncertain_operation' and value['used']['active_ms']==60000
    with pytest.raises(ValueError):execution.reserve(j,row['id'],'next','setup',60000,request())
    assert execution.reserve(j,row['id'],'cleanup-owned-image','cleanup',10000,request())


def test_interruption_is_preserved_and_unchanged_code_cannot_reset_budget_under_new_id(scoped):
    j,w,ids,tls,_=scoped;row=new_execution(scoped);execution.reserve(j,row['id'],'build','setup',60000,request())
    assert execution.recover_interrupted(j,row['id'])['state']=='uncertain_operation'
    with pytest.raises(ValueError,match='do not reset'):execution.create(j,'new-budget',assembled(scoped),ids,w,tls)
    assert new_execution(scoped)['used']['active_ms']==60000


def test_confirmed_failure_stops_continuation_without_erasing_original(scoped):
    j,*_=scoped;row=new_execution(scoped);execution.reserve(j,row['id'],'build','setup',60000,request())
    value=execution.settle(j,row['id'],'build',outcome(exit_code=1))
    assert value['state']=='failed_operation' and value['operations'][0]['outcome']['exit_code']==1
    with pytest.raises(ValueError):execution.reserve(j,row['id'],'again','setup',60000,request())


def test_secret_metadata_cannot_enter_durable_operation_log(scoped):
    j,*_=scoped;row=new_execution(scoped)
    for bad in (request()|{'stdin':'password'},request()|{'resource':'DATABASE_URL=secret'}):
        with pytest.raises(ValueError):execution.reserve(j,row['id'],'build','setup',60000,bad)
    assert not execution.get(j,row['id'])['operations']


@pytest.mark.parametrize('kind',['app','api_test','gateway','browser'])
def test_all_workers_share_only_owned_loopback_namespace_with_no_mounts_ports(kind):
    args=sandbox.container_argv(kind,'age50-auth-safe','sha256:'+'a'*64,'run','b'*32,'c'*64)
    assert args[args.index('--network')+1]=='container:'+'c'*64
    assert '--read-only' in args and '--publish' not in args and '--mount' not in args and '--volume' not in args
    assert all('noexec' in args[i+1] for i,a in enumerate(args) if a=='--tmpfs')
    assert args[args.index('--memory')+1]==args[args.index('--memory-swap')+1]


def test_auth_does_not_expand_or_reset_directory_execution():
    import phase3_source,phase3_workflow
    assert phase3_source.SECTIONS==('application','public_api','public_profile')
    assert not set(auth.SECTIONS)&set(phase3_workflow.KINDS)
    assert execution.SCOPE!='public-profile' and execution.LIMITS=={'executions':3,'operations':240,'active_ms':2700000}


def test_second_operation_cannot_skip_in_flight_accounting(scoped):
    j,*_=scoped;row=new_execution(scoped);execution.reserve(j,row['id'],'build','setup',60000,request())
    with pytest.raises(ValueError,match='in flight'):execution.reserve(j,row['id'],'parallel-build','setup',60000,request())
    assert execution.get(j,row['id'])['used']['operations']==1


def test_aggregate_bound_is_persisted_and_blocks_before_external_dispatch(scoped):
    j,*_=scoped;row=new_execution(scoped)
    with j.transaction() as db:
        b=execution.budget(db);b['used']['active_ms']=execution.LIMITS['active_ms']-100;execution.save_budget(db,b)
    assert execution.reserve(j,row['id'],'over-budget','setup',1000,request()) is None
    assert execution.get(j,row['id'])['state']=='blocked_budget' and not execution.get(j,row['id'])['operations']
    with j.transaction() as db:assert execution.budget(db)['used']['active_ms']==execution.LIMITS['active_ms']-100


def effective(kind='app',install=False):
    nonce='a'*32;image='sha256:'+'b'*64;ns=None if install else 'c'*64
    args=sandbox.container_argv(kind,'age50-auth-safe',image,'auth-run',nonce,ns,install)
    p=sandbox.POLICY['node' if kind in ('app','api_test') else kind]
    return {'Image':image,'Config':{'User':'1000:1000','Env':['HOME=/tmp','PATH=/usr/local/bin:/usr/bin'],
        'Labels':{'agentapp.auth.run':'auth-run','agentapp.auth.nonce':nonce}},
        'HostConfig':{'NetworkMode':'none' if ns is None else 'container:'+ns,'ReadonlyRootfs':True,'Privileged':False,
        'Binds':None,'PortBindings':{},'CapAdd':None,'Devices':[],'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges'],
        'Memory':int(p['memory'].removesuffix('m'))*1024*1024,'MemorySwap':int(p['memory'].removesuffix('m'))*1024*1024,
        'NanoCpus':round(float(p['cpu'])*1e9),'PidsLimit':int(p['pids']),
        'Tmpfs':{args[i+1].split(':',1)[0]:args[i+1].split(':',1)[1] for i,a in enumerate(args) if a=='--tmpfs'},
        'LogConfig':{'Type':'none'}},'Mounts':[]},nonce,image,ns


@pytest.mark.parametrize('field,value',[('NetworkMode','bridge'),('ReadonlyRootfs',False),('Binds',['/private:/work']),
 ('PortBindings',{'9443/tcp':[{'HostPort':'9443'}]}),('CapAdd',['SYS_ADMIN']),('Devices',[{'PathOnHost':'/dev/private'}]),
 ('MemorySwap',-1),('SecurityOpt',[]),('Tmpfs',{'/tmp':'rw,exec'}),('LogConfig',{'Type':'json-file'})])
def test_effective_isolation_drift_is_rejected(field,value):
    d,nonce,image,ns=effective();assert execution.verify_container(d,'app','auth-run',nonce,image,ns)
    d['HostConfig'][field]=value
    with pytest.raises(ValueError,match='isolation'):execution.verify_container(d,'app','auth-run',nonce,image,ns)


@pytest.mark.parametrize('credential',['AUTH_FIXTURE_DATABASE_URL=private','AUTH_TLS_KEY_PATH=/tmp/key','GH_TOKEN=private'])
def test_app_worker_cannot_receive_fixture_tls_or_controller_credentials(credential):
    d,nonce,image,ns=effective();d['Config']['Env'].append(credential)
    with pytest.raises(ValueError,match='credentials'):execution.verify_container(d,'app','auth-run',nonce,image,ns)
