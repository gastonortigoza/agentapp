"""Concrete disposable auth runtime. No deployment, mounts or external network.

Every Docker operation is durably reserved before dispatch. A restart only
reconciles owned resources; it never resends an operation or resets an attempt.
"""
import copy
from datetime import datetime,timezone
import hashlib
import io
import ipaddress
import json
from pathlib import Path
import posixpath
import re
import secrets
import tarfile

import controller_gate
import manifest
import phase3_auth_execution as ledger
import phase3_auth_sandbox as sandbox
import phase3_execution as process_tools
from worker_lock import worker_lock

DEB_SHA256='d8d6093edcf2206edeee40eaddd90a54bacc84cde4d8cc9c4fb50bd7aa12cf0d'
MAX_ARCHIVE=220*1024*1024
COPY_FILES="const fs=require('node:fs');fs.cpSync('/seed','/work',{recursive:true});"
WRITE_KEY="umask 077; cat > /tmp/server-key.pem; chmod 400 /tmp/server-key.pem"
WRITE_ADMIN="umask 077; cat > /tmp/admin-password; chmod 400 /tmp/admin-password"
ENV_EXEC="""const fs=require('node:fs'),cp=require('node:child_process');const v=JSON.parse(fs.readFileSync(0,'utf8'));const c=cp.spawn(process.execPath,process.argv.slice(1),{env:{...process.env,...v},stdio:['ignore','inherit','inherit']});c.on('error',()=>process.exit(1));c.on('close',code=>process.exit(code??1));"""
SERVICE_EXEC="""const fs=require('node:fs'),cp=require('node:child_process');const v=JSON.parse(fs.readFileSync(0,'utf8'));const log=fs.openSync('/tmp/service.log','a',0o600);const c=cp.spawn(process.execPath,process.argv.slice(1),{env:{...process.env,...v},detached:true,stdio:['ignore',log,log]});c.on('error',()=>process.exit(1));c.on('spawn',()=>{c.unref();process.stdout.write('started');});"""
NSS_INIT="""const fs=require('node:fs'),cp=require('node:child_process');fs.mkdirSync('/tmp/.pki/nssdb',{recursive:true});cp.execFileSync('/usr/bin/certutil',['-N','--empty-password','-d','sql:/tmp/.pki/nssdb']);cp.execFileSync('/usr/bin/certutil',['-A','-d','sql:/tmp/.pki/nssdb','-t','C,,','-n','age50-auth-disposable-ca','-i','/work/ca.pem']);"""
READINESS="""const fs=require('node:fs'),https=require('node:https'),http=require('node:http');const end=Date.now()+45000;async function check(mod,options){return new Promise(resolve=>{const r=mod.get(options,s=>{s.resume();resolve(s.statusCode);});r.setTimeout(1500,()=>r.destroy());r.on('error',()=>resolve(0));});} (async()=>{while(Date.now()<end){const a=await check(http,{hostname:'127.0.0.1',port:3000,path:'/'});const g=await check(https,{hostname:'localhost',port:9443,path:'/',ca:fs.readFileSync('/work/ca.pem'),rejectUnauthorized:true});if(a===404&&g===200){process.stdout.write('auth-services-ready');return;}await new Promise(r=>setTimeout(r,300));}process.exit(1);})();"""


def checked_bytes(path,limit):
    path=Path(path)
    if path.is_symlink() or path.is_junction() or path.stat().st_size>limit:raise ValueError('Auth artifact link/size denied')
    data=path.read_bytes()
    if len(data)>limit:raise ValueError('Auth artifact byte bound')
    return data


