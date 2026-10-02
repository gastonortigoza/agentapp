"""Bounded isolated local FE/API/PostgreSQL dispatcher with durable operations.

No host mounts, published ports, registry access, Docker socket or controller
credentials reach generated code. A digest binds the concrete manifest; the
frozen documentary manifest stays disabled. Interruption is never a retry.
"""
import hashlib
import io
import json
from pathlib import Path
import re
import posixpath
import secrets
import subprocess
import tarfile
import threading
import time
import uuid

import controller_gate
import manifest
import phase3_handoff as handoff
import phase3_sandbox as sandbox
from phase3_dependencies import cache_archive
from worker_lock import worker_lock

ROOT=Path(__file__).resolve().parent
INITIAL_SHA='6fb342b29549c666e3c7cf2bbd0c5fe687e01e8138f75cf9e51356758e0fd72f'
MAX_OUTPUT=64000


def save(journal,row):
    with journal.transaction() as db:
        db.execute('UPDATE phase3_executions SET body=? WHERE id=?',(manifest.canonical(row),row['id']))


def get(journal,run_id):
    with journal.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS phase3_executions(id TEXT PRIMARY KEY,body TEXT NOT NULL)')
        record=db.execute('SELECT body FROM phase3_executions WHERE id=?',(run_id,)).fetchone()
    return manifest.parse(record[0]) if record else None


