"""Trusted local PostgreSQL provisioner; never accepts model commands or SQL."""
import argparse,datetime,hashlib,json,os,pathlib,secrets,subprocess,time,uuid
ROOT=pathlib.Path(__file__).resolve().parents[1]
PACKAGE=pathlib.Path(__file__).resolve().parent if pathlib.Path(__file__).with_name('001_initial.sql').exists() else ROOT/'outputs/AGE-34-postgresql'
STATE=pathlib.Path(os.environ['LOCALAPPDATA'])/'AgentApp/age34'
IMAGE='postgres@sha256:5a5a84b19854a9ffaa54082c166ff4ec27473a361e496e5ea167f298f2da9722'
NET='agentapp-age34-local'
HOST_NET='agentapp-age34-host'
TARGETS={'dev':('agentapp-age34-dev','agentapp_age34_dev',55434),'test':('agentapp-age34-test','agentapp_age34_test',55435)}
LABEL='agentapp.task=AGE-34'

def docker(*args,input=None,timeout=60):
    p=subprocess.run(['docker',*args],input=input,capture_output=True,text=True,encoding='utf-8',timeout=timeout)
    if p.returncode:raise RuntimeError('Docker operation failed: '+args[0])
    return p.stdout.strip()
def inspect(kind,name):
    p=subprocess.run(['docker',kind,'inspect',name],capture_output=True,text=True,encoding='utf-8',timeout=15)
    return json.loads(p.stdout)[0] if p.returncode==0 else None
def owned(kind,name):
    row=inspect(kind,name)
    if row and (row.get('Labels') or row.get('Config',{}).get('Labels') or {}).get('agentapp.task')!='AGE-34':raise ValueError('Existing resource is not AGE-34: '+name)
    return row
def config():
    STATE.mkdir(parents=True,exist_ok=True);p=STATE/'credentials.json'
    if not p.exists():
        v={'instance_id':str(uuid.uuid4()),'admin':secrets.token_urlsafe(32),'migrator':secrets.token_urlsafe(32),'app':secrets.token_urlsafe(32)}
        p.write_text(json.dumps(v),encoding='utf-8')
    return json.loads(p.read_text(encoding='utf-8'))
def sql(target,statement,role='postgres',database=None,timeout=60):
    if target not in TARGETS:raise ValueError('Only dev/test allowed')
    name,dbname,_=TARGETS[target];row=owned('container',name)
    if not row or row['Config']['Image']!=IMAGE:raise ValueError('Missing or wrong image instance')
    database=database or dbname
    if database not in (dbname,'postgres'):raise ValueError('Database outside target')
    password=config()[{'postgres':'admin','age34_migrator':'migrator','age34_app':'app'}[role]]
    # -X ignores psqlrc. Docker forwards PGPASSWORD from process environment;
    # its value is never placed in command arguments or evidence output.
    env={**os.environ,'PGPASSWORD':password}
    command=['docker','exec','-i','--env','PGPASSWORD',name,'psql','-X','-w','-h','127.0.0.1','-U',role,'-d',database,'-v','ON_ERROR_STOP=1','-A','-t']
    p=subprocess.run(command,input=statement,capture_output=True,text=True,encoding='utf-8',timeout=timeout,env=env)
    if p.returncode:raise RuntimeError('SQL failed: '+p.stderr[:500].replace(password,'[REDACTED]'))
    return p.stdout.strip()
def guard(target,expected_db=None):
    if target not in TARGETS:raise ValueError('Unknown target')
    name,db,_=TARGETS[target]
    if expected_db is not None and expected_db!=db:raise ValueError('Refusing foreign/production database')
    row=owned('container',name)
    if not row or row['Config']['Labels'].get('agentapp.disposable')!='true':raise ValueError('Not disposable')
    expected=config()['instance_id']
    actual=sql(target,"SELECT current_database()||'|'||instance_id::text FROM age34_control.identity",database=db)
    if actual!=db+'|'+expected:raise ValueError('Database ownership marker differs')
    return db