def validate_tls(ca_pem,server_pem,key_pem,expected,now=None):
    """Validate actual private key/chain/time/SAN; expose only public fingerprints."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa,padding
    from cryptography.x509.oid import ExtendedKeyUsageOID
    if any(not isinstance(v,bytes) or not 1<=len(v)<=16384 for v in (ca_pem,server_pem,key_pem)):raise ValueError('Auth TLS input bound')
    if {'ca_sha256':sandbox.sha(ca_pem),'server_sha256':sandbox.sha(server_pem),'certutil_sha256':sandbox.CERTUTIL_SHA256}!=expected:
        raise ValueError('Auth public TLS fingerprint drift')
    try:
        ca=x509.load_pem_x509_certificate(ca_pem);cert=x509.load_pem_x509_certificate(server_pem)
        key=serialization.load_pem_private_key(key_pem,password=None);public=ca.public_key();current=now or datetime.now(timezone.utc)
        if (current.tzinfo is None or not isinstance(public,rsa.RSAPublicKey) or not isinstance(key,rsa.RSAPrivateKey)
            or public.key_size<2048 or key.key_size<2048 or ca.subject!=ca.issuer or cert.issuer!=ca.subject
            or ca.signature_hash_algorithm.name!='sha256' or cert.signature_hash_algorithm.name!='sha256'):
            raise ValueError('Auth TLS chain/key policy')
        public.verify(ca.signature,ca.tbs_certificate_bytes,padding.PKCS1v15(),ca.signature_hash_algorithm)
        public.verify(cert.signature,cert.tbs_certificate_bytes,padding.PKCS1v15(),cert.signature_hash_algorithm)
        if not (ca.not_valid_before_utc<=current<ca.not_valid_after_utc and cert.not_valid_before_utc<=current<cert.not_valid_after_utc):raise ValueError('Auth TLS expired/not valid')
        if not ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca or cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:raise ValueError('Auth TLS CA constraints')
        if not ca.extensions.get_extension_for_class(x509.KeyUsage).value.key_cert_sign:raise ValueError('Auth TLS CA usage')
        san=cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        if san.get_values_for_type(x509.DNSName)!=['localhost'] or san.get_values_for_type(x509.IPAddress)!=[ipaddress.ip_address('127.0.0.1')]:raise ValueError('Auth TLS fixture hosts only')
        if ExtendedKeyUsageOID.SERVER_AUTH not in cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value:raise ValueError('Auth TLS server usage')
        encode=lambda k:k.public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
        if encode(key.public_key())!=encode(cert.public_key()):raise ValueError('Auth TLS private key mismatch')
    except Exception as exc:raise ValueError('Auth TLS validation failed') from None
    return {'public':copy.deepcopy(expected),'key_matches':True,'chain_verified':True,'time_verified':True,'hosts':['localhost','127.0.0.1']}


def validate_nss_deb(deb):
    """Verify the signature-qualified pinned archive before any decoder sees it."""
    if not isinstance(deb,bytes) or len(deb)>16*1024*1024 or sandbox.sha(deb)!=DEB_SHA256 or deb[:8]!=b'!<arch>\n':raise ValueError('Pinned NSS archive denied')
    return True


def certutil_from_deb(deb,decoded_tar=None):
    """Only the pinned tool may emerge from dpkg's inert archive extraction."""
    validate_nss_deb(deb);offset=8;payload=None;compressed=False
    while offset<len(deb):
        header=deb[offset:offset+60]
        if len(header)!=60 or header[58:]!=b'`\n':raise ValueError('NSS ar format')
        size=int(header[48:58]);name=header[:16].decode('ascii').strip().rstrip('/');offset+=60
        if size<0 or offset+size>len(deb):raise ValueError('NSS ar size')
        if name in ('data.tar.xz','data.tar.gz'):payload=deb[offset:offset+size]
        if name=='data.tar.zst':compressed=True
        offset+=size+(size%2)
    if compressed:
        if not isinstance(decoded_tar,bytes) or len(decoded_tar)>32*1024*1024:raise ValueError('NSS zstd requires bounded isolated dpkg extraction')
        payload=decoded_tar
    if payload is None:raise ValueError('NSS data archive missing')
    with tarfile.open(fileobj=io.BytesIO(payload),mode='r:*') as source:
        result=None;count=0;total=0
        for member in source:
            count+=1;total+=member.size
            if count>2000 or total>32*1024*1024:raise ValueError('NSS tar bound')
            if member.name in ('./usr/bin/certutil','usr/bin/certutil'):
                if not member.isfile() or member.size>16*1024*1024 or result is not None:raise ValueError('NSS certutil entry')
                result=source.extractfile(member).read()
    if result is None or sandbox.sha(result)!=sandbox.CERTUTIL_SHA256:raise ValueError('NSS certutil fingerprint drift')
    return result


