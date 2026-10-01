"""Trusted migration/recovery rehearsal, exclusively on labelled synthetic test DB."""
import concurrent.futures,datetime,hashlib,json,os,pathlib,subprocess,time
import age34_driver as db
HERE=pathlib.Path(__file__).resolve().parent
PACKAGE=HERE if (HERE/'002_directory_index.sql').exists() else db.PACKAGE
ROOT=HERE if (HERE/'002_directory_index.sql').exists() else db.ROOT/'outputs'
STATE=db.STATE/'recovery';STATE.mkdir(parents=True,exist_ok=True)
DATABASE=db.TARGETS['test'][1]

def migration2():
    db.guard('test',DATABASE)
    text=(PACKAGE/'002_directory_index.sql').read_text(encoding='utf-8')
    sha=hashlib.sha256(text.encode()).hexdigest();escaped=text.replace("'","''")
    result=db.sql('test',f"""BEGIN; SET LOCAL lock_timeout='5s'; SET LOCAL statement_timeout='30s';
    SELECT pg_advisory_xact_lock(34001);
    DO $upgrade$ DECLARE prior text; BEGIN
      IF NOT EXISTS(SELECT 1 FROM agentapp.schema_migrations WHERE version=1) THEN RAISE EXCEPTION 'Version1 required'; END IF;
      SELECT sha256 INTO prior FROM agentapp.schema_migrations WHERE version=2;
      IF FOUND THEN
        IF prior<>'{sha}' THEN RAISE EXCEPTION 'Version2 checksum drift'; END IF;
        PERFORM set_config('age36.result','already_applied',true);
      ELSE
        EXECUTE '{escaped}';
        INSERT INTO agentapp.schema_migrations(version,sha256) VALUES(2,'{sha}');
        PERFORM set_config('age36.result','applied',true);
      END IF;
    END $upgrade$;
    SELECT current_setting('age36.result'); COMMIT;""",role='age34_migrator')
    return next(x for x in result.splitlines() if x in ('applied','already_applied'))

def fingerprint():
    tables=['users','countries','provinces','zones','profiles','photos','plans','subscriptions','idempotency','sessions','password_resets','schema_migrations']
    # Stable complete row representation, including persisted dates and responses.
    pieces=[f"'{table}',(SELECT COALESCE(jsonb_agg(v ORDER BY v::text),'[]'::jsonb) FROM (SELECT to_jsonb(t) v FROM agentapp.{table} t) rows)" for table in tables]
    value=db.sql('test','SELECT jsonb_build_object('+','.join(pieces)+')',role='age34_migrator')
    return hashlib.sha256(value.encode()).hexdigest()

def backup():
    db.guard('test',DATABASE)
    env={**os.environ,'PGPASSWORD':db.config()['migrator']}
    p=subprocess.run(['docker','exec','--env','PGPASSWORD',db.TARGETS['test'][0],'pg_dump','-w','-h','127.0.0.1','-U','age34_migrator','-d',DATABASE,'--data-only','--schema=agentapp','--no-owner','--no-acl'],capture_output=True,timeout=60,env=env)
    if p.returncode:raise RuntimeError('Synthetic pg_dump failed; no restore dispatched')
    path=STATE/'synthetic-data.sql';path.write_bytes(p.stdout)
    return path,hashlib.sha256(p.stdout).hexdigest()

def restore(path,expected_sha,database):
    if path.resolve()!= (STATE/'synthetic-data.sql').resolve():raise ValueError('Unapproved backup reference')
    if database!=DATABASE:raise ValueError('Foreign database rejected')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=expected_sha:raise ValueError('Backup checksum drift')
    db.guard('test',database)
    # Reconstruct exact reviewed schema first. Restoring an image is insufficient.
    db.reset('test',database);migration2()
    statement='BEGIN; SET LOCAL statement_timeout=\'30s\'; TRUNCATE agentapp.schema_migrations;\n'+path.read_text(encoding='utf-8')+'\nCOMMIT;'
    db.sql('test',statement,role='age34_migrator')

