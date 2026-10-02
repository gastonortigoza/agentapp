"""Immutable session-core inputs and original-review gate, separate from directory.

Historical public UI is a frozen auxiliary input, not a fresh review or repair.
Only the two auth modules can be proposed. Fixtures never stand in for them.
"""
import copy
import hashlib
import io
import os
from pathlib import Path
import re
import tarfile

import controller_gate
import manifest
import phase3_auth_source as auth
import phase3_dependencies as dependencies
import phase3_handoff as handoff
import phase3_sandbox as directory
from phase3_auth_pins import FILES, PUBLIC_ORIGINALS, CERTUTIL_SHA256

SEED = Path(__file__).resolve().parent.parent/'examples/phase3-auth-execution/inputs'
LOCK_SHA256 = 'e4c4b4ad25631363aa7518383e1fe337d85b75fe726b622bac0ed9e1c5a756a6'
POLICY = {'schema':'agentapp.auth-local-policy/1','scope':'synthetic_auth_session_core',
 'network':'shared_loopback_namespace_without_interfaces','host_mounts':False,'host_ports':False,
 'dependency_network':False,'dependency_scripts':False,'deployment':False,'remote_writes':False,
 'input_bytes':8*1024*1024,'input_count':80,'cache_bytes':100*1024*1024,'max_packages':200,
 'total_active_ms':900000,'operation_limit':80,'cleanup_ms':90000,
 'minimum_api_tests':9,'minimum_browser_tests':5,'browser_retries':0,
 'private_tls_destination':'gateway_only_tmpfs_stdin','database':'age50_auth_test',
 'credentials':{'app':['DATABASE_URL','AUTH_KEY_B64','AUTH_ISSUER','AUTH_AUDIENCE','AUTH_ORIGIN'],
                'api_test':['DATABASE_URL','AUTH_FIXTURE_DATABASE_URL','AUTH_DISPOSABLE_FIXTURE'],
                'gateway':['AUTH_STATIC_DIR','AUTH_TLS_KEY_PATH','AUTH_TLS_CERT_PATH'], 'browser':[]},
 'node':{'image':directory.NODE,'cpu':'0.8','memory':'1024m','pids':'128'},
 'postgres':{'image':directory.POSTGRES,'cpu':'0.4','memory':'768m','pids':'64'},
 'gateway':{'image':directory.NODE,'cpu':'0.2','memory':'256m','pids':'64'},
 'browser':{'image':directory.BROWSER,'cpu':'0.8','memory':'2048m','pids':'256'}}

STAGES = {
 'be_build':{'worker':'app','cwd':'/work/backend','argv':['node','../node_modules/typescript/bin/tsc','--noEmit','-p','tsconfig.json'],'seconds':60},
 'fe_build':{'worker':'app','cwd':'/work/frontend','argv':['node','../node_modules/typescript/bin/tsc','--noEmit','-p','tsconfig.json'],'seconds':60},
 'bundle':{'worker':'app','cwd':'/work/frontend','argv':['node','../node_modules/vite/bin/vite.js','build','--config','vite.config.ts','--configLoader','runner'],'seconds':60},
 'api':{'worker':'api_test','cwd':'/work','argv':['node','--import','tsx','--test','auth-acceptance.test.mjs'],'seconds':180},
 'browser':{'worker':'browser','cwd':'/work/frontend','argv':['node','../node_modules/@playwright/test/cli.js','test','--config','playwright.config.ts'],'seconds':300}}


def sha(data):return hashlib.sha256(data).hexdigest()


def path_name(name):
    if (not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_./-]{1,200}',name)
        or name.startswith('/') or any(p in ('','.', '..') for p in name.split('/'))):
        raise ValueError('Auth path denied')
    return name