def add(tar,name,data,mode=0o444):
    sandbox.path_name(name);entry=tarfile.TarInfo(name);entry.size=len(data);entry.uid=entry.gid=1000;entry.mode=mode;entry.mtime=0
    tar.addfile(entry,io.BytesIO(data))


def image_context(base,files,run_id,nonce,dependencies=None,tarballs=None,certutil=None,installer=False,nss_deb=None):
    if base not in (sandbox.directory.NODE,sandbox.directory.BROWSER) or not re.fullmatch('[a-z0-9-]{1,100}',run_id) or not re.fullmatch('[a-f0-9]{32}',nonce):raise ValueError('Auth image identity')
    out=io.BytesIO();total=0
    with tarfile.open(fileobj=out,mode='w') as target:
        for n,d in sorted(files.items()):
            total+=len(d)
            if total>MAX_ARCHIVE:raise ValueError('Auth image byte bound')
            add(target,('seed/' if installer else 'work/')+sandbox.path_name(n),d)
        if dependencies is not None:
            if not isinstance(dependencies,bytes) or len(dependencies)>MAX_ARCHIVE:raise ValueError('Auth dependency archive bound')
            with tarfile.open(fileobj=io.BytesIO(dependencies),mode='r:') as source:
                seen=set();count=0
                for member in source:
                    count+=1
                    if count>40000:raise ValueError('Auth dependency archive count')
                    name=member.name
                    if not (name=='node_modules' or name.startswith('node_modules/')) or name.startswith('/') or '..' in name.split('/') or name in seen:raise ValueError('Auth dependency archive path')
                    seen.add(name);entry=process_tools.copy_tar_member(member,'work/'+name)
                    if member.issym():
                        resolved=posixpath.normpath(posixpath.join(posixpath.dirname(entry.name),member.linkname))
                        if member.linkname.startswith('/') or not resolved.startswith('work/'):raise ValueError('Auth dependency link escape')
                        target.addfile(entry)
                    elif member.isdir():target.addfile(entry)
                    elif member.isfile():
                        total+=member.size
                        if member.size>16*1024*1024 or total>MAX_ARCHIVE:raise ValueError('Auth dependency byte bound')
                        target.addfile(entry,source.extractfile(member))
                    else:raise ValueError('Auth dependency special entry')
        for name,data in (tarballs or {}).items():
            if not installer or not re.fullmatch(r'[a-f0-9]{64}\.tgz',name):raise ValueError('Auth installer tarballs')
            total+=len(data)
            if total>MAX_ARCHIVE:raise ValueError('Auth installer context bound')
            add(target,'tarballs/'+name,data)
        if certutil is not None:
            if base!=sandbox.directory.BROWSER or sandbox.sha(certutil)!=sandbox.CERTUTIL_SHA256:raise ValueError('Auth browser tool drift')
            add(target,'tool/certutil',certutil,0o555)
        if nss_deb is not None:
            if not installer or base!=sandbox.directory.NODE:raise ValueError('NSS archive only in isolated installer')
            validate_nss_deb(nss_deb);add(target,'tool/nss-tools.deb',nss_deb)
        dockerfile=f'FROM {base}\nLABEL agentapp.auth.run="{run_id}" agentapp.auth.nonce="{nonce}"\n'
        dockerfile+='COPY --chown=1000:1000 seed/ /seed/\nCOPY --chown=1000:1000 tarballs/ /tarballs/\n' if installer else 'COPY --chown=1000:1000 work/ /work/\n'
        if certutil is not None:dockerfile+='COPY --chown=1000:1000 tool/certutil /usr/bin/certutil\n'
        if nss_deb is not None:dockerfile+='COPY --chown=1000:1000 tool/nss-tools.deb /tools/nss-tools.deb\n'
        add(target,'Dockerfile',dockerfile.encode())
    return out.getvalue()