def process(argv,timeout,stdin=None,max_output=MAX_OUTPUT):
    """No shell; bounded concurrent pipe draining, including binary archives."""
    started=time.monotonic();proc=subprocess.Popen(argv,stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    streams={'stdout':bytearray(),'stderr':bytearray()};oversized=[]
    def drain(pipe,key):
        while True:
            chunk=pipe.read(16384)
            if not chunk:break
            streams[key].extend(chunk)
            if len(streams[key])>max_output:
                oversized.append(key);del streams[key][:-max_output]
    threads=[threading.Thread(target=drain,args=(proc.stdout,'stdout'),daemon=True),threading.Thread(target=drain,args=(proc.stderr,'stderr'),daemon=True)]
    for thread in threads:thread.start()
    def send():
        try:proc.stdin.write(stdin);proc.stdin.close()
        except (BrokenPipeError,OSError):pass
    if stdin is not None:threading.Thread(target=send,daemon=True).start()
    try:code=proc.wait(timeout=timeout);timed_out=False
    except subprocess.TimeoutExpired:
        proc.kill();proc.wait(timeout=5);code=124;timed_out=True
    for thread in threads:thread.join(timeout=2)
    return {'exit_code':code,'stdout':bytes(streams['stdout']),'stderr':bytes(streams['stderr']),
            'timed_out':timed_out,'truncated':bool(oversized),'duration_ms':round((time.monotonic()-started)*1000)}


def trusted_sql(password):
    initial=(ROOT/'phase3_initial.sql').read_bytes()
    if hashlib.sha256(initial).hexdigest()!=INITIAL_SHA:raise ValueError('Accepted PostgreSQL migration drift')
    seed=(ROOT/'phase3_directory_seed.sql').read_text(encoding='utf-8')
    return ("BEGIN; CREATE ROLE age50_migrator NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;\n"
        "CREATE SCHEMA agentapp AUTHORIZATION age50_migrator; SET ROLE age50_migrator;\n"+initial.decode()+seed+
        "\nRESET ROLE; CREATE ROLE age50_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '"+password+"';\n"
        "REVOKE ALL ON SCHEMA public FROM PUBLIC; REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;"
        "GRANT CONNECT ON DATABASE age50_test TO age50_app; GRANT USAGE ON SCHEMA agentapp TO age50_app;"
        "GRANT SELECT(id,birth_date) ON agentapp.users TO age50_app;"
        "GRANT SELECT ON agentapp.countries,agentapp.provinces,agentapp.zones,agentapp.profiles,agentapp.photos,agentapp.plans,agentapp.subscriptions TO age50_app;"
        "ALTER ROLE age50_app SET statement_timeout='5s'; ALTER ROLE age50_app SET idle_in_transaction_session_timeout='5s'; COMMIT;\n").encode()


def password_archive(password):
    output=io.BytesIO()
    with tarfile.open(fileobj=output,mode='w') as tar:
        data=password.encode();entry=tarfile.TarInfo('bootstrap/password');entry.size=len(data);entry.mode=0o400;entry.uid=entry.gid=999
        tar.addfile(entry,io.BytesIO(data))
    return output.getvalue()


def pg_argv(name,run_id,nonce):
    return ['docker','create','-i','--name',name,'--pull','never','--network','none','--read-only',
        '--cap-drop','ALL','--security-opt','no-new-privileges','--user','999:999','--cpus','0.4','--memory','768m','--memory-swap','768m',
        '--pids-limit','64','--log-driver','none','--label','agentapp.phase3.run='+run_id,'--label','agentapp.phase3.nonce='+nonce,
        '--tmpfs','/var/lib/postgresql:rw,nosuid,nodev,noexec,size=256m,uid=999,gid=999,mode=0700',
        '--tmpfs','/var/run/postgresql:rw,nosuid,nodev,noexec,size=16m,uid=999,gid=999,mode=0700',
        '--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=16m,uid=999,gid=999,mode=0700',
        '--env','POSTGRES_DB=age50_test',
        sandbox.POSTGRES,'sh','-c',
        'docker-entrypoint.sh postgres -c max_connections=10 -c listen_addresses=127.0.0.1 & p=$!; (sleep 900; kill -TERM "$p") & wait "$p"']


def verify_container(description,kind,row):
    cfg=description['Config'];host=description['HostConfig'];labels=cfg.get('Labels') or {}
    expected_network='none' if kind=='postgres' else 'container:'+row['namespace_id']
    if (labels.get('agentapp.phase3.run')!=row['id'] or labels.get('agentapp.phase3.nonce')!=row['nonce'] or
        cfg['Image']!=(sandbox.POSTGRES if kind=='postgres' else row['images'][kind]['id']) or not host['ReadonlyRootfs'] or
        host['NetworkMode']!=expected_network or host.get('Binds') or host.get('PortBindings') or host.get('Privileged') or
        host.get('CapAdd') or host.get('CapDrop')!=['ALL'] or 'no-new-privileges' not in host.get('SecurityOpt',[]) or
        cfg['User']!=('999:999' if kind=='postgres' else '1000:1000') or host['MemorySwap']!=host['Memory'] or
        host['Memory']!=int(sandbox.POLICY[kind]['memory'].removesuffix('m'))*1024*1024 or
        host['NanoCpus']!=round(float(sandbox.POLICY[kind]['cpu'])*1_000_000_000) or
        host['PidsLimit']!=int(sandbox.POLICY[kind]['pids']) or
        any(m['Type']!='tmpfs' for m in description.get('Mounts',[]))):
        raise ValueError('Effective container isolation differs from the bound policy')


def image_context(kind,source,cache,run_id,nonce,dependencies=None):
    """An inert Dockerfile: COPY only. No untrusted RUN/ADD/build instructions."""
    if kind not in ('node','browser') or not re.fullmatch(r'[a-z0-9-]{1,100}',run_id) or not re.fullmatch(r'[0-9a-f]{32}',nonce):raise ValueError('Image identity denied')
    output=io.BytesIO();total=0
    with tarfile.open(fileobj=output,mode='w') as target:
        for raw,prefix in ((source,'work/'),(cache,''),(dependencies or b'', 'work/')):
            if not raw:continue
            with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as incoming:
                for member in incoming.getmembers():
                    path=posixpath.normpath(prefix+member.name)
                    if not path.startswith(('work/','cache/')) or member.name.startswith('/') or '..' in member.name.split('/'):
                        raise ValueError('Image archive path denied')
                    if raw is dependencies and not member.name.startswith(('node_modules/','node_modules')):raise ValueError('Dependency archive path denied')
                    entry=copy_tar_member(member,path)
                    if member.issym():
                        resolved=posixpath.normpath(posixpath.join(posixpath.dirname(path),member.linkname))
                        if raw is not dependencies or member.linkname.startswith('/') or not resolved.startswith('work/'):
                            raise ValueError('Image dependency link escape')
                        target.addfile(entry)
                    elif member.isdir():target.addfile(entry)
                    elif member.isfile():
                        total+=member.size
                        if total>220*1024*1024:raise ValueError('Image context byte bound')
                        target.addfile(entry,incoming.extractfile(member))
                    else:raise ValueError('Image archive special entry denied')
        dockerfile=(f'FROM {sandbox.POLICY[kind]["image"]}\nLABEL agentapp.phase3.run="{run_id}" agentapp.phase3.nonce="{nonce}"\n'
                    'COPY --chown=1000:1000 work/ /work/\nCOPY --chown=1000:1000 cache/ /cache/\n').encode()
        entry=tarfile.TarInfo('Dockerfile');entry.size=len(dockerfile);entry.mode=0o444
        target.addfile(entry,io.BytesIO(dockerfile))
    return output.getvalue()


def copy_tar_member(member,path):
    import copy
    entry=copy.copy(member);entry.name=path;entry.uid=entry.gid=1000;entry.mtime=0
    return entry


def consume_sources(journal,review_ids,files):
    from phase3_source import SECTIONS,PATHS
    if not isinstance(review_ids,dict) or set(review_ids)!=set(SECTIONS) or any(not isinstance(v,str) or not v for v in review_ids.values()):
        raise ValueError('All public source reviews required')
    if len(set(review_ids.values()))!=len(SECTIONS):raise ValueError('Source review identities must be distinct')
    binding={}
    for section in SECTIONS:
        code,ops,_,_=handoff.consume(journal,review_ids[section],section)
        candidate=code['candidate']
        if candidate['path']!=PATHS[section] or files.get(candidate['path'])!=candidate['content'].encode():
            raise ValueError('Reviewed source bytes differ from the application:'+section)
        binding[section]={'id':review_ids[section],'path':candidate['path'],'run_sha256':manifest.identity(code),'ops_sha256':manifest.identity(ops)}
    return binding


def run(journal,run_id,reviews,code_review,workspace,candidate,bound_digest,cache,observer=None):
    if not re.fullmatch(r'[a-z0-9-]{1,100}',run_id):raise ValueError('Bad execution ID')
    with worker_lock(journal.path,'execution-'+run_id) as owned:
        if not owned:raise ValueError('Execution already owned')
        assembled=handoff.assemble(journal,reviews,workspace)
        actual=sandbox.validate_execution(candidate,workspace,assembled['plan'],bound_digest)
        fingerprint,files=sandbox.capture(workspace,assembled['plan'])
        code_binding = consume_sources(journal,code_review,files)
        data=sandbox.archive(files);cache_data,acquisition=cache_archive(files,cache)
        binding={'preflight':assembled['binding'],'manifest_sha256':manifest.identity(actual),
                 'code_reviews':code_binding,
                 'archive_sha256':hashlib.sha256(data).hexdigest(),'cache_sha256':hashlib.sha256(cache_data).hexdigest(),
                 'acquisition_sha256':manifest.identity(acquisition)}
        prior=get(journal,run_id)
        if prior:
            if prior['binding']!=binding:raise ValueError('Execution drift: preserve original evidence; a new run repeats all checks')
            if prior['state']=='active':
                prior.update(state='uncertain_operation',reason='Interrupted dispatcher; no stage or resource creation is resent')
                cleanup(journal,prior)
                save(journal,prior)
            return prior
        nonce=uuid.uuid4().hex;prefix='age50-'+nonce[:20]
        row={'schema':'agentapp.phase3-execution-result/1','id':run_id,'state':'active','reason':'',
             'binding':binding,'binding_sha256':manifest.identity(binding),'nonce':nonce,
             'resources':{k:prefix+'-'+k for k in ('postgres','node','browser')},'images':{},'operations':[], 'stages':[],
             'execution_authorized':True,'authorization_scope':'User AGE-50 request; fixed local synthetic sandbox policy, never the documentary manifest',
             'product_tests_executed':False,'full_product_acceptance':False,'active_ms':0,'cleanup':[]}
        with journal.transaction() as db:db.execute('INSERT INTO phase3_executions VALUES(?,?)',(run_id,manifest.canonical(row)))
        started=time.monotonic();app_password=secrets.token_hex(24);admin_password=secrets.token_hex(24);cursor_key=secrets.token_hex(32)
        created=[]
        def notify():
            if observer:
                try:observer(row)
                except Exception as exc:
                    row.setdefault('projection_errors',[]).append({'operation':len(row['operations']),'error_type':type(exc).__name__})
                    save(journal,row)
        def check_binding():
            if controller_gate.require_green()!=binding['preflight']['suite_identity']:raise ValueError('Controller drift')
            if sandbox.build_manifest(workspace,assembled['plan'])!=actual:raise ValueError('Code/lock/policy drift during execution')
        def command(argv,timeout=30,stdin=None,binary=False,allow_failure=False,stage_name=None):
            check_binding()
            remaining=sandbox.POLICY['total_seconds']-sandbox.POLICY['cleanup_seconds']-(time.monotonic()-started)
            if remaining<=0:raise TimeoutError('Global execution budget exhausted')
            if len(row['operations'])>=100:raise TimeoutError('Operation budget exhausted')
            redacted=[re.sub(r'^(DATABASE_URL|CURSOR_KEY|POSTGRES_PASSWORD)=.*',r'\1=<ephemeral>',arg) for arg in argv]
            op={'seq':len(row['operations']),'argv':redacted,'state':'in_flight','stdin_sha256':hashlib.sha256(stdin).hexdigest() if stdin else None}
            if stage_name is not None:op['stage']=stage_name
            row['operations'].append(op);save(journal,row)
            result=process(argv,min(timeout,remaining),stdin,200*1024*1024 if binary else MAX_OUTPUT)
            op.update(state='confirmed',**{k:result[k] for k in ('exit_code','duration_ms','timed_out','truncated')})
            for key in ('stdout','stderr'):
                op[key+'_sha256']=hashlib.sha256(result[key]).hexdigest()
                op[key]=('<binary archive>' if binary and key=='stdout' else result[key].decode('utf-8','replace').replace(app_password,'<ephemeral>').replace(admin_password,'<ephemeral>').replace(cursor_key,'<ephemeral>'))
            row['active_ms']=round((time.monotonic()-started)*1000);save(journal,row)
            if result['timed_out']:raise RuntimeError('Operation timeout; container stopped, never resent')
            if result['truncated']:raise RuntimeError('Operation output bound exceeded')
            if result['exit_code'] and not allow_failure:raise RuntimeError('Check or infrastructure command failed: '+str(result['exit_code']))
            check_binding();return result
        def inspected(kind):
            description=json.loads(command(['docker','inspect',row['resources'][kind]])['stdout'])[0]
            verify_container(description,kind,row)
            return description
        def worker(kind,dependencies=None):
            name=row['resources'][kind];tag=name+'-image'
            row['images'][kind]={'tag':tag};save(journal,row)
            context=image_context(kind,data,cache_data,run_id,nonce,dependencies)
            command(['docker','build','--network','none','--pull=false','--progress','plain','--tag',tag,'-'],120,context)
            image=json.loads(command(['docker','image','inspect',tag])['stdout'])[0]
            labels=image['Config'].get('Labels') or {}
            if labels.get('agentapp.phase3.run')!=run_id or labels.get('agentapp.phase3.nonce')!=nonce:raise ValueError('Built image ownership differs')
            row['images'][kind]['id']=image['Id'];row['images'][kind]['context_sha256']=hashlib.sha256(context).hexdigest();save(journal,row)
            argv=sandbox.container_argv(kind,name,row['resources']['postgres'])
            image_index=argv.index(sandbox.POLICY[kind]['image']);argv[image_index]=image['Id']
            argv[image_index:image_index]=['--label','agentapp.phase3.run='+run_id,'--label','agentapp.phase3.nonce='+nonce]
            command(argv);created.append(kind)
            command(['docker','start',name]);inspected(kind)
        def stage(name,container,stdin=None):
            cwd,argv,timeout=sandbox.STAGES[name]
            args=['docker','exec','-i','--workdir','/work'+('' if cwd=='.' else '/'+cwd)]
            if container=='node':args+=['--env','DATABASE_URL=postgresql://age50_app:'+app_password+'@127.0.0.1:5432/age50_test','--env','CURSOR_KEY='+cursor_key]
            args+=[row['resources'][container],*argv]
            if name in ('be','fe'):args.insert(2,'-d')
            op_index=len(row['operations']);result=command(args,timeout,stdin,stage_name=name)
            record={'name':name,'operation':op_index,'exit_code':result['exit_code'],'duration_ms':result['duration_ms']}
            if name=='unit':
                counts=[int(n) for n in re.findall(r'# tests (\d+)',result['stdout'].decode())]
                if sum(counts)<sandbox.POLICY['minimum_unit_tests'] or re.search(r'# (?:fail|skipped|cancelled) [1-9]',result['stdout'].decode()):raise RuntimeError('No complete independent unit/integration check evidence')
                record['tests']=sum(counts)
            if name=='e2e':
                summary=re.search(r'(\d+) passed',result['stdout'].decode())
                if not summary or int(summary[1])<sandbox.POLICY['minimum_e2e_tests'] or re.search(r'\d+ (?:failed|skipped|did not run)',result['stdout'].decode()):raise RuntimeError('No complete integrated browser check evidence')
                record['tests']=int(summary[1])
            if name in ('unit','e2e'):row['product_tests_executed']=True
            row['stages'].append(record);save(journal,row);notify()
        def ready(kind,port,path):
            until=min(started+sandbox.POLICY['total_seconds'],time.monotonic()+30)
            while time.monotonic()<until:
                script="fetch('http://127.0.0.1:"+str(port)+path+"',{signal:AbortSignal.timeout(1000)}).then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
                if command(['docker','exec',row['resources'][kind],'node','-e',script],5,allow_failure=True)['exit_code']==0:return
                time.sleep(.3)
            raise RuntimeError('Service readiness failed')
        try:
            db=row['resources']['postgres']
            argv=pg_argv(db,run_id,nonce);idx=argv.index(sandbox.POSTGRES);argv[idx:idx]=['--env','POSTGRES_PASSWORD='+admin_password]
            command(argv);created.append('postgres')
            row['namespace_id']=db;save(journal,row)
            command(['docker','start',db]);description=inspected('postgres')
            row['namespace_id']=description['Id'];save(journal,row)
            deadline=time.monotonic()+30
            while command(['docker','exec',db,'pg_isready','-h','127.0.0.1','-U','postgres','-d','age50_test'],5,allow_failure=True)['exit_code']:
                if time.monotonic()>deadline:raise RuntimeError('PostgreSQL readiness failed')
                time.sleep(.3)
            # Bootstrap/migration runs as the schema owner via SET ROLE. The app
            # receives only a disposable read role, never this bootstrap channel.
            op_index=len(row['operations']);r=command(['docker','exec','-i',db,*sandbox.STAGES['migrate'][1]],120,trusted_sql(app_password))
            row['stages'].append({'name':'migrate','operation':op_index,'exit_code':0,'duration_ms':r['duration_ms']});save(journal,row);notify()
            worker('node')
            node=row['resources']['node'];browser=row['resources']['browser']
            r=command(['docker','exec',node,'node','--version'])
            if r['stdout'].strip()!=b'v22.18.0':raise ValueError('Node runtime drift')
            r=command(['docker','exec',node,'npm','--version'])
            if r['stdout'].strip()!=b'10.9.3':raise ValueError('npm runtime drift')
            stage('install','node')
            dependency_archive=command(['docker','exec',node,'tar','-C','/work','-cf','-','node_modules'],60,binary=True)['stdout']
            # Browser uses the untouched dependency snapshot taken before any
            # generated source executes, and cannot alter source/tests/deps.
            worker('browser',dependency_archive)
            r=command(['docker','exec',browser,'node','--version'])
            if r['stdout'].strip()!=('v'+sandbox.POLICY['browser']['node_tool_version']).encode():raise ValueError('Browser tool runtime drift')
            probe="const fs=require('node:fs');if(fs.existsSync('/var/run/docker.sock'))process.exit(1);try{fs.writeFileSync('/work/package.json','bad');process.exit(1)}catch{};if(Object.keys(process.env).some(k=>/^(GITHUB|GH_TOKEN|LINEAR|NOTION|OPENAI)/.test(k)))process.exit(1);fetch('https://example.com',{signal:AbortSignal.timeout(1000)}).then(()=>process.exit(1)).catch(()=>process.exit(0));"
            command(['docker','exec',node,'node','-e',probe],5)
            for name in ('be_build','fe_build','unit','be','fe'):
                stage(name,'node')
                if name=='be':ready('node',3000,'/api/health')
                if name=='fe':ready('node',5173,'/')
            stage('e2e','browser')
            row.update(state='completed',reason='Bounded directory slice passed; all other SaaS criteria remain pending')
        except Exception as exc:
            uncertain=any(op['state']=='in_flight' or op.get('timed_out') for op in row['operations'])
            row.update(state='uncertain_operation' if uncertain else 'blocked_drift' if isinstance(exc,ValueError) else 'blocked_budget' if isinstance(exc,TimeoutError) else 'failed',reason=str(exc))
        finally:
            # Cleanup is limited to cryptographically named resources with exact
            # ownership labels. Never reset a named development/test database.
            for kind in reversed(tuple(row['resources'])):
                name=row['resources'][kind]
                try:
                    result=process(['docker','inspect',name],10)
                    if result['exit_code']:continue
                    description=json.loads(result['stdout'])[0];labels=description['Config'].get('Labels') or {}
                    if labels.get('agentapp.phase3.run')!=run_id or labels.get('agentapp.phase3.nonce')!=nonce:raise ValueError('Cleanup ownership mismatch')
                    removed=process(['docker','rm','-f',name],10)
                    row['cleanup'].append({'container':name,'exit_code':removed['exit_code']})
                except Exception as exc:row['cleanup'].append({'container':name,'error':type(exc).__name__})
            cleanup_images(row)
            row['active_ms']=round((time.monotonic()-started)*1000);save(journal,row);notify()
            if any(entry.get('exit_code',1) for entry in row['cleanup']):
                if row['state']=='completed':row['state']='completed_pending_cleanup'
                row['reason']+='; owned-resource cleanup requires reconciliation';save(journal,row);notify()
        return row


def cleanup(journal,row):
    """Recovery only stops owned containers; never resends a product operation."""
    if manifest.identity(row['binding'])!=row['binding_sha256']:raise ValueError('Execution binding damaged')
    if not re.fullmatch(r'[0-9a-f]{32}',row['nonce']):raise ValueError('Execution nonce damaged')
    for kind,name in reversed(tuple(row['resources'].items())):
        if name!='age50-'+row['nonce'][:20]+'-'+kind:raise ValueError('Cleanup resource identity damaged')
        result=process(['docker','inspect',name],10)
        if result['exit_code']:continue
        description=json.loads(result['stdout'])[0];labels=description['Config'].get('Labels') or {}
        if labels.get('agentapp.phase3.run')!=row['id'] or labels.get('agentapp.phase3.nonce')!=row['nonce']:raise ValueError('Cleanup ownership mismatch')
        removed=process(['docker','rm','-f',name],10)
        row['cleanup'].append({'container':name,'exit_code':removed['exit_code'],'recovery':True})
    cleanup_images(row);save(journal,row)
    return row


def cleanup_images(row):
    for kind,record in row.get('images',{}).items():
        tag=record['tag']
        if tag!=row['resources'][kind]+'-image':raise ValueError('Cleanup image identity damaged')
        try:
            result=process(['docker','image','inspect',tag],10)
            if result['exit_code']:continue
            image=json.loads(result['stdout'])[0];labels=image['Config'].get('Labels') or {}
            if labels.get('agentapp.phase3.run')!=row['id'] or labels.get('agentapp.phase3.nonce')!=row['nonce']:raise ValueError('Image cleanup ownership mismatch')
            removed=process(['docker','image','rm',tag],10)
            row['cleanup'].append({'image':tag,'exit_code':removed['exit_code']})
        except Exception as exc:row['cleanup'].append({'image':tag,'error':type(exc).__name__})
