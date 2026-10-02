"""Signed NSS tools + isolated Chromium TLS; COPY-only builds, no host trust changes."""
import datetime,hashlib,io,json,pathlib,secrets,sys,tarfile,time
HERE=pathlib.Path(__file__).resolve().parent;REPO=pathlib.Path('C:/Users/gasto/Documents/Codex/2026-10-01/ejec/work/age50')
sys.path.insert(0,str(REPO/'controller'))
import phase3_execution as execution,phase3_sandbox as sandbox,phase3_dependencies as deps
STATE=HERE/'auth-chromium-tls-qualification-intent.json';TOOLS=HERE/'auth-browser-tools';PRIVATE=HERE/'outputs/auth-tls-private'
assert not STATE.exists(),'Existing TLS operation must be inspected, never repeated'
metadata=json.loads((HERE/'auth-browser-tools-acquisition.json').read_bytes())
assert metadata['state']=='acquired_pending_signature_verification'
for name,record in metadata['files'].items():assert hashlib.sha256((TOOLS/name).read_bytes()).hexdigest()==record['sha256']
tls=json.loads((HERE/'auth-tls-preparation.json').read_bytes());assert tls['state']=='tls_probe_verified'
for name,sha in tls['sha256'].items():assert hashlib.sha256((PRIVATE/name).read_bytes()).hexdigest()==sha
lock=json.loads((HERE/'auth-session-workspace/package-lock.json').read_bytes())
nonce=secrets.token_hex(16);NAME='age50-auth-browser-'+nonce[:16];TAG=NAME+'-image';FINAL=NAME+'-tls'
files={'auth-browser-tls-fixture.mjs':(HERE/'auth-browser-tls-fixture.mjs').read_bytes(),
 'ca.pem':(PRIVATE/'ca.pem').read_bytes(),'server.pem':(PRIVATE/'server.pem').read_bytes()}
total=0
for name in ('@playwright/test','playwright','playwright-core'):
 entry=lock['packages']['node_modules/'+name];assert entry['version']=='1.56.1'
 raw=(HERE/'auth-runtime-tarballs'/(hashlib.sha256(entry['integrity'].encode()).hexdigest()+'.tgz')).read_bytes()
 assert deps.digest(raw)==entry['integrity']
 with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as package:
  for m in package.getmembers():
   parts=m.name.split('/');assert parts[0]=='package' and not any(p in ('..','') for p in parts)
   if m.isdir():continue
   assert m.isfile() and m.size<=16*1024*1024
   total+=m.size;assert total<=40*1024*1024
   files['node_modules/'+name+'/'+ '/'.join(parts[1:])]=package.extractfile(m).read()