def bundle_files(raw):
    if not isinstance(raw,bytes) or len(raw)>16*1024*1024:raise ValueError('Auth bundle archive bound')
    files={}
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as source:
        for member in source:
            if member.isdir():continue
            name=posixpath.normpath(member.name)
            if member.name.startswith('/') or '..' in member.name.split('/') or not member.isfile() or member.size>8*1024*1024 or name in files or len(files)>=30:raise ValueError('Auth bundle entry denied')
            sandbox.path_name(name);files[name]=source.extractfile(member).read()
    sandbox.worker_files('gateway',sandbox.seed_files(),files)
    return files


def redact(text,private_values):
    for secret in sorted(private_values,key=len,reverse=True):
        if secret:text=text.replace(secret,'[redacted]')
    text=re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+','[redacted-token]',text)
    return text


class Runtime:
    def __init__(self,journal,run_id,transport=process_tools.process,private_values=()):
        self.journal=journal;self.id=run_id;self.transport=transport;self.private_values=private_values

    def row(self):return ledger.get(self.journal,self.id)

    def update(self,**changes):
        with self.journal.transaction() as db:
            row=ledger.load(db,self.id);row.update(changes);ledger.save(db,row)
        return row

    def command(self,key,kind,action,resource,argv,seconds=15,stdin=None,allow_failure=False,binary=False):
        if kind not in ('cleanup','observe') and controller_gate.require_green()!=self.row()['manifest']['binding']['suite_identity']:
            raise ValueError('Auth controller changed before dispatch')
        if ledger.reserve(self.journal,self.id,key,kind,seconds*1000,{'action':action.replace('_','-'),'resource':resource}) is None:raise TimeoutError('Auth durable budget exhausted')
        try:result=self.transport(argv,seconds,stdin=stdin,max_output=MAX_ARCHIVE if binary else 64000)
        except BaseException:
            ledger.recover_interrupted(self.journal,self.id);raise
        outcome={k:result[k] for k in ('exit_code','duration_ms','timed_out','truncated')}
        outcome.update(stdout_sha256=sandbox.sha(result['stdout']),stderr_sha256=sandbox.sha(result['stderr']))
        row=ledger.settle(self.journal,self.id,key,outcome)
        if kind=='stage':
            report={'name':key,'operation':len(row['operations'])-1,'exit_code':result['exit_code'],
                    'stdout':redact(result['stdout'].decode('utf-8','replace'),self.private_values),
                    'stderr':redact(result['stderr'].decode('utf-8','replace'),self.private_values)}
            self.update(reports=row.get('reports',[])+[report])
        if row['operations'][-1]['state']!='confirmed':raise RuntimeError('Uncertain auth operation; never replay')
        if not allow_failure and result['exit_code']!=0:raise RuntimeError('Confirmed auth operation failed: '+action)
        return result

    def inspect(self,kind,name,key,cleanup=False):
        result=self.command(key,'cleanup' if cleanup else 'setup','inspect-owned-'+kind,name,['docker',kind,'inspect',name],5,allow_failure=cleanup)
        if result['exit_code']!=0:
            if cleanup and re.search(rb'No such (?:image|container|object)',result['stderr'],re.I):return None
            raise RuntimeError('Auth resource inspection failed; absence unconfirmed')
        values=json.loads(result['stdout'])
        if len(values)!=1:raise ValueError('Auth resource inspect shape')
        return values[0]

    def own(self,kind,name):
        row=self.row();item={'kind':kind,'name':name}
        if item in row.get('resources',[]):raise ValueError('Auth resource already owned')
        self.update(resources=row.get('resources',[])+[item])

    def image(self,kind,base,files,dependencies=None,tarballs=None,certutil=None,installer=False,nss_deb=None):
        row=self.row();name='age50-auth-'+row['nonce'][:20]+'-'+kind+'-image';self.own('image',name)
        context=image_context(base,files,self.id,row['nonce'],dependencies,tarballs,certutil,installer,nss_deb)
        self.command('build-'+kind,'setup','build-copy-only-'+kind,name,['docker','build','--network','none','--pull=false','--progress','plain','--tag',name,'-'],90,context)
        desc=self.inspect('image',name,'inspect-image-'+kind);labels=desc['Config'].get('Labels') or {}
        if labels.get('agentapp.auth.run')!=self.id or labels.get('agentapp.auth.nonce')!=row['nonce']:raise ValueError('Auth image ownership')
        return desc['Id']

    def worker(self,kind,image,namespace=None,install=False):
        row=self.row();name='age50-auth-'+row['nonce'][:20]+'-'+('installer' if install else kind.replace('_','-'));self.own('container',name)
        args=sandbox.container_argv(kind,name,image,self.id,row['nonce'],namespace,install)
        if kind=='postgres':
            args[-1]="timeout 900 sh -c 'while [ ! -s /tmp/admin-password ]; do sleep 0.1; done; export POSTGRES_PASSWORD_FILE=/tmp/admin-password; "+args[-1]+"'"
        self.command('create-'+('installer' if install else kind),'setup','create-'+kind,name,args)
        self.command('start-'+('installer' if install else kind),'setup','start-'+kind,name,['docker','start',name])
        desc=self.inspect('container',name,'inspect-'+('installer' if install else kind))
        expected=sandbox.directory.POSTGRES.removeprefix('postgres@') if kind=='postgres' else image
        ledger.verify_container(desc,kind,self.id,row['nonce'],expected,namespace,install)
        return name,desc['Id']

    def stage(self,name,worker,environment=None):
        stage=sandbox.STAGES[name];argv=['docker','exec','-i','--workdir',stage['cwd'],worker]
        if environment is None:argv+=stage['argv'];stdin=None
        else:
            allowed=sandbox.POLICY['credentials'][stage['worker']]
            if set(environment)!=set(allowed):raise ValueError('Auth stage credentials denied')
            argv+=['node','-e',ENV_EXEC,'--',*stage['argv'][1:]];stdin=json.dumps(environment).encode()
        result=self.command(name,'stage','stage-'+name,worker,argv,stage['seconds'],stdin,allow_failure=name in ('api','browser'))
        if name=='api':
            text=result['stdout'].decode('utf-8','replace')
            if re.search(r'# tests [1-9][0-9]*\b',text):self.update(product_tests_executed=True)
            if any(not re.search(r'# '+field+r' '+str(value)+r'\b',text) for field,value in (('tests',9),('pass',9),('fail',0),('cancelled',0),('skipped',0),('todo',0))):raise ValueError('Auth API test totals invalid')
            if result['exit_code']!=0:raise RuntimeError('Auth API acceptance failed')
        return result

    def cleanup(self):
        row=self.row();ledger.recover_interrupted(self.journal,self.id)
        for index,item in reversed(list(enumerate(row.get('resources',[])))):
            name=item['name'];kind=item['kind'];key='cleanup-'+str(index)
            # A cleanup operation with uncertainty is never retried automatically.
            current=self.row()
            if any(op['key'].startswith(key+'-') for op in current['operations']):continue
            try:
                desc=self.inspect(kind,name,key+'-inspect',cleanup=True)
                if desc is not None:
                    labels=desc['Config'].get('Labels') or {}
                    if labels.get('agentapp.auth.run')!=self.id or labels.get('agentapp.auth.nonce')!=row['nonce']:raise ValueError('Auth cleanup ownership mismatch')
                    argv=['docker','rm','--force',name] if kind=='container' else ['docker','image','rm',name]
                    self.command(key+'-remove','cleanup','remove-owned-'+kind,name,argv,10)
                current=self.row();self.update(cleanup=current.get('cleanup',[])+[item|{'confirmed':True}])
            except Exception as exc:
                current=self.row();self.update(cleanup=current.get('cleanup',[])+[item|{'confirmed':False,'reason':type(exc).__name__}])
        current=self.row()
        if len(current.get('cleanup',[]))!=len(current.get('resources',[])) or any(c['confirmed'] is not True for c in current.get('cleanup',[])):
            self.update(state='needs_resource_reconciliation',reason='Owned-resource cleanup incomplete; do not replay uncertain operations')
        elif current.get('accepted_before_cleanup') and current['state'] not in ('uncertain_operation','failed_operation','blocked_budget'):
            self.update(state='qualified_session_core',reason='Nine real PG/API and five strict-TLS Chromium checks passed; session-core scope only')
        return self.row()