def read_files(root,expected):
    """Reject links/extras before reading; cap directory count and allocation."""
    root=Path(root)
    if root.is_symlink() or root.is_junction() or not root.is_dir():raise ValueError('Auth root link/missing')
    root=root.resolve();files={};total=0;count=0
    for folder,dirs,names in os.walk(root,followlinks=False):
        count+=len(dirs)+len(names)
        if count>POLICY['input_count']:raise ValueError('Auth file count')
        for n in dirs:
            p=Path(folder)/n;path_name(p.relative_to(root).as_posix())
            if p.is_symlink() or p.is_junction():raise ValueError('Auth directory link')
        for n in names:
            p=Path(folder)/n;name=path_name(p.relative_to(root).as_posix())
            if p.is_symlink() or p.is_junction() or not p.is_file():raise ValueError('Auth file link')
            if name not in expected:raise ValueError('Unplanned auth file: '+name)
            size=p.stat().st_size;total+=size
            if total>POLICY['input_bytes']:raise ValueError('Auth byte bound')
            data=p.read_bytes()
            if len(data)!=size:raise ValueError('Auth file changed while reading')
            files[name]=data
    if set(files)!=set(expected):raise ValueError('Missing auth inputs')
    return files


def seed_files():
    files=read_files(SEED,FILES)
    if {p:sha(d) for p,d in files.items()}!=FILES:raise ValueError('Frozen auth input drift')
    packages(files)
    return files


def packages(files):
    # Exact hashes bind every script, pin and all transitive registry sources.
    for n in ('package.json','package-lock.json','backend/package.json','frontend/package.json'):
        if sha(files[n])!=FILES[n]:raise ValueError('Auth package/lock drift')
    lock=manifest.parse(files['package-lock.json'].decode('utf-8'))
    if sha(files['package-lock.json'])!=LOCK_SHA256 or lock['lockfileVersion']!=3 or len(lock['packages'])>POLICY['max_packages']:
        raise ValueError('Auth dependency graph denied')
    dependencies.references(lock) # Platform/integrity validation; no acquisition.
    return lock


def public_originals(journal):
    receipts={}
    for kind,frozen in PUBLIC_ORIGINALS.items():
        row=journal.get(frozen['id']);ops=journal.records(row['id'],'plan_ops')
        if (manifest.identity(row)!=frozen['row_sha256'] or manifest.identity(ops)!=frozen['ops_sha256']
            or row['state']!='reviewed_application_file' or row['findings']
            or row['binding']['suite_identity']!=frozen['suite_identity']
            or any(op['state']!='confirmed' for op in ops)
            or row['candidate']['path']!=frozen['path'] or sha(row['candidate']['content'].encode())!=FILES[frozen['path']]):
            raise ValueError('Historical public original drift')
        receipts[kind]=copy.deepcopy(frozen)
    return receipts


def consume_auth(journal,review_ids,files):
    if (not isinstance(review_ids,dict) or set(review_ids)!=set(auth.SECTIONS)
        or any(not isinstance(r,str) or not r for r in review_ids.values()) or len(set(review_ids.values()))!=2):
        raise ValueError('Two distinct original auth reviews required')
    receipts={};identity=None
    for kind in auth.SECTIONS:
        row,ops,lock,_=handoff.consume(journal,review_ids[kind],kind)
        if (lock['files']['contract.json']!=auth.CONTRACT_SHA or row['candidate']['path']!=auth.PATHS[kind]
            or files.get(auth.PATHS[kind])!=row['candidate']['content'].encode()):raise ValueError('Auth original bytes differ')
        if identity is not None and row['binding']['input_identity']!=identity:raise ValueError('Mixed auth input identities')
        identity=row['binding']['input_identity']
        receipts[kind]={'id':row['id'],'row_sha256':manifest.identity(row),'ops_sha256':manifest.identity(ops)}
    return identity,receipts


def capture(workspace):
    expected=set(FILES)|set(auth.PATHS.values())
    files=read_files(workspace,expected)
    if {p:sha(files[p]) for p in FILES}!=FILES:raise ValueError('Frozen auth file substitution')
    packages(files)
    return files


