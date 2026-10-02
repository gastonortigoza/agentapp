"""Separate locked workspace profile; never weakens directory policy or runs auth."""
import datetime,hashlib,io,json,pathlib,posixpath,re,secrets,sys,tarfile,time
HERE=pathlib.Path(__file__).resolve().parent;WORK=HERE/'auth-integration-workspace'
REPO=pathlib.Path('C:/Users/gasto/Documents/Codex/2026-10-01/ejec/work/age50')
sys.path.insert(0,str(REPO/'controller'))
import phase3_dependencies as deps,phase3_execution as execution,phase3_sandbox as sandbox
STATE=HERE/'auth-integration-qualification-intent.json';CACHE=HERE/'auth-runtime-tarballs'
assert not STATE.exists(),'Existing operation must be inspected, never replayed'
lock=json.loads((WORK/'package-lock.json').read_bytes())
assert lock['lockfileVersion']==3 and len(lock['packages'])<=200
expected={
 'backend':{'fastify':'5.6.1','pg':'8.16.3','@node-rs/argon2':'2.2.1','jose':'6.2.12','@fastify/cookie':'11.1.2',
  'typescript':'5.9.3','tsx':'4.20.6','@types/node':'22.18.0','@types/pg':'8.15.5'},
 'frontend':{'react':'19.2.0','react-dom':'19.2.0','libphonenumber-js':'1.13.14','vite':'6.4.1','typescript':'5.9.3',
  'tsx':'4.20.6','@types/node':'22.18.0','@types/react':'19.2.0','@types/react-dom':'19.2.0','@playwright/test':'1.56.1'}}
for name in ('','backend','frontend'):
 path=WORK/name/'package.json';pkg=json.loads(path.read_bytes());entry=lock['packages'][name]
 assert pkg['private'] is True and pkg['engines']=={'node':'22.18.0','npm':'10.9.3'}
 if name:assert pkg.get('dependencies',{})|pkg.get('devDependencies',{})==expected[name]
 else:assert pkg['workspaces']==['backend','frontend'] and not pkg.get('dependencies') and not pkg.get('devDependencies')
 for field in ('dependencies','devDependencies','engines','workspaces'):assert entry.get(field)==pkg.get(field)
for path,entry in lock['packages'].items():
 if path in ('','backend','frontend'):continue
 if entry.get('link'):
  assert path in ('node_modules/backend','node_modules/frontend') and entry['resolved']==path.removeprefix('node_modules/')
  continue
 assert re.fullmatch(r'node_modules/(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+(?:/node_modules/(?:@[a-z0-9_.-]+/)?[a-z0-9_.-]+)*',path)
 assert re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?',entry['version'])
 assert re.fullmatch(r'https://registry\.npmjs\.org/[A-Za-z0-9_@./%+-]+\.tgz',entry['resolved'])
 assert re.fullmatch(r'sha512-[A-Za-z0-9+/]+={0,2}',entry['integrity'])
refs=deps.references(lock);refs.pop(deps.NPM_INTEGRITY);CACHE.mkdir(exist_ok=True)
old=REPO/'controller/.state/phase3/dependencies/5ed595bf30d6088d1871-linux-x64'
records=[];size=0
for integrity,url in refs.items():
 name=hashlib.sha256(integrity.encode()).hexdigest()+'.tgz';target=CACHE/name
 choices=[target,HERE/'auth-library-tarballs'/name,old/name]
 prior=next((p for p in choices if p.exists()),None)
 if prior:
  assert not prior.is_symlink() and not prior.is_junction() and prior.stat().st_size<=16*1024*1024
 data=prior.read_bytes() if prior else deps.registry(url,integrity)
 assert deps.digest(data)==integrity and len(data)<=16*1024*1024
 size+=len(data);assert size<=100*1024*1024
 if not target.exists():target.write_bytes(data)
 records.append({'url':url,'integrity':integrity,'file':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'reused':bool(prior)})
(HERE/'auth-integration-acquisition.json').write_text(json.dumps({'schema':'agentapp.auth-runtime-acquisition/1','records':records,
 'root_lock_sha256':hashlib.sha256((WORK/'package-lock.json').read_bytes()).hexdigest(),'scripts':False,'platform':'linux-x64-glibc'},indent=2)+'\n',encoding='utf-8')
nonce=secrets.token_hex(16);NAME='age50-auth-wiring-'+nonce[:16];TAG=NAME+'-image';NATIVE=NAME+'-readonly'
files={p.relative_to(WORK).as_posix():p.read_bytes() for p in WORK.rglob('*') if p.is_file()}
def put(tar,name,data,mode=0o444):
 info=tarfile.TarInfo(name);info.size=len(data);info.uid=info.gid=1000;info.mode=mode;info.mtime=0;tar.addfile(info,io.BytesIO(data))