def run(journal,run_id,concrete,review_ids,workspace,cache_root,ca_pem,server_pem,key_pem,nss_deb,transport=process_tools.process):
    # Complete all deterministic preflight before consuming an execution budget.
    tls=concrete['binding']['tls_public'];validate_tls(ca_pem,server_pem,key_pem,tls)
    validate_nss_deb(nss_deb);files=sandbox.capture(workspace);tarballs=sandbox.cached_tarballs(files,cache_root)
    sandbox.validate(concrete,journal,review_ids,workspace,tls)
    with worker_lock(journal.path,'auth-runtime-'+run_id) as acquired:
        if not acquired:raise ValueError('Auth runtime worker already active')
        row=ledger.create(journal,run_id,concrete,review_ids,workspace,tls)
        runtime=Runtime(journal,run_id,transport)
        if row['operations'] or row['state']!='awaiting_resource_materializer':
            ledger.recover_interrupted(journal,run_id)
            return runtime.cleanup() if row.get('resources') else runtime.row()
        admin,app_secret,fixture_secret=(secrets.token_hex(32) for _ in range(3));jwt_key=__import__('base64').b64encode(secrets.token_bytes(32)).decode()
        runtime.private_values=(admin,app_secret,fixture_secret,jwt_key,key_pem.decode('ascii'))
        try:
            install_image=runtime.image('installer',sandbox.directory.NODE,files,tarballs=tarballs,installer=True,nss_deb=nss_deb)
            installer,_=runtime.worker('app',install_image,install=True)
            node=runtime.command('node-version','setup','verify-node',installer,['docker','exec',installer,'node','--version'])
            npm=runtime.command('npm-version','setup','verify-npm',installer,['docker','exec',installer,'npm','--version'])
            if node['stdout'].strip()!=b'v22.18.0' or npm['stdout'].strip()!=b'10.9.3':raise ValueError('Auth installer runtime drift')
            decoded=runtime.command('nss-extract','setup','extract-pinned-nss-inert',installer,['docker','exec',installer,'dpkg-deb','--fsys-tarfile','/tools/nss-tools.deb'],30,binary=True)['stdout']
            tool=certutil_from_deb(nss_deb,decoded)
            runtime.command('copy-seed','setup','copy-frozen-seed',installer,['docker','exec',installer,'node','-e',COPY_FILES])
            runtime.command('cache-add','setup','populate-offline-cache',installer,['docker','exec',installer,'npm','cache','add',*['/tarballs/'+n for n in tarballs],'--offline','--ignore-scripts','--no-audit','--fund=false','--cache','/cache'],90)
            runtime.command('npm-ci','setup','install-offline-locked',installer,['docker','exec','--workdir','/work',installer,'npm','ci','--offline','--ignore-scripts','--no-audit','--fund=false','--engine-strict','--cache','/cache'],90)
            modules=runtime.command('capture-modules','setup','capture-readonly-modules',installer,['docker','exec',installer,'tar','-C','/work','-cf','-','node_modules'],30,binary=True)['stdout']
            pg,namespace=runtime.worker('postgres',sandbox.directory.POSTGRES)
            runtime.command('pg-password','setup','inject-private-pg-password',pg,['docker','exec','-i',pg,'sh','-c',WRITE_ADMIN],stdin=admin.encode())
            runtime.command('pg-final-ready','setup','wait-final-tcp-pg',pg,['docker','exec',pg,'sh','-c','for i in $(seq 1 100); do pg_isready -h 127.0.0.1 -U postgres -d age50_auth_test >/dev/null && exit 0; sleep 0.3; done; exit 1'],40)
            runtime.command('bootstrap-auth','setup','atomic-auth-bootstrap',pg,['docker','exec','-i',pg,'psql','-X','-q','-v','ON_ERROR_STOP=1','-U','postgres','-d','age50_auth_test'],30,sandbox.database_sql(files['schema.sql'],app_secret,fixture_secret))
            app_image=runtime.image('app',sandbox.directory.NODE,sandbox.worker_files('app',files)|{'ca.pem':ca_pem},dependencies=modules)
            app,_=runtime.worker('app',app_image,namespace)
            runtime.update(build_ready=True,state='building_product',reason='Original code in readonly worker; disposable PG bootstrap confirmed')
            for name in ('be_build','fe_build','bundle'):runtime.stage(name,app)
            raw_dist=runtime.command('capture-bundle','setup','capture-product-bundle',app,['docker','exec',app,'tar','-C','/tmp/auth-product-dist','-cf','-','.'],30,binary=True)['stdout'];dist=bundle_files(raw_dist)
            api_worker,_=runtime.worker('api_test',app_image,namespace)
            browser_image=runtime.image('browser',sandbox.directory.BROWSER,sandbox.worker_files('browser',files)|{'ca.pem':ca_pem},dependencies=modules,certutil=tool)
            browser,_=runtime.worker('browser',browser_image,namespace)
            runtime.command('nss-init','setup','initialize-ephemeral-nss',browser,['docker','exec',browser,'node','-e',NSS_INIT])
            gateway_image=runtime.image('gateway',sandbox.directory.NODE,sandbox.worker_files('gateway',files,dist)|{'server.pem':server_pem})
            gateway,_=runtime.worker('gateway',gateway_image,namespace)
            runtime.command('tls-key','setup','inject-private-gateway-key',gateway,['docker','exec','-i',gateway,'sh','-c',WRITE_KEY],stdin=key_pem)
            url=lambda role,password:'postgresql://'+role+':'+password+'@127.0.0.1:5432/age50_auth_test'
            app_env={'DATABASE_URL':url('age50_auth_app',app_secret),'AUTH_KEY_B64':jwt_key,'AUTH_ISSUER':'age50-auth-disposable','AUTH_AUDIENCE':'age50-auth-browser','AUTH_ORIGIN':'https://localhost:9443'}
            gateway_env={'AUTH_STATIC_DIR':'/work/dist','AUTH_TLS_KEY_PATH':'/tmp/server-key.pem','AUTH_TLS_CERT_PATH':'/work/server.pem'}
            for role,name,environment,argv in [('app',app,app_env,['--import','tsx','backend/src/session-server.ts']),('gateway',gateway,gateway_env,['auth-gateway.mjs'])]:
                if set(environment)!=set(sandbox.POLICY['credentials'][role]):raise ValueError('Auth service credentials policy')
                runtime.command('serve-'+role,'setup','start-private-'+role,name,['docker','exec','-i',name,'node','-e',SERVICE_EXEC,'--',*argv],15,json.dumps(environment).encode())
            runtime.command('services-ready','setup','strict-tls-and-api-readiness',app,['docker','exec',app,'node','-e',READINESS],50)
            # Revalidate the original host inputs at the final test boundary.
            sandbox.validate(concrete,journal,review_ids,workspace,tls)
            runtime.update(runtime_ready=True,state='testing_product',reason='Owned PG/app/API/gateway/browser verified; final TCP, real TLS/key and ephemeral NSS ready')
            runtime.stage('api',api_worker,{'DATABASE_URL':url('age50_auth_app',app_secret),'AUTH_FIXTURE_DATABASE_URL':url('age50_auth_fixture',fixture_secret),'AUTH_DISPOSABLE_FIXTURE':'1'})
            browser_result=runtime.stage('browser',browser)
            raw=runtime.command('browser-report','observe','read-original-browser-report',browser,['docker','exec',browser,'cat','/tmp/auth-product-results/results.json'])['stdout']
            report=json.loads(raw);stats=report['stats']
            runtime.update(browser_report=redact(raw.decode('utf-8'),runtime.private_values))
            if browser_result['exit_code']!=0 or stats.get('expected')!=5 or any(stats.get(k)!=0 for k in ('unexpected','flaky','skipped')) or report.get('errors'):raise ValueError('Auth browser test totals invalid')
            runtime.update(product_tests_executed=True,accepted_before_cleanup=True,browser_report=redact(raw.decode('utf-8'),runtime.private_values),state='completed_pending_cleanup',reason='Session-core checks passed; cleaning owned resources')
        except BaseException as exc:
            ledger.recover_interrupted(journal,run_id);current=runtime.row()
            if current['state'] not in ('uncertain_operation','blocked_budget','failed_operation'):
                runtime.update(state='failed_operation',reason='Auth runtime stopped: '+type(exc).__name__)
        finally:runtime.cleanup()
        return runtime.row()