def assemble(journal,review_ids,workspace,tls_public):
    """Pre-dispatch evidence only: never means tests passed or runtime is ready."""
    suite=controller_gate.require_green();seed_files();files=capture(workspace)
    if (not isinstance(tls_public,dict) or set(tls_public)!={'ca_sha256','server_sha256','certutil_sha256'}
        or any(not isinstance(s,str) or not re.fullmatch('[0-9a-f]{64}',s) for s in tls_public.values())
        or tls_public['certutil_sha256']!=CERTUTIL_SHA256):raise ValueError('Auth public TLS binding denied')
    identity,originals=consume_auth(journal,review_ids,files)
    historical=public_originals(journal)
    fingerprint={'files':{p:sha(d) for p,d in sorted(files.items())},'bytes':sum(map(len,files.values()))}
    binding={'suite_identity':suite,'input_identity':identity,'contract_sha256':auth.CONTRACT_SHA,
             'auth_originals':originals,'historical_public_inputs':historical,'workspace':fingerprint,
             'tls_public':copy.deepcopy(tls_public),'policy':copy.deepcopy(POLICY),'stages':copy.deepcopy(STAGES)}
    if capture(workspace)!=files or consume_auth(journal,review_ids,files)!=(identity,originals) or public_originals(journal)!=historical:
        raise ValueError('Auth inputs changed during handoff')
    if controller_gate.require_green()!=suite:raise ValueError('Auth controller changed')
    return {'schema':'agentapp.auth-local-manifest/1','binding':binding,'binding_sha256':manifest.identity(binding),
            'product_tests_executed':False,'deployment_authorized':False,'runtime_ready':False,
            'scope':'Session core only; historical public UI is not requalified; no owner form/reset/activation.'}


def validate(candidate,journal,review_ids,workspace,tls_public):
    actual=assemble(journal,review_ids,workspace,tls_public)
    if candidate!=actual:raise ValueError('Auth manifest/evidence/code drift')
    return actual


def archive(files):
    if not isinstance(files,dict) or len(files)>POLICY['input_count'] or sum(map(len,files.values()))>POLICY['input_bytes']:
        raise ValueError('Auth archive bound')
    out=io.BytesIO()
    with tarfile.open(fileobj=out,mode='w') as tar:
        for name,data in sorted(files.items()):
            path_name(name);entry=tarfile.TarInfo(name);entry.size=len(data);entry.mode=0o444;entry.uid=entry.gid=1000;entry.mtime=0
            tar.addfile(entry,io.BytesIO(data))
    return out.getvalue()


def cached_tarballs(files,root):
    """Reuse only existing integrity-verified cache; no network and no new budget."""
    refs=dependencies.references(packages(files));refs.pop(dependencies.NPM_INTEGRITY)
    root=Path(root)
    if root.is_symlink() or root.is_junction():raise ValueError('Auth cache root link')
    result={};total=0
    for integrity in refs:
        name=sha(integrity.encode())+'.tgz';p=root/name
        if p.is_symlink() or p.is_junction() or p.stat().st_size>16*1024*1024:raise ValueError('Auth cache file bound')
        data=p.read_bytes();total+=len(data)
        if total>POLICY['cache_bytes'] or dependencies.digest(data)!=integrity:raise ValueError('Auth cache integrity')
        result[name]=data
    return result


def database_sql(schema,app_password,fixture_password):
    if sha(schema)!=FILES['schema.sql']:raise ValueError('Auth schema drift')
    if (app_password==fixture_password or any(not isinstance(p,str) or not re.fullmatch('[a-f0-9]{64}',p) for p in (app_password,fixture_password))):
        raise ValueError('Distinct ephemeral auth passwords required')
    return ("BEGIN; CREATE ROLE age50_auth_migrator NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;\n"
        f"CREATE ROLE age50_auth_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '{app_password}';\n"
        f"CREATE ROLE age50_auth_fixture LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '{fixture_password}';\n"
        "REVOKE CONNECT,TEMP ON DATABASE age50_auth_test FROM PUBLIC; REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;\n"
        "GRANT CONNECT ON DATABASE age50_auth_test TO age50_auth_app,age50_auth_fixture;\n"+schema.decode('utf-8')+"\nCOMMIT;\n").encode()


