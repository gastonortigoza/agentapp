"""Qualify disposable schema/grants/transactions. Never run an auth implementation."""
import datetime,hashlib,json,pathlib,secrets,sys,time
from concurrent.futures import ThreadPoolExecutor
HERE=pathlib.Path(__file__).resolve().parent;REPO=pathlib.Path('C:/Users/gasto/Documents/Codex/2026-10-01/ejec/work/age50')
sys.path.insert(0,str(REPO/'controller'))
import phase3_execution as execution,phase3_sandbox as sandbox
STATE=HERE/'auth-database-qualification-intent.json';PRIVATE=HERE/'outputs/auth-database-private'
assert not STATE.exists() and not PRIVATE.exists(),'Existing DB operation must be reconciled, never replayed'
nonce=secrets.token_hex(16);NAME='age50-auth-db-'+nonce[:16]
passwords={n:secrets.token_hex(32) for n in ('postgres','age50_auth_app','age50_auth_fixture')}
PRIVATE.mkdir();env=PRIVATE/'postgres.env';env.write_text('POSTGRES_PASSWORD='+passwords['postgres']+'\n',encoding='utf-8')
schema=(HERE/'auth-session-workspace/schema.sql').read_bytes()
row={'state':'active','nonce':nonce,'name':NAME,'scope':'disposable_auth_database_infrastructure_only',
 'model_calls':0,'product_tests_executed':False,'prior_budget_reset':False,'operations':[], 'checks':[], 'cleanup':[],
 'schema_sha256':hashlib.sha256(schema).hexdigest(),'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'max_active_ms':180000,'bootstrap_calls':0}
start=time.monotonic();created=False
def redact(text):
 for secret in passwords.values():text=text.replace(secret,'[redacted]')
 return text
def save():
 tmp=STATE.with_suffix('.tmp');tmp.write_text(json.dumps(row,indent=2)+'\n',encoding='utf-8');tmp.replace(STATE)
def command(argv,timeout=15,stdin=None,allow=False):
 assert time.monotonic()-start+timeout<=180,'Aggregate DB bound'
 op={'argv':argv,'state':'in_flight'}
 if stdin is not None:op['input_sha256']=hashlib.sha256(stdin).hexdigest()
 row['operations'].append(op);save()
 r=execution.process(argv,timeout,stdin=stdin,max_output=100000)
 op.update({k:v for k,v in r.items() if k not in ('stdout','stderr')})
 op['stdout']=redact(r['stdout'].decode('utf-8','replace'));op['stderr']=redact(r['stderr'].decode('utf-8','replace'))
 op['state']='uncertain' if r['timed_out'] or r['truncated'] else 'confirmed';save()
 assert op['state']=='confirmed','Uncertain DB command; do not repeat'
 assert allow or r['exit_code']==0,'Database infrastructure command failed'
 return op
def sql(role,text,allow=False,timeout=15):
 return command(['docker','exec','-i','--env','PGPASSFILE=/tmp/auth.pgpass',NAME,'psql','-X','-qAt','-h','127.0.0.1',
  '-U',role,'-d','age50_auth_test','-v','ON_ERROR_STOP=1','-v','VERBOSITY=verbose'],timeout,text.encode(),allow)
def check(name,condition):
 assert condition,name;row['checks'].append({'name':name,'passed':True});save()
save()
try:
 absent=command(['docker','container','inspect',NAME],allow=True);assert absent['exit_code']!=0
 argv=execution.pg_argv(NAME,'auth-db-infrastructure',nonce)
 argv[argv.index('POSTGRES_DB=age50_test')]='POSTGRES_DB=age50_auth_test'
 idx=argv.index(sandbox.POSTGRES);argv[idx:idx]=['--env-file',str(env)]
 # Preserve bounded readonly/none-network policy; isolated new role scope only.
 argv[-1]=argv[-1].replace('sleep 900','sleep 180')
 command(argv);created=True;command(['docker','start',NAME])
 h=json.loads(command(['docker','container','inspect','--format','{{json .HostConfig}}',NAME])['stdout'])
 user=command(['docker','container','inspect','--format','{{.Config.User}}',NAME])['stdout'].strip()
 check('effective_isolation',user=='999:999' and h['ReadonlyRootfs'] and h['NetworkMode']=='none' and not h['Privileged']
  and not h.get('Binds') and not h.get('PortBindings') and h['CapDrop']==['ALL'] and h['SecurityOpt']==['no-new-privileges']
  and h['Memory']==805306368 and h['MemorySwap']==805306368 and h['NanoCpus']==400000000 and h['PidsLimit']==64
  and all('noexec' in v for v in h['Tmpfs'].values()))
 deadline=time.monotonic()+40
 while True:
  r=command(['docker','exec',NAME,'pg_isready','-h','127.0.0.1','-U','postgres','-d','age50_auth_test'],5,allow=True)
  if r['exit_code']==0:break
  assert time.monotonic()<deadline,'Final TCP PostgreSQL server not ready';time.sleep(.5)
 check('final_tcp_readiness_not_bootstrap_socket',True)
 pgpass=''.join('127.0.0.1:5432:age50_auth_test:'+role+':'+secret+'\n' for role,secret in passwords.items())
 command(['docker','exec','-i',NAME,'sh','-c','umask 077; cat > /tmp/auth.pgpass; chmod 400 /tmp/auth.pgpass'],stdin=pgpass.encode())
 setup="BEGIN; CREATE ROLE age50_auth_migrator NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;\n"
 for role in ('age50_auth_app','age50_auth_fixture'):
  setup+=f"CREATE ROLE {role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '{passwords[role]}';\n"
 setup+="REVOKE CONNECT,TEMP ON DATABASE age50_auth_test FROM PUBLIC; GRANT CONNECT ON DATABASE age50_auth_test TO age50_auth_app,age50_auth_fixture;\n"
 setup+=schema.decode()+"\nCOMMIT;\n";row['bootstrap_calls']+=1;save();sql('postgres',setup)
 check('atomic_bootstrap_once',row['bootstrap_calls']==1)
 role=sql('age50_auth_app',"SELECT current_user,current_database(),rolsuper,rolcreatedb,rolcreaterole,rolreplication FROM pg_roles WHERE rolname=current_user;")['stdout'].strip()
 check('authenticated_app_nonprivileged',role=='age50_auth_app|age50_auth_test|f|f|f|f')
 uid='00000000-0000-4000-8000-000000000001';sid='00000000-0000-4000-8000-000000000002';fid='00000000-0000-4000-8000-000000000003'
 sql('age50_auth_app',f"INSERT INTO agentapp.users(id,email,password_hash,birth_date) VALUES('{uid}','fixture@example.invalid','infrastructure-placeholder','2000-01-01');")
 sql('age50_auth_app',f"BEGIN; SELECT id FROM agentapp.users WHERE id='{uid}' FOR UPDATE; INSERT INTO agentapp.sessions(id,user_id,family_id,refresh_hash,expires_at) VALUES('{sid}','{uid}','{fid}','infra-rollback',now()+interval '1 day'); ROLLBACK;")
 check('session_rollback_persisted_zero',sql('age50_auth_fixture','SELECT count(*) FROM agentapp.sessions;')['stdout'].strip()=='0')
 sql('age50_auth_app',f"INSERT INTO agentapp.sessions(id,user_id,family_id,refresh_hash,expires_at) VALUES('{sid}','{uid}','{fid}','infra-commit',now()+interval '1 day'); BEGIN; UPDATE agentapp.sessions SET revoked_at=now() WHERE family_id='{fid}'; COMMIT;")
 check('session_revocation_commit_visible',sql('age50_auth_fixture',f"SELECT count(*) FROM agentapp.sessions WHERE family_id='{fid}' AND revoked_at IS NOT NULL;")['stdout'].strip()=='1')
 denies={
  'users_delete':f"DELETE FROM agentapp.users WHERE id='{uid}';",
  'users_truncate':'TRUNCATE agentapp.users CASCADE;',
  'email_update':f"UPDATE agentapp.users SET email='other@example.invalid' WHERE id='{uid}';",
  'birth_date_update':f"UPDATE agentapp.users SET birth_date='2001-01-01' WHERE id='{uid}';",
  'create_table':'CREATE TABLE agentapp.not_allowed(id integer);',
  'role_escalation':'SET ROLE age50_auth_migrator;',
  'create_temp_table':'CREATE TEMP TABLE not_allowed(id integer);'}
 for name,text in denies.items():
  r=sql('age50_auth_app',text,allow=True);check('denied_'+name,r['exit_code']!=0 and '42501' in r['stderr'])
 # Holder uses a trusted test channel; wait until the lock is observed in PG before contender.
 holder_sql=f"BEGIN; SELECT id FROM agentapp.users WHERE id='{uid}' FOR UPDATE; SELECT pg_sleep(2); COMMIT;"
 with ThreadPoolExecutor(max_workers=1) as pool:
  argv=['docker','exec','-i','--env','PGPASSFILE=/tmp/auth.pgpass',NAME,'psql','-X','-qAt','-h','127.0.0.1','-U','age50_auth_app','-d','age50_auth_test','-v','ON_ERROR_STOP=1']
  op={'argv':argv,'state':'in_flight','input_sha256':hashlib.sha256(holder_sql.encode()).hexdigest()};row['operations'].append(op);save()
  future=pool.submit(execution.process,argv,8,holder_sql.encode())
  deadline=time.monotonic()+3
  while True:
   locks=sql('postgres',"SELECT count(*) FROM pg_stat_activity WHERE usename='age50_auth_app' AND wait_event='PgSleep';")['stdout'].strip()
   if locks=='1':break
   assert time.monotonic()<deadline,'Holder lock not observed';time.sleep(.1)
  r=sql('age50_auth_app',f"BEGIN; SET LOCAL lock_timeout='200ms'; SELECT id FROM agentapp.users WHERE id='{uid}' FOR UPDATE; COMMIT;",allow=True)
  check('same_user_row_lock_serializes',r['exit_code']!=0 and '55P03' in r['stderr'])
  result=future.result();op.update({k:v for k,v in result.items() if k not in ('stdout','stderr')});op['state']='confirmed' if not result['timed_out'] else 'uncertain';save()
  check('holder_commit_confirmed',result['exit_code']==0 and not result['timed_out'] and not result['truncated'])
 check('lock_released_after_commit',sql('age50_auth_app',f"BEGIN; SET LOCAL lock_timeout='200ms'; SELECT id FROM agentapp.users WHERE id='{uid}' FOR UPDATE; COMMIT;")['exit_code']==0)
 sql('age50_auth_fixture','TRUNCATE agentapp.sessions,agentapp.users;')
 check('fixture_reset_zero',sql('age50_auth_fixture','SELECT (SELECT count(*) FROM agentapp.users),(SELECT count(*) FROM agentapp.sessions);')['stdout'].strip()=='0|0')
 row['state']='qualified_database_infrastructure'
except Exception as exc:row.update(state='needs_attention',error=redact(str(exc)));save()
finally:
 if created:
  r=execution.process(['docker','container','inspect','--format','{{index .Config.Labels "agentapp.phase3.nonce"}}',NAME],10)
  if r['exit_code']==0 and r['stdout'].decode().strip()==nonce:
   r=execution.process(['docker','rm','--force',NAME],20);row['cleanup'].append({'container':NAME,'exit_code':r['exit_code']})
  else:row['cleanup'].append({'container':NAME,'error':'ownership_unconfirmed'})
 if created and (len(row['cleanup'])!=1 or row['cleanup'][0].get('exit_code',1)!=0):row['state']='needs_attention'
 row['duration_ms']=round((time.monotonic()-start)*1000);save()
print(json.dumps({k:row.get(k) for k in ('state','error','checks','duration_ms','cleanup','product_tests_executed')}))
raise SystemExit(0 if row['state']=='qualified_database_infrastructure' else 1)