def main():
    started=time.monotonic(); checks=[]
    def check(name,action):
        then=time.monotonic();action();checks.append({'criterion':name,'status':'passed','seconds':round(time.monotonic()-then,3)})
    def empty_and_upgrade():
        assert db.reset('test',DATABASE)=='applied';db.seed('test')
        assert db.sql('test','SELECT count(*) FROM agentapp.users',role='age34_app')=='2'
        assert migration2()=='applied'
        assert db.sql('test','SELECT max(version) FROM agentapp.schema_migrations',role='age34_migrator')=='2'
        assert db.sql('test','SELECT count(*) FROM agentapp.users',role='age34_app')=='2'
    check('empty_install_v1_and_additive_upgrade_v2_preserve_fixtures',empty_and_upgrade)
    check('repeated_migration_is_noop',lambda:assert_equal(migration2(),'already_applied'))
    def failure_atomic():
        try:db.sql('test','BEGIN; CREATE INDEX age36_failure_fixture ON agentapp.users(created_at); INSERT INTO agentapp.schema_migrations(version,sha256) VALUES(3,\'failure-fixture\'); SELECT 1/0; COMMIT;',role='age34_migrator')
        except RuntimeError as e:
            assert 'division by zero' in str(e)
        else:raise AssertionError('Failure was expected')
        assert db.sql('test',"SELECT (SELECT count(*) FROM pg_indexes WHERE schemaname='agentapp' AND indexname='age36_failure_fixture')||':'||(SELECT count(*) FROM agentapp.schema_migrations WHERE version=3)",role='age34_migrator')=='0:0'
    check('partial_ddl_and_version_record_rollback_together',failure_atomic)
    def concurrency():
        db.sql('test','BEGIN; DROP INDEX agentapp.profiles_directory_geo; DELETE FROM agentapp.schema_migrations WHERE version=2; COMMIT;',role='age34_migrator')
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:migration2(),range(2)))
        assert sorted(results)==['already_applied','applied']
    check('two_migrators_serialized_by_advisory_transaction_lock',concurrency)
    def drift():
        source=PACKAGE/'002_directory_index.sql';prior=source.read_bytes()
        try:
            source.write_bytes(prior+b'\n-- deliberate synthetic checksum drift\n')
            try:migration2()
            except RuntimeError as e:assert 'checksum drift' in str(e)
            else:raise AssertionError('Drift was expected to fail')
        finally:source.write_bytes(prior)
    check('migration_source_drift_rejected',drift)
    def recovery():
        # SQL compatibility with the original column contract is tested, not two
        # application versions: no implemented FE/API exists yet.
        db.sql('test',"INSERT INTO agentapp.profiles(user_id,display_name,gender,country_id,province_id,zone_id,description,phone_e164) VALUES('10000000-0000-4000-8000-000000000001','Fixture de recuperación','Sin especificar','00000000-0000-4000-8000-000000000001','00000000-0000-4000-8000-000000000011','00000000-0000-4000-8000-000000000021','Solo sintético','+5491100000000');",role='age34_app')
        before=fingerprint();path,sha=backup()
        for bad_db,bad_sha in [('production',sha),(DATABASE,'0'*64)]:
            try:restore(path,bad_sha,bad_db)
            except ValueError:pass
            else:raise AssertionError('Unsafe recovery accepted')
        db.sql('test','DELETE FROM agentapp.profiles;',role='age34_app')
        assert fingerprint()!=before
        restore(path,sha,DATABASE)
        assert fingerprint()==before
        assert db.sql('test','SELECT count(*) FROM agentapp.profiles',role='age34_app')=='1'
        assert db.sql('test',"SELECT count(*) FROM pg_indexes WHERE schemaname='agentapp' AND indexname='profiles_directory_geo'",role='age34_migrator')=='1'
    check('logical_backup_restore_complete_data_and_v2_schema_foreign_or_tampered_rejected',recovery)
    result={'status':'passed','checks':checks,'total_seconds':round(time.monotonic()-started,3),'image':db.IMAGE,'database':DATABASE,'version':2,'scope':'local_synthetic_only','sql_compatibility':'additive index; original columns/queries unchanged; no application coexistence claimed','lock_timeout_seconds':5,'statement_timeout_seconds':30,'backup_scope':'logical data snapshot plus exact reviewed schema migrations, no roles/control marker or real data','author':'Codex supervision; not autonomous agent correction'}
    (ROOT/'AGE-36-pruebas-recuperacion.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))

def assert_equal(a,b):assert a==b,(a,b)
if __name__=='__main__':main()