def worker_files(kind,files,dist=None):
    """Only backend worker sees generated API; gateway never receives TLS via image."""
    if kind in ('app','api_test'):return dict(files)
    if kind=='browser':
        return {p:d for p,d in files.items() if p in ('frontend/playwright.config.ts','frontend/e2e/auth-session.spec.ts','package.json','frontend/package.json','package-lock.json')}
    if kind=='gateway':
        if not isinstance(dist,dict) or 'index.html' not in dist:raise ValueError('Built auth dist required')
        result={'auth-gateway.mjs':files['auth-gateway.mjs']}
        for n,d in dist.items():
            path_name(n)
            if not (n=='index.html' or re.fullmatch(r'assets/[A-Za-z0-9_.-]+\.(?:js|css)',n)):raise ValueError('Unexpected gateway bundle entry')
            result['dist/'+n]=d
        return result
    raise ValueError('Auth worker kind denied')


def container_argv(kind,name,image,run_id,nonce,namespace=None,install=False):
    if kind not in ('postgres','app','api_test','gateway','browser'):raise ValueError('Auth worker denied')
    if (not re.fullmatch('age50-auth-[a-z0-9-]{1,60}',name) or not re.fullmatch('[a-z0-9-]{1,100}',run_id)
        or not re.fullmatch('[a-f0-9]{32}',nonce) or (namespace is not None and not re.fullmatch('sha256:[a-f0-9]{64}|[a-f0-9]{64}',namespace))):raise ValueError('Auth resource identity denied')
    if kind=='postgres':
        if namespace is not None or install or image!=directory.POSTGRES:raise ValueError('Auth PG image/namespace denied')
    elif not re.fullmatch('sha256:[a-f0-9]{64}',image) or (install and (kind!='app' or namespace is not None)):
        raise ValueError('Auth derived image/installer denied')
    elif not install and namespace is None:raise ValueError('Auth worker needs owned PG namespace')
    cfg=POLICY['node' if kind in ('app','api_test') else kind];uid=999 if kind=='postgres' else 1000
    mounts={'/tmp':f'rw,nosuid,nodev,noexec,size=128m,uid={uid},gid={uid},mode=0700'}
    if kind=='postgres':mounts.update({'/var/lib/postgresql':'rw,nosuid,nodev,noexec,size=256m,uid=999,gid=999,mode=0700','/var/run/postgresql':'rw,nosuid,nodev,noexec,size=16m,uid=999,gid=999,mode=0700'})
    if install:mounts.update({'/work':'rw,nosuid,nodev,noexec,size=512m,uid=1000,gid=1000,mode=0700','/cache':'rw,nosuid,nodev,noexec,size=128m,uid=1000,gid=1000,mode=0700'})
    args=['docker','create','-i','--pull','never','--name',name,'--network','none' if namespace is None else 'container:'+namespace,
          '--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--user',f'{uid}:{uid}',
          '--cpus',cfg['cpu'],'--memory',cfg['memory'],'--memory-swap',cfg['memory'],'--pids-limit',cfg['pids'],
          '--shm-size','128m','--log-driver','none','--label','agentapp.auth.run='+run_id,'--label','agentapp.auth.nonce='+nonce,
          *[a for p,v in mounts.items() for a in ('--tmpfs',p+':'+v)]]
    if kind=='postgres':
        return args+['--env','POSTGRES_DB=age50_auth_test',image,'sh','-c',
          'docker-entrypoint.sh postgres -c max_connections=16 -c listen_addresses=127.0.0.1 & p=$!; (sleep 900; kill -TERM "$p") & wait "$p"']
    return args+['--workdir','/work','--env','HOME=/tmp','--env','CI=1','--env','NPM_CONFIG_USERCONFIG=/tmp/empty-npmrc',image,'node','-e','setTimeout(()=>process.exit(0),900000)']