out=io.BytesIO()
with tarfile.open(fileobj=out,mode='w') as tar:
 for n,d in files.items():put(tar,'seed/'+n,d)
 for r in records:put(tar,'tarballs/'+r['file'],(CACHE/r['file']).read_bytes())
 put(tar,'Dockerfile',(f'FROM {sandbox.NODE}\nLABEL agentapp.auth-runtime.nonce="{nonce}"\nCOPY --chown=1000:1000 seed/ /seed/\nCOPY --chown=1000:1000 tarballs/ /tarballs/\n').encode())
row={'state':'active','nonce':nonce,'scope':'trusted_auth_integration_scaffold_only','model_calls':0,'product_tests_executed':False,
 'prior_budget_reset':False,'lock_sha256':hashlib.sha256(files['package-lock.json']).hexdigest(),'operations':[],
 'source_sha256':{n:hashlib.sha256(d).hexdigest() for n,d in files.items()},'cleanup':[],
 'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'max_active_ms':360000}
start=time.monotonic();owned=[]
def save():
 tmp=STATE.with_suffix('.tmp');tmp.write_text(json.dumps(row,indent=2)+'\n',encoding='utf-8');tmp.replace(STATE)
def command(argv,timeout=30,stdin=None,allow=False,binary=False):
 assert time.monotonic()-start+timeout<=360,'Aggregate bound'
 op={'argv':argv,'state':'in_flight'};row['operations'].append(op);save()
 r=execution.process(argv,timeout,stdin=stdin,max_output=220*1024*1024 if binary else 200000)
 op.update({k:v for k,v in r.items() if k not in ('stdout','stderr')})
 op['stdout']=hashlib.sha256(r['stdout']).hexdigest() if binary else r['stdout'].decode('utf-8','replace')
 op['stderr']=r['stderr'].decode('utf-8','replace');op['state']='uncertain' if r['timed_out'] or r['truncated'] else 'confirmed';save()
 assert op['state']=='confirmed','Uncertain command; never replay'
 assert allow or r['exit_code']==0,'Failed command'
 return r['stdout'] if binary else op
def inspect(kind,name):return json.loads(command(['docker',kind,'inspect',name])['stdout'])[0]
def guard(d,image_id,mutable=False):
 h=d['HostConfig'];assert d['Image']==image_id and d['Config']['Labels']['agentapp.auth-runtime.nonce']==nonce
 assert d['Config']['User']=='1000:1000' and h['NetworkMode']=='none' and h['ReadonlyRootfs'] and not h['Privileged']
 assert h['CapDrop']==['ALL'] and h['SecurityOpt']==['no-new-privileges'] and not h.get('Binds') and not h.get('PortBindings')
 assert h['Memory']==1073741824 and h['NanoCpus']==800000000 and h['PidsLimit']==128
 assert set(h['Tmpfs'])==({'/tmp','/work','/cache'} if mutable else {'/tmp'})
 assert all('noexec' in options for options in h['Tmpfs'].values())
def container(name,image_id,mutable=False):
 assert command(['docker','container','inspect',name],allow=True)['exit_code']!=0
 args=['docker','create','--name',name,'--pull','never','--network','none','--read-only','--user','1000:1000','--cap-drop','ALL',
  '--security-opt','no-new-privileges','--cpus','0.8','--memory','1024m','--pids-limit','128',
  '--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=128m,uid=1000,gid=1000','--label','agentapp.auth-runtime.nonce='+nonce]
 if mutable:args+=['--tmpfs','/work:rw,nosuid,nodev,noexec,size=512m,uid=1000,gid=1000','--tmpfs','/cache:rw,nosuid,nodev,noexec,size=128m,uid=1000,gid=1000']
 command(args+[image_id,'node','-e','setTimeout(()=>process.exit(0),360000)']);owned.append(('container',name))
 command(['docker','start',name]);guard(inspect('container',name),image_id,mutable)
save()
try:
 command(['docker','build','--network','none','--pull=false','--progress','plain','--tag',TAG,'-'],90,out.getvalue())
 d=inspect('image',TAG);assert d['Config']['Labels']['agentapp.auth-runtime.nonce']==nonce;image=d['Id'];owned.append(('image',image))
 container(NAME,image,True)
 command(['docker','exec',NAME,'node','--version']);command(['docker','exec',NAME,'npm','--version'])
 command(['docker','exec',NAME,'node','-e',"require('fs').cpSync('/seed','/work',{recursive:true})"])
 command(['docker','exec',NAME,'npm','cache','add',*['/tarballs/'+r['file'] for r in records],'--offline','--ignore-scripts','--no-audit','--fund=false','--cache','/cache'],60)
 command(['docker','exec','--workdir','/work',NAME,'npm','ci','--offline','--ignore-scripts','--no-audit','--fund=false','--engine-strict','--cache','/cache'],60)
 raw=command(['docker','exec',NAME,'tar','-C','/work','-cf','-','node_modules'],30,binary=True)
 context=io.BytesIO();total=0
 with tarfile.open(fileobj=context,mode='w') as tar:
  for n,d in files.items():put(tar,'work/'+n,d)
  with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as source:
   for m in source.getmembers():
    assert not m.name.startswith('/') and m.name.split('/')[0]=='node_modules' and '..' not in m.name.split('/')
    info=execution.copy_tar_member(m,'work/'+m.name)
    if m.issym():
     assert not m.linkname.startswith('/')
     assert posixpath.normpath(posixpath.join(posixpath.dirname(info.name),m.linkname)).startswith('work/')
     tar.addfile(info)
    elif m.isdir():tar.addfile(info)
    else:
     assert m.isfile() and m.size<=16*1024*1024;total+=m.size;assert total<=220*1024*1024
     tar.addfile(info,source.extractfile(m))
  put(tar,'Dockerfile',(f'FROM {sandbox.NODE}\nLABEL agentapp.auth-runtime.nonce="{nonce}"\nCOPY --chown=1000:1000 work/ /work/\n').encode())
 command(['docker','build','--network','none','--pull=false','--progress','plain','--tag',NATIVE+'-image','-'],90,context.getvalue())
 d=inspect('image',NATIVE+'-image');assert d['Config']['Labels']['agentapp.auth-runtime.nonce']==nonce;native=d['Id'];owned.append(('image',native))
 container(NATIVE,native)

 # These checks exercise new operator wiring, not the previously qualified libraries.
 assert row['lock_sha256']=='e4c4b4ad25631363aa7518383e1fe337d85b75fe726b622bac0ed9e1c5a756a6'
 assert not (WORK/'backend/src/auth.ts').exists() and not (WORK/'frontend/src/AuthPages.tsx').exists()
 command(['docker','exec','--workdir','/work/backend',NATIVE,'node','../node_modules/typescript/bin/tsc','--noEmit','-p','tsconfig.wiring.json'],30)
 command(['docker','exec','--workdir','/work/frontend',NATIVE,'node','../node_modules/typescript/bin/tsc','--noEmit','-p','tsconfig.wiring.json'],30)
 units=command(['docker','exec','--workdir','/work',NATIVE,'node','--import','tsx','--test','backend/tests/session-app.test.ts'],30)
 assert re.search(r'# tests 4\b',units['stdout']) and re.search(r'# pass 4\b',units['stdout']) and re.search(r'# fail 0\b',units['stdout'])
 command(['docker','exec','--workdir','/work/frontend',NATIVE,'node','../node_modules/vite/bin/vite.js','build','--config','vite.wiring.config.ts','--configLoader','runner'],30)
 dist=command(['docker','exec',NATIVE,'tar','-C','/tmp/auth-wiring-dist','-cf','-','.'],30,binary=True)
 signed=json.loads((HERE/'auth-chromium-tls-qualification-intent.json').read_bytes())
 metadata=json.loads((HERE/'auth-browser-tools-acquisition.json').read_bytes())
 assert signed['state']=='qualified_disposable_chromium_TLS' and signed['ubuntu_signature_verified']
 assert signed['tool_acquisition_sha256']==hashlib.sha256((HERE/'auth-browser-tools-acquisition.json').read_bytes()).hexdigest()
 assert hashlib.sha256(files['browser-tools.deb']).hexdigest()==metadata['files']['libnss3-tools.deb']['sha256']
 tools=command(['docker','exec',NATIVE,'dpkg-deb','--fsys-tarfile','/work/browser-tools.deb'],30,binary=True)
 with tarfile.open(fileobj=io.BytesIO(tools),mode='r:') as source:
  item=source.getmember('./usr/bin/certutil');assert item.isfile() and item.size<=16*1024*1024
  certutil=source.extractfile(item).read()
 assert hashlib.sha256(certutil).hexdigest()==signed['certutil_binary_sha256']
 browser_context=io.BytesIO()
 with tarfile.open(fileobj=browser_context,mode='w') as target:
  for name,data in files.items():
   if name!='browser-tools.deb':put(target,'work/'+name,data)
  with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as source:
   for m in source.getmembers():
    assert not m.name.startswith('/') and m.name.split('/')[0]=='node_modules' and '..' not in m.name.split('/')
    i=execution.copy_tar_member(m,'work/'+m.name)
    if m.issym():
     assert not m.linkname.startswith('/') and posixpath.normpath(posixpath.join(posixpath.dirname(i.name),m.linkname)).startswith('work/')
     target.addfile(i)
    elif m.isdir():target.addfile(i)
    else:assert m.isfile() and m.size<=16*1024*1024;target.addfile(i,source.extractfile(m))
  with tarfile.open(fileobj=io.BytesIO(dist),mode='r:') as source:
   for m in source.getmembers():
    assert not m.name.startswith('/') and '..' not in m.name.split('/')
    if m.isdir():continue
    assert m.isfile() and m.size<=16*1024*1024
    i=execution.copy_tar_member(m,'work/dist/'+posixpath.normpath(m.name));target.addfile(i,source.extractfile(m))
  put(target,'tool/certutil',certutil,0o555)
  put(target,'Dockerfile',(f'FROM {sandbox.BROWSER}\nLABEL agentapp.auth-runtime.nonce="{nonce}"\nCOPY --chown=1000:1000 work/ /work/\nCOPY --chown=1000:1000 tool/certutil /usr/bin/certutil\n').encode())
 browser_name=NAME+'-browser'
 command(['docker','build','--network','none','--pull=false','--progress','plain','--tag',browser_name+'-image','-'],60,browser_context.getvalue())
 b=inspect('image',browser_name+'-image');assert b['Config']['Labels']['agentapp.auth-runtime.nonce']==nonce;browser_id=b['Id'];owned.append(('image',browser_id))
 assert command(['docker','container','inspect',browser_name],allow=True)['exit_code']!=0
 command(['docker','create','--name',browser_name,'--pull','never','--network','none','--read-only','--user','1000:1000','--cap-drop','ALL','--security-opt','no-new-privileges','--cpus','0.8','--memory','1536m','--memory-swap','1536m','--pids-limit','128','--log-driver','none','--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=512m,uid=1000,gid=1000','--label','agentapp.auth-runtime.nonce='+nonce,browser_id,'node','-e','setTimeout(()=>process.exit(0),360000)'])
 owned.append(('container',browser_name));command(['docker','start',browser_name])
 b=inspect('container',browser_name);h=b['HostConfig']
 assert b['Image']==browser_id and b['Config']['User']=='1000:1000' and b['Config']['Labels']['agentapp.auth-runtime.nonce']==nonce
 assert h['ReadonlyRootfs'] and h['NetworkMode']=='none' and not h.get('Binds') and not h.get('PortBindings') and not h['Privileged']
 assert h['CapDrop']==['ALL'] and h['SecurityOpt']==['no-new-privileges'] and h['Memory']==1610612736 and h['MemorySwap']==h['Memory']
 assert h['NanoCpus']==800000000 and h['PidsLimit']==128 and set(h['Tmpfs'])=={'/tmp'} and 'noexec' in h['Tmpfs']['/tmp']
 # Secret input is never recorded or baked into an image. Synthetic browser only.
 command(['docker','exec','-i',browser_name,'sh','-c','umask 077; cat > /tmp/server-key.pem; chmod 400 /tmp/server-key.pem'],stdin=(HERE/'outputs/auth-tls-private/server-key.pem').read_bytes())
 result=command(['docker','exec','--workdir','/work','--env','AUTH_WIRING_FIXTURE=1',browser_name,'node','wiring-browser-check.mjs'],60)
 check=json.loads(result['stdout'].strip());assert check['state']=='qualified_trusted_auth_wiring' and len(check['checks'])==9
 row.update(state='qualified_trusted_auth_wiring',runtime_check=check,adapter_unit_tests=4,frontend_backend_types=True,
  fixture_build=True,dependencies=len(records),acquired_bytes=size,readonly_modules_bytes=total,
  native_modules_rerun=False,prior_TLS_qualification_rerun=False,product_sources_absent=True,host_trust_changed=False)

except Exception as exc:row.update(state='needs_attention',error=str(exc));save()
finally:
 for kind,name in reversed(owned):
  r=execution.process(['docker',kind,'inspect',name],10)
  if r['exit_code']==0 and json.loads(r['stdout'])[0]['Config'].get('Labels',{}).get('agentapp.auth-runtime.nonce')==nonce:
   r=execution.process(['docker','rm','--force',name] if kind=='container' else ['docker','image','rm',name],20)
   row['cleanup'].append({'kind':kind,'resource':name,'exit_code':r['exit_code']})
  else:row['cleanup'].append({'kind':kind,'resource':name,'error':'ownership_unconfirmed'})
 if len(row['cleanup'])!=len(owned) or any(r.get('exit_code',1) for r in row['cleanup']):row['state']='needs_attention'
 row['duration_ms']=round((time.monotonic()-start)*1000);save()
print(json.dumps({k:row.get(k) for k in ('state','error','dependencies','duration_ms','runtime_check','cleanup')}))
raise SystemExit(0 if row['state']=='qualified_trusted_auth_wiring' else 1)
