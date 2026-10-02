"""Protocol simulation and cryptography tests. No simulated run is product proof."""
import copy
from datetime import datetime,timedelta,timezone
import io
import ipaddress
import json
import os
import pathlib
import re
import shutil
import subprocess
import tarfile
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID,ExtendedKeyUsageOID
import phase3_auth_execution as ledger
import phase3_auth_sandbox as sandbox
import phase3_auth_materializer as materializer
from test_phase3_auth_execution import scoped,assembled,new_execution,request,outcome


@pytest.fixture(scope='module')
def tls_fixture():
    now=datetime.now(timezone.utc);issuer=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'Synthetic unit test CA')])
    ca_key=rsa.generate_private_key(public_exponent=65537,key_size=2048);server_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    ca=(x509.CertificateBuilder().subject_name(issuer).issuer_name(issuer).public_key(ca_key.public_key()).serial_number(1)
        .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=5))
        .add_extension(x509.BasicConstraints(ca=True,path_length=0),True)
        .add_extension(x509.KeyUsage(True,False,False,False,False,True,True,False,False),True).sign(ca_key,hashes.SHA256()))
    cert=(x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])).issuer_name(issuer)
        .public_key(server_key.public_key()).serial_number(2).not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False,path_length=None),True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),False).sign(ca_key,hashes.SHA256()))
    public=serialization.Encoding.PEM;private=lambda k:k.private_bytes(public,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
    return ca.public_bytes(public),cert.public_bytes(public),private(server_key),private(ca_key),now


def fingerprints(ca,cert):return {'ca_sha256':sandbox.sha(ca),'server_sha256':sandbox.sha(cert),'certutil_sha256':sandbox.CERTUTIL_SHA256}


def test_real_tls_validation_binds_chain_key_san_time_without_private_fingerprint(tls_fixture):
    ca,cert,key,_,now=tls_fixture;value=materializer.validate_tls(ca,cert,key,fingerprints(ca,cert),now)
    assert value['chain_verified'] and value['key_matches'] and value['time_verified']
    assert sandbox.sha(key) not in repr(value) and 'PRIVATE KEY' not in repr(value)


@pytest.mark.parametrize('change',['wrong_key','expired','future','wrong_ca','digest','oversized'])
def test_tls_invalid_material_fails_before_docker_or_budget(tls_fixture,change):
    ca,cert,key,other,now=tls_fixture;expected=fingerprints(ca,cert)
    if change=='wrong_key':key=other
    elif change=='expired':now+=timedelta(days=2)
    elif change=='future':now-=timedelta(days=1)
    elif change=='wrong_ca':ca=cert;expected=fingerprints(ca,cert)
    elif change=='digest':expected['server_sha256']='a'*64
    else:key=b'x'*16385
    with pytest.raises(ValueError):materializer.validate_tls(ca,cert,key,expected,now)


def tar_bytes(entries):
    out=io.BytesIO()
    with tarfile.open(fileobj=out,mode='w') as target:
        for name,value in entries.items():
            if isinstance(value,tarfile.TarInfo):target.addfile(value)
            else:materializer.add(target,name,value)
    return out.getvalue()


def test_image_context_is_copy_only_and_keeps_native_modules_on_readonly_root():
    dependencies=tar_bytes({'node_modules/fixture/index.js':b'inert dependency'})
    raw=materializer.image_context(sandbox.directory.NODE,{'entry.mjs':b'entry'},'auth-run','a'*32,dependencies)
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        dockerfile=archive.extractfile('Dockerfile').read().decode()
        assert all(line.startswith(('FROM ','LABEL ','COPY ')) for line in dockerfile.splitlines())
        assert archive.extractfile('work/node_modules/fixture/index.js').read()==b'inert dependency'
        assert 'RUN ' not in dockerfile and 'ADD ' not in dockerfile


@pytest.mark.parametrize('path,kind,target',[('../host','file',''),('node_modules/secret','symlink','/host'),
 ('node_modules/.bin/escape','symlink','../../../private'),('node_modules/pipe','fifo',''),('outside/entry','file','')])
def test_dependency_archive_escape_links_and_special_entries_fail_closed(path,kind,target):
    raw=io.BytesIO()
    with tarfile.open(fileobj=raw,mode='w') as tar:
        member=tarfile.TarInfo(path)
        if kind=='symlink':member.type=tarfile.SYMTYPE;member.linkname=target
        elif kind=='fifo':member.type=tarfile.FIFOTYPE
        tar.addfile(member)
    with pytest.raises(ValueError):materializer.image_context(sandbox.directory.NODE,{'entry.mjs':b'entry'},'run','a'*32,raw.getvalue())


def test_bundle_cannot_include_private_key_or_unplanned_asset():
    good=tar_bytes({'index.html':b'page','assets/main.js':b'js'})
    assert set(materializer.bundle_files(good))=={'index.html','assets/main.js'}
    with pytest.raises(ValueError):materializer.bundle_files(tar_bytes({'index.html':b'page','server-key.pem':b'private'}))


def test_nss_tool_rejects_unqualified_archive_without_executing_package_hooks():
    with pytest.raises(ValueError):materializer.certutil_from_deb(b'unqualified deb')


def test_interruption_keeps_reservation_and_allows_owned_cleanup_without_resend(scoped):
    j,*_=scoped;row=new_execution(scoped);ledger.reserve(j,row['id'],'original','setup',60000,request())
    interrupted=ledger.recover_interrupted(j,row['id'])
    assert interrupted['operations'][0]['state']=='uncertain' and interrupted['operations'][0]['interrupted_in_flight']
    assert interrupted['used']['active_ms']==60000
    assert ledger.reserve(j,row['id'],'owned-cleanup','cleanup',10000,request())


def test_build_ready_cannot_authorize_api_or_browser_and_stage_order_is_fixed(scoped):
    j,*_=scoped;row=new_execution(scoped)
    with j.transaction() as db:
        value=ledger.load(db,row['id']);value['build_ready']=True;ledger.save(db,value)
    with pytest.raises(ValueError,match='order'):ledger.reserve(j,row['id'],'fe_build','stage',60000,request())
    with pytest.raises(ValueError,match='materializer'):ledger.reserve(j,row['id'],'api','stage',60000,request())
    assert ledger.reserve(j,row['id'],'be_build','stage',60000,request())


def test_readonly_reports_after_failed_stage_preserve_failure_and_charge_consumption(scoped):
    j,*_=scoped;row=new_execution(scoped);ledger.reserve(j,row['id'],'failure','setup',60000,request())
    ledger.settle(j,row['id'],'failure',outcome(exit_code=1))
    ledger.reserve(j,row['id'],'report','observe',15000,request());value=ledger.settle(j,row['id'],'report',outcome())
    assert value['state']=='failed_operation' and value['used']['active_ms']==200


def test_node_launch_wrapper_keeps_child_flags_after_double_dash():
    node=shutil.which('node')
    if not node:pytest.skip('Node host runtime unavailable')
    env={k:v for k,v in os.environ.items() if k in ('PATH','SystemRoot','WINDIR')}
    child="process.stdout.write(process.env.SYNTHETIC_FIXTURE);"
    result=subprocess.run([node,'-e',materializer.ENV_EXEC,'--','-e',child],input=b'{"SYNTHETIC_FIXTURE":"fixture-only"}',capture_output=True,timeout=10,env=env)
    assert result.returncode==0 and result.stdout==b'fixture-only'


class DockerProtocol:
    """Simulated Docker responses for order/accounting, never behavioral evidence."""
    def __init__(self,journal,run_id,failure=None):self.journal=journal;self.id=run_id;self.images={};self.containers={};self.calls=[];self.failure=failure
    def __call__(self,args,seconds,stdin=None,max_output=None):
        row=ledger.get(self.journal,self.id);self.calls.append((list(args),stdin));stdout=b'';stderr=b'';code=0
        if args[:2]==['docker','build']:
            name=args[args.index('--tag')+1];image='sha256:'+sandbox.sha(name.encode())
            with tarfile.open(fileobj=io.BytesIO(stdin)) as tar:
                dockerfile=tar.extractfile('Dockerfile').read().decode();assert 'PRIVATE KEY' not in stdin.decode('utf-8','ignore')
            browser=sandbox.directory.BROWSER in dockerfile
            self.images[name]={'Id':image,'Config':{'Labels':{'agentapp.auth.run':self.id,'agentapp.auth.nonce':row['nonce']},
                'Env':['PATH=/usr/bin','LC_ALL=C.UTF-8','PLAYWRIGHT_BROWSERS_PATH=/ms-playwright'] if browser else ['PATH=/usr/bin','NODE_VERSION=22.18.0','YARN_VERSION=1.22.22']}}
        elif args[:3]==['docker','image','inspect']:
            name=args[-1]
            if name not in self.images:code=1;stderr=b'Error: No such image'
            else:stdout=json.dumps([self.images[name]]).encode()
        elif args[:2]==['docker','create']:
            name=args[args.index('--name')+1];get=lambda k:args[args.index(k)+1]
            image=next((a for a in args if re.fullmatch('sha256:[a-f0-9]{64}',a)),None)
            if sandbox.directory.POSTGRES in args:image=sandbox.directory.POSTGRES.removeprefix('postgres@');baseline=['PATH=/usr/bin','PGDATA=/var/lib/postgresql/18/docker']
            else:baseline=next(d['Config']['Env'] for d in self.images.values() if d['Id']==image)
            labels={args[i+1].split('=',1)[0]:args[i+1].split('=',1)[1] for i,a in enumerate(args) if a=='--label'}
            env=baseline+[args[i+1] for i,a in enumerate(args) if a=='--env']
            host={'ReadonlyRootfs':True,'NetworkMode':get('--network'),'Binds':None,'PortBindings':{},'Privileged':False,'CapAdd':None,'Devices':[],
                'CapDrop':['ALL'],'SecurityOpt':['no-new-privileges'],'Memory':int(get('--memory').rstrip('m'))*1024*1024,
                'MemorySwap':int(get('--memory-swap').rstrip('m'))*1024*1024,'NanoCpus':round(float(get('--cpus'))*1e9),'PidsLimit':int(get('--pids-limit')),
                'Tmpfs':{args[i+1].split(':',1)[0]:args[i+1].split(':',1)[1] for i,a in enumerate(args) if a=='--tmpfs'},'LogConfig':{'Type':'none'}}
            self.containers[name]={'Id':sandbox.sha(name.encode()),'Image':image,'Config':{'User':get('--user'),'Labels':labels,'Env':env},'HostConfig':host,'Mounts':[]}
            stdout=self.containers[name]['Id'].encode()
        elif args[:3]==['docker','container','inspect']:
            if args[-1] not in self.containers:code=1;stderr=b'Error: No such container'
            else:stdout=json.dumps([self.containers[args[-1]]]).encode()
        elif args[:2]==['docker','rm']:self.containers.pop(args[-1],None)
        elif args[:3]==['docker','image','rm']:self.images.pop(args[-1],None)
        elif args[:2]==['docker','exec']:
            if '--version' in args:stdout=b'v22.18.0\n' if 'node' in args else b'10.9.3\n'
            elif 'tar' in args:stdout=tar_bytes({'index.html':b'page','assets/main.js':b'js'}) if '/tmp/auth-product-dist' in args else tar_bytes({'node_modules/fixture/index.js':b'fixture dependency'})
            elif '--test' in args:
                assert args[args.index('--')+1:]==['--import','tsx','--test','auth-acceptance.test.mjs']
                if self.failure=='api':code=1;stdout=b'# tests 9\n# pass 8\n# fail 1\n# cancelled 0\n# skipped 0\n# todo 0\n'
                else:stdout=b'# tests 9\n# pass 9\n# fail 0\n# cancelled 0\n# skipped 0\n# todo 0\n'
            elif '../node_modules/@playwright/test/cli.js' in args:
                if self.failure=='browser':code=1
            elif '/tmp/auth-product-results/results.json' in args:
                stdout=json.dumps({'stats':{'expected':4 if self.failure=='browser' else 5,'unexpected':1 if self.failure=='browser' else 0,'flaky':0,'skipped':0},'errors':[]}).encode()
        return {'exit_code':code,'stdout':stdout,'stderr':stderr,'timed_out':False,'truncated':False,'duration_ms':1}


def simulated_run(scoped,tls_fixture,monkeypatch,failure=None):
    j,w,ids,tls,_=scoped;ca,cert,key,_,_=tls_fixture
    fake_tool=b'qualified synthetic NSS tool';monkeypatch.setattr(sandbox,'CERTUTIL_SHA256',sandbox.sha(fake_tool))
    tls.update(fingerprints(ca,cert));monkeypatch.setattr(materializer,'certutil_from_deb',lambda d,*a:fake_tool)
    monkeypatch.setattr(materializer,'validate_nss_deb',lambda d:True)
    monkeypatch.setattr(sandbox,'cached_tarballs',lambda *a:{'a'*64+'.tgz':b'synthetic offline cache'})
    fake=DockerProtocol(j,'auth-simulated-runtime',failure)
    row=materializer.run(j,'auth-simulated-runtime',assembled(scoped),ids,w,w,ca,cert,key,b'synthetic deb',fake)
    return row,fake,(j,w,ids,tls,ca,cert,key)


def test_simulated_pipeline_obeys_all_stages_ownership_credentials_and_cleanup(scoped,tls_fixture,monkeypatch):
    row,fake,_=simulated_run(scoped,tls_fixture,monkeypatch)
    assert row['state']=='qualified_session_core',row['reason']
    assert [r['name'] for r in row['reports']]==['be_build','fe_build','bundle','api','browser']
    assert not fake.containers and not fake.images and len(row['cleanup'])==10
    assert all(op['state']=='confirmed' for op in row['operations']) and len(row['operations'])<=80
    for args,data in fake.calls:
        if args[:2]==['docker','exec'] and data and b'AUTH_FIXTURE_DATABASE_URL' in data:assert any(a.endswith('-api-test') for a in args)
        if data and b'PRIVATE KEY' in data:assert any(a.endswith('-gateway') for a in args)
    assert 'PRIVATE KEY' not in json.dumps(row) and 'postgresql://age50_auth_app:' not in json.dumps(row)


@pytest.mark.parametrize('failure',['api','browser'])
def test_simulated_failed_product_stage_keeps_original_reports_and_cleans(scoped,tls_fixture,monkeypatch,failure):
    row,fake,_=simulated_run(scoped,tls_fixture,monkeypatch,failure)
    assert row['state']=='failed_operation' and row['reports'][-1]['name']==failure
    assert row['reports'][-1]['exit_code']==1 and not fake.images and not fake.containers
    if failure=='browser':assert json.loads(row['browser_report'])['stats']['unexpected']==1


def test_terminal_simulated_pipeline_does_not_dispatch_again(scoped,tls_fixture,monkeypatch):
    row,fake,inputs=simulated_run(scoped,tls_fixture,monkeypatch);calls=len(fake.calls)
    j,w,ids,tls,ca,cert,key=inputs
    second=materializer.run(j,row['id'],assembled(scoped),ids,w,w,ca,cert,key,b'synthetic deb',fake)
    assert second['state']==row['state'] and len(fake.calls)==calls