def start():
    cfg=config()
    if not owned('network',NET):docker('network','create','--internal','--label',LABEL,NET)
    # Docker Desktop does not forward published ports on an internal-only
    # network. A separate dedicated bridge connects the trusted PG service to
    # the supervisor's loopback ports; generated code is never attached to it.
    if not owned('network',HOST_NET):docker('network','create','--label',LABEL,HOST_NET)
    for target,(name,db,port) in TARGETS.items():
        volume=name+'-data'
        if not owned('volume',volume):docker('volume','create','--label',LABEL,volume)
        row=owned('container',name)
        if row:
            if row['Config']['Image']!=IMAGE:raise ValueError('Image drift')
            if not row['State']['Running']:docker('start',name)
        else:
            envfile=STATE/(target+'.env');envfile.write_text('POSTGRES_PASSWORD='+cfg['admin']+'\nPOSTGRES_DB='+db+'\nPOSTGRES_INITDB_ARGS=--auth-host=scram-sha-256\n',encoding='utf-8')
            docker('run','-d','--name',name,'--pull','never','--network',HOST_NET,'--label',LABEL,'--label','agentapp.disposable=true','--memory','512m','--cpus','1','--pids-limit','128','--publish',f'127.0.0.1:{port}:5432','--mount',f'type=volume,source={volume},target=/var/lib/postgresql','--env-file',str(envfile),'--health-cmd','pg_isready -U postgres','--health-interval','2s','--health-timeout','2s','--health-retries','30',IMAGE)
        current=owned('container',name)
        if HOST_NET not in current['NetworkSettings']['Networks']:docker('network','connect',HOST_NET,name)
        if NET not in current['NetworkSettings']['Networks']:docker('network','connect',NET,name)
        end=time.monotonic()+60
        while time.monotonic()<end:
            try:
                if sql(target,'SELECT 1',database='postgres').endswith('1'):break
            except RuntimeError:time.sleep(1)
        else:raise RuntimeError('PostgreSQL readiness timeout')
        # Control metadata remains outside the resettable application schema.
        marker=cfg['instance_id']
        bootstrap=f"""
        DO $$ BEGIN
          IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='age34_migrator') THEN CREATE ROLE age34_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '{cfg['migrator']}'; END IF;
          IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='age34_app') THEN CREATE ROLE age34_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '{cfg['app']}'; END IF;
        END $$;
        REVOKE ALL ON DATABASE {db} FROM PUBLIC;
        REVOKE ALL ON DATABASE postgres FROM PUBLIC;
        REVOKE ALL ON DATABASE template1 FROM PUBLIC;
        GRANT CONNECT ON DATABASE {db} TO age34_migrator,age34_app;
        REVOKE CREATE ON SCHEMA public FROM PUBLIC;
        CREATE SCHEMA IF NOT EXISTS age34_control;
        CREATE TABLE IF NOT EXISTS age34_control.identity(instance_id uuid PRIMARY KEY, disposable boolean NOT NULL CHECK(disposable));
        INSERT INTO age34_control.identity VALUES('{marker}',true) ON CONFLICT DO NOTHING;
        CREATE SCHEMA IF NOT EXISTS agentapp AUTHORIZATION age34_migrator;
        GRANT USAGE ON SCHEMA agentapp TO age34_app;
        ALTER ROLE age34_app IN DATABASE {db} SET search_path=agentapp,pg_catalog;
        ALTER ROLE age34_migrator IN DATABASE {db} SET search_path=agentapp,pg_catalog;
        ALTER DEFAULT PRIVILEGES FOR ROLE age34_migrator IN SCHEMA agentapp GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO age34_app;
        """
        sql(target,bootstrap);guard(target)
    return {'image':IMAGE,'server_version':sql('test','SELECT version()'),'databases':{k:v[1] for k,v in TARGETS.items()},'ports_loopback':{k:v[2] for k,v in TARGETS.items()},'scope':'local_disposable_only'}
def migrate(target):
    guard(target);text=(PACKAGE/'001_initial.sql').read_text(encoding='utf-8');sha=hashlib.sha256(text.encode()).hexdigest()
    sql(target,'CREATE TABLE IF NOT EXISTS agentapp.schema_migrations(version integer PRIMARY KEY,sha256 text NOT NULL,applied_at timestamptz NOT NULL DEFAULT clock_timestamp())',role='age34_migrator')
    sql(target,'REVOKE ALL ON TABLE agentapp.schema_migrations FROM age34_app;',role='age34_migrator')
    prior=sql(target,'SELECT sha256 FROM agentapp.schema_migrations WHERE version=1',role='age34_migrator')
    if prior:
        if prior!=sha:raise ValueError('Migration checksum drift')
        return 'already_applied'
    sql(target,"BEGIN; SET LOCAL lock_timeout='5s'; SET LOCAL statement_timeout='30s'; SELECT pg_advisory_xact_lock(34001);\n"+text+f"\nINSERT INTO agentapp.schema_migrations(version,sha256) VALUES(1,'{sha}'); REVOKE INSERT,UPDATE,DELETE ON agentapp.countries,agentapp.provinces,agentapp.zones,agentapp.plans FROM age34_app; COMMIT;",role='age34_migrator')
    return 'applied'
def seed(target):
    guard(target);sql(target,(PACKAGE/'synthetic_seed.sql').read_text(encoding='utf-8'),role='age34_migrator')
def reset(target,expected_db):
    if target!='test':raise ValueError('Reset supports test only')
    if expected_db is None:raise ValueError('Explicit disposable database name required')
    guard(target,expected_db)
    sql(target,'BEGIN; DROP SCHEMA agentapp CASCADE; CREATE SCHEMA agentapp AUTHORIZATION age34_migrator; GRANT USAGE ON SCHEMA agentapp TO age34_app; ALTER DEFAULT PRIVILEGES FOR ROLE age34_migrator IN SCHEMA agentapp GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO age34_app; COMMIT;')
    return migrate(target)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['start','migrate','seed','reset','status']);p.add_argument('--target',choices=['dev','test'],default='test');p.add_argument('--database');a=p.parse_args()
    result=start() if a.action=='start' else migrate(a.target) if a.action=='migrate' else seed(a.target) if a.action=='seed' else reset(a.target,a.database) if a.action=='reset' else {'database':guard(a.target),'version':sql(a.target,'SELECT version()')}
    print(json.dumps(result,ensure_ascii=False))