row={'state':'active','scope':'isolated_chromium_TLS_infrastructure_only','nonce':nonce,'operations':[],'cleanup':[],
 'model_calls':0,'product_tests_executed':False,'prior_budget_reset':False,'host_trust_changed':False,
 'certificate_validation_disabled':False,'private_key_published':False,'max_active_ms':240000,
 'fixture_sha256':hashlib.sha256(files['auth-browser-tls-fixture.mjs']).hexdigest(),
 'public_CA_sha256':tls['sha256']['ca.pem'],'public_server_certificate_sha256':tls['sha256']['server.pem'],
 'tool_acquisition_sha256':hashlib.sha256((HERE/'auth-browser-tools-acquisition.json').read_bytes()).hexdigest(),
 'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
owned=[];start=time.monotonic()
def save():
 tmp=STATE.with_suffix('.tmp');tmp.write_text(json.dumps(row,indent=2)+'\n',encoding='utf-8');tmp.replace(STATE)
def command(argv,timeout=30,stdin=None,allow=False,binary=False,private=False):
 assert time.monotonic()-start+timeout<=240,'Aggregate TLS bound'
 op={'argv':argv,'state':'in_flight'}
 if stdin is not None and not private:op['input_sha256']=hashlib.sha256(stdin).hexdigest()
 if private:op['private_input_not_recorded']=True
 row['operations'].append(op);save()
 r=execution.process(argv,timeout,stdin=stdin,max_output=40*1024*1024 if binary else 200000)
 op.update({k:v for k,v in r.items() if k not in ('stdout','stderr')})
 op['stdout']=hashlib.sha256(r['stdout']).hexdigest() if binary else r['stdout'].decode('utf-8','replace')
 op['stderr']=r['stderr'].decode('utf-8','replace');op['state']='uncertain' if r['timed_out'] or r['truncated'] else 'confirmed';save()
 assert op['state']=='confirmed','Uncertain operation; never repeat'
 assert allow or r['exit_code']==0,'TLS qualification operation failed'
 return r['stdout'] if binary else op
def archive(files):
 out=io.BytesIO()
 with tarfile.open(fileobj=out,mode='w') as tar:
  for name,data in files.items():
   i=tarfile.TarInfo(name);i.size=len(data);i.mode=0o555 if name.endswith('/certutil') else 0o444;i.uid=i.gid=1000;i.mtime=0;tar.addfile(i,io.BytesIO(data))
 return out.getvalue()
def build(name,items):
 items=dict(items);items['Dockerfile']=(f'FROM {sandbox.BROWSER}\nLABEL agentapp.auth-browser.nonce="{nonce}"\nCOPY --chown=1000:1000 work/ /work/\n'+('COPY --chown=1000:1000 tool/certutil /usr/bin/certutil\n' if 'tool/certutil' in items else '')).encode()
 command(['docker','build','--network','none','--pull=false','--progress','plain','--tag',name+'-image','-'],60,archive(items))
 d=json.loads(command(['docker','image','inspect',name+'-image'])['stdout'])[0]
 assert d['Config']['Labels']['agentapp.auth-browser.nonce']==nonce;owned.append(('image',d['Id']));return d['Id']
def container(name,image):
 assert command(['docker','container','inspect',name],allow=True)['exit_code']!=0
 command(['docker','create','--name',name,'--pull','never','--network','none','--read-only','--user','1000:1000','--cap-drop','ALL',
  '--security-opt','no-new-privileges','--cpus','0.8','--memory','1536m','--memory-swap','1536m','--pids-limit','128','--log-driver','none',
  '--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=512m,uid=1000,gid=1000','--label','agentapp.auth-browser.nonce='+nonce,
  image,'node','-e','setTimeout(()=>process.exit(0),240000)']);owned.append(('container',name));command(['docker','start',name])
 h=json.loads(command(['docker','container','inspect','--format','{{json .HostConfig}}',name])['stdout'])
 assert h['ReadonlyRootfs'] and h['NetworkMode']=='none' and not h.get('Binds') and not h.get('PortBindings') and not h['Privileged']
 assert h['CapDrop']==['ALL'] and h['SecurityOpt']==['no-new-privileges'] and h['Memory']==1610612736 and h['MemorySwap']==h['Memory']
 assert h['NanoCpus']==800000000 and h['PidsLimit']==128 and set(h['Tmpfs'])=={'/tmp'} and 'noexec' in h['Tmpfs']['/tmp']
save()
try:
 items={'work/'+n:d for n,d in files.items()}
 items.update({'work/tools/'+n:(TOOLS/n).read_bytes() for n in ('InRelease','libnss3-tools.deb')})
 image=build(NAME,items);container(NAME,image)
 command(['docker','exec',NAME,'gpgv','--keyring','/usr/share/keyrings/ubuntu-archive-keyring.gpg','/work/tools/InRelease'])
 row['ubuntu_signature_verified']=True;save()
 raw=command(['docker','exec',NAME,'dpkg-deb','--fsys-tarfile','/work/tools/libnss3-tools.deb'],30,binary=True)
 with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as tar:
  m=tar.getmember('./usr/bin/certutil');assert m.isfile() and m.size<=16*1024*1024
  certutil=tar.extractfile(m).read();row['certutil_binary_sha256']=hashlib.sha256(certutil).hexdigest();save()
 final=build(FINAL,{'work/'+n:d for n,d in files.items()}|{'tool/certutil':certutil});container(FINAL,final)
 command(['docker','exec','-i',FINAL,'sh','-c','umask 077; cat > /tmp/server-key.pem; chmod 400 /tmp/server-key.pem'],stdin=(PRIVATE/'server-key.pem').read_bytes(),private=True)
 r=command(['docker','exec','--workdir','/work','--env','AUTH_TLS_FIXTURE=1',FINAL,'node','auth-browser-tls-fixture.mjs'],60)
 result=json.loads(r['stdout'].strip());assert result['state']=='qualified_disposable_chromium_TLS' and len(result['checks'])==8
 row.update(state='qualified_disposable_chromium_TLS',browser_check=result)
except Exception as exc:row.update(state='needs_attention',error=str(exc));save()
finally:
 for kind,name in reversed(owned):
  r=execution.process(['docker',kind,'inspect','--format','{{index .Config.Labels "agentapp.auth-browser.nonce"}}',name],10)
  if r['exit_code']==0 and r['stdout'].decode().strip()==nonce:
   r=execution.process(['docker','rm','--force',name] if kind=='container' else ['docker','image','rm',name],20)
   row['cleanup'].append({'kind':kind,'resource':name,'exit_code':r['exit_code']})
  else:row['cleanup'].append({'kind':kind,'resource':name,'error':'ownership_unconfirmed'})
 if len(row['cleanup'])!=len(owned) or any(x.get('exit_code',1) for x in row['cleanup']):row['state']='needs_attention'
 row['duration_ms']=round((time.monotonic()-start)*1000);save()
print(json.dumps({k:row.get(k) for k in ('state','error','browser_check','ubuntu_signature_verified','duration_ms','cleanup')}))
raise SystemExit(0 if row['state']=='qualified_disposable_chromium_TLS' else 1)
