import datetime,hashlib,io,json,pathlib,secrets,sys,tarfile,time,re
HERE=pathlib.Path(__file__).resolve().parent;WORK=HERE/'auth-integration-workspace'
sys.path.insert(0,'C:/Users/gasto/Documents/Codex/2026-10-01/ejec/work/age50/controller')
import phase3_execution as execution,phase3_sandbox as sandbox
STATE=HERE/'auth-gateway-qualification-intent.json';assert not STATE.exists(),'Inspect previous gateway operation, never retry'
nonce=secrets.token_hex(16);NAME='age50-auth-gateway-'+nonce[:16];TAG=NAME+'-image'
files={name:(WORK/name).read_bytes() for name in ('auth-gateway.mjs','gateway-fixture.test.mjs','ca.pem','server.pem')}
context=io.BytesIO()
with tarfile.open(fileobj=context,mode='w') as tar:
 for name,data in files.items()|{'Dockerfile':(f'FROM {sandbox.NODE}\nLABEL agentapp.auth-gateway.nonce="{nonce}"\nCOPY --chown=1000:1000 work/ /work/\n').encode()}.items():
  path=name if name=='Dockerfile' else 'work/'+name
  info=tarfile.TarInfo(path);info.size=len(data);info.uid=info.gid=1000;info.mode=0o444;tar.addfile(info,io.BytesIO(data))
row={'state':'active','nonce':nonce,'scope':'trusted_gateway_fixture_only','source_sha256':{n:hashlib.sha256(d).hexdigest() for n,d in files.items()},
 'model_calls':0,'product_tests_executed':False,'prior_budget_reset':False,'operations':[],'cleanup':[],
 'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'max_active_ms':120000}
start=time.monotonic();owned=[]
def save():
 tmp=STATE.with_suffix('.tmp');tmp.write_text(json.dumps(row,indent=2)+'\n',encoding='utf-8');tmp.replace(STATE)
def command(argv,timeout=20,stdin=None,allow=False):
 assert time.monotonic()-start+timeout<=120
 op={'argv':argv,'state':'in_flight'};row['operations'].append(op);save();r=execution.process(argv,timeout,stdin,max_output=100000)
 op.update({k:(v.decode('utf-8','replace') if isinstance(v,bytes) else v) for k,v in r.items()});op['state']='uncertain' if r['timed_out'] or r['truncated'] else 'confirmed';save()
 assert op['state']=='confirmed' and (allow or r['exit_code']==0),'Gateway command incomplete/failed'
 return op
save()
try:
 assert command(['docker','container','inspect',NAME],allow=True)['exit_code']!=0
 command(['docker','build','--network','none','--pull=false','--progress','plain','--tag',TAG,'-'],40,context.getvalue())
 image=json.loads(command(['docker','image','inspect',TAG])['stdout'])[0];assert image['Config']['Labels']['agentapp.auth-gateway.nonce']==nonce
 owned.append(('image',image['Id']))
 command(['docker','create','--name',NAME,'--pull','never','--network','none','--read-only','--user','1000:1000','--cap-drop','ALL','--security-opt','no-new-privileges',
  '--cpus','0.4','--memory','256m','--memory-swap','256m','--pids-limit','32','--log-driver','none','--tmpfs','/tmp:rw,nosuid,nodev,noexec,size=32m,uid=1000,gid=1000',
  '--label','agentapp.auth-gateway.nonce='+nonce,image['Id'],'node','-e','setTimeout(()=>process.exit(0),120000)']);owned.append(('container',NAME));command(['docker','start',NAME])
 d=json.loads(command(['docker','container','inspect',NAME])['stdout'])[0];h=d['HostConfig']
 assert d['Image']==image['Id'] and d['Config']['User']=='1000:1000' and d['Config']['Labels']['agentapp.auth-gateway.nonce']==nonce
 assert h['ReadonlyRootfs'] and h['NetworkMode']=='none' and not h.get('Binds') and not h.get('PortBindings') and not h['Privileged']
 assert h['CapDrop']==['ALL'] and h['SecurityOpt']==['no-new-privileges'] and h['NanoCpus']==400000000 and h['Memory']==268435456 and h['MemorySwap']==h['Memory'] and h['PidsLimit']==32
 assert set(h['Tmpfs'])=={'/tmp'} and 'noexec' in h['Tmpfs']['/tmp']
 command(['docker','exec','-i',NAME,'sh','-c','umask 077; cat > /tmp/server-key.pem; chmod 400 /tmp/server-key.pem'],stdin=(HERE/'outputs/auth-tls-private/server-key.pem').read_bytes())
 result=command(['docker','exec','--workdir','/work',NAME,'node','--test','gateway-fixture.test.mjs'],30)
 assert re.search(r'# tests 4\b',result['stdout']) and re.search(r'# pass 4\b',result['stdout']) and re.search(r'# fail 0\b',result['stdout'])
 row.update(state='qualified_trusted_gateway',gateway_fixture_tests=4,auth_implementation_executed=False)
except Exception as exc:row.update(state='needs_attention',error=str(exc));save()
finally:
 for kind,name in reversed(owned):
  r=execution.process(['docker',kind,'inspect','--format','{{index .Config.Labels "agentapp.auth-gateway.nonce"}}',name],10)
  if r['exit_code']==0 and r['stdout'].decode().strip()==nonce:
   r=execution.process(['docker','rm','--force',name] if kind=='container' else ['docker','image','rm',name],20);row['cleanup'].append({'kind':kind,'resource':name,'exit_code':r['exit_code']})
  else:row['cleanup'].append({'kind':kind,'resource':name,'error':'ownership_unconfirmed'})
 if len(row['cleanup'])!=len(owned) or any(r.get('exit_code',1) for r in row['cleanup']):row['state']='needs_attention'
 row['duration_ms']=round((time.monotonic()-start)*1000);save()
print(json.dumps({k:row.get(k) for k in ('state','error','gateway_fixture_tests','duration_ms','cleanup')}));raise SystemExit(0 if row['state']=='qualified_trusted_gateway' else 1)
