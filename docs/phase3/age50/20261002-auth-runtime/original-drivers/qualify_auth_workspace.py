"""Separate locked workspace profile; never weakens directory policy or runs auth."""
import datetime,hashlib,io,json,pathlib,posixpath,re,secrets,sys,tarfile,time
HERE=pathlib.Path(__file__).resolve().parent;WORK=HERE/'auth-session-workspace'
REPO=pathlib.Path('C:/Users/gasto/Documents/Codex/2026-10-01/ejec/work/age50')
sys.path.insert(0,str(REPO/'controller'))
import phase3_dependencies as deps,phase3_execution as execution,phase3_sandbox as sandbox
STATE=HERE/'auth-runtime-qualification-intent.json';CACHE=HERE/'auth-runtime-tarballs'
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
(HERE/'auth-runtime-acquisition.json').write_text(json.dumps({'schema':'agentapp.auth-runtime-acquisition/1','records':records,
 'root_lock_sha256':hashlib.sha256((WORK/'package-lock.json').read_bytes()).hexdigest(),'scripts':False,'platform':'linux-x64-glibc'},indent=2)+'\n',encoding='utf-8')
nonce=secrets.token_hex(16);NAME='age50-auth-runtime-'+nonce[:16];TAG=NAME+'-image';NATIVE=NAME+'-readonly'
files={p.relative_to(WORK).as_posix():p.read_bytes() for p in WORK.rglob('*') if p.is_file()}
def put(tar,name,data,mode=0o444):
 info=tarfile.TarInfo(name);info.size=len(data);info.uid=info.gid=1000;info.mode=mode;info.mtime=0;tar.addfile(info,io.BytesIO(data))
out=io.BytesIO()
with tarfile.open(fileobj=out,mode='w') as tar:
 for n,d in files.items():put(tar,'seed/'+n,d)
 for r in records:put(tar,'tarballs/'+r['file'],(CACHE/r['file']).read_bytes())
 put(tar,'Dockerfile',(f'FROM {sandbox.NODE}\nLABEL agentapp.auth-runtime.nonce="{nonce}"\nCOPY --chown=1000:1000 seed/ /seed/\nCOPY --chown=1000:1000 tarballs/ /tarballs/\n').encode())
row={'state':'active','nonce':nonce,'scope':'auth_runtime_infrastructure_only','model_calls':0,'product_tests_executed':False,
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
 result=command(['docker','exec','--workdir','/work',NATIVE,'node','--import','tsx','runtime-input-check.mjs'],30)
 check=json.loads(result['stdout'].strip());assert check['state']=='runtime_inputs_verified'
 row.update(state='qualified_runtime_inputs',runtime_check=check,dependencies=len(records),acquired_bytes=size,readonly_modules_bytes=total)
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
raise SystemExit(0 if row['state']=='qualified_runtime_inputs' else 1)
