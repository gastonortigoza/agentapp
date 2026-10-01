"""Real PostgreSQL checks, including a SQL reference of the subscription protocol.

The reference exercises locking/storage, not an HTTP implementation or auth.
Only the driver's explicitly labelled disposable test database is reset.
"""
import concurrent.futures, hashlib, json, pathlib, subprocess, time, unittest
import age34_driver as db

A='10000000-0000-4000-8000-000000000001'
B='10000000-0000-4000-8000-000000000002'
P='20000000-0000-4000-8000-000000000001'

def app(s): return db.sql('test',s,role='age34_app')

def activation(user,key,plan='basic',pause=0):
    # All values here are fixed synthetic test fixtures, never external inputs.
    payload=hashlib.sha256(json.dumps({'plan_id':plan},separators=(',',':'),sort_keys=True).encode()).hexdigest()
    statement=f"""BEGIN; SET LOCAL lock_timeout='8s'; SET LOCAL statement_timeout='15s';
    DO $fixture$ DECLARE t timestamptz; r agentapp.idempotency%ROWTYPE;
    sid uuid; body jsonb; price integer;
    BEGIN
      PERFORM 1 FROM agentapp.users WHERE id='{user}' FOR UPDATE;
      -- Capture once AFTER the shared row lock. A transaction that began first
      -- can acquire this lock second; its BEGIN timestamp would be too early.
      t:=clock_timestamp();
      PERFORM pg_sleep({pause});
      SELECT * INTO r FROM agentapp.idempotency WHERE user_id='{user}' AND key='{key}';
      IF FOUND AND r.expires_at>t THEN
        IF r.payload_hash='{payload}' THEN
          PERFORM set_config('age34_test.result',jsonb_build_object('status',r.response_status,'body',r.response_body)::text,true);
        ELSE PERFORM set_config('age34_test.result','{{"status":409,"error":"idempotency_conflict"}}',true); END IF;
      ELSE
        DELETE FROM agentapp.idempotency WHERE user_id='{user}' AND key='{key}' AND expires_at<=t;
        IF EXISTS(SELECT 1 FROM agentapp.subscriptions WHERE user_id='{user}' AND starts_at<=t AND ends_at>t) THEN
          PERFORM set_config('age34_test.result','{{"status":409,"error":"active_subscription"}}',true);
        ELSE
          SELECT amount_minor INTO STRICT price FROM agentapp.plans WHERE id='{plan}' AND enabled;
          sid:=gen_random_uuid();
          INSERT INTO agentapp.subscriptions(id,user_id,plan_id,origin,amount_minor,currency,starts_at,ends_at)
            VALUES(sid,'{user}','{plan}','simulated',price,'ARS',t,t+interval '30 days');
          body:=jsonb_build_object('id',sid,'plan_id','{plan}','origin','simulated','status','active','starts_at',t,'ends_at',t+interval '30 days','amount_minor',price,'currency','ARS','notice','Sin cobro — activación de prueba');
          INSERT INTO agentapp.idempotency(user_id,key,payload_hash,response_status,response_body,expires_at)
            VALUES('{user}','{key}','{payload}',201,body,t+interval '24 hours');
          PERFORM set_config('age34_test.result',jsonb_build_object('status',201,'body',body)::text,true);
        END IF;
      END IF;
    END $fixture$;
    SELECT current_setting('age34_test.result');
    SELECT COALESCE(current_setting('age34_test.result')::jsonb->>'error'='idempotency_conflict',false) AS rollback \\gset
    \\if :rollback
    ROLLBACK;
    \\else
    COMMIT;
    \\endif
    """
    return json.loads(next(x for x in app(statement).splitlines() if x.startswith('{')))

class PostgreSQLChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.reset('test',db.TARGETS['test'][1]); db.seed('test')

    def setUp(self):
        app('DELETE FROM agentapp.idempotency; DELETE FROM agentapp.subscriptions; DELETE FROM agentapp.photos; DELETE FROM agentapp.profiles; DELETE FROM agentapp.sessions; DELETE FROM agentapp.password_resets;')

    def reject(self,sql,fragment,role='age34_app',database=None):
        with self.assertRaises(RuntimeError) as error: db.sql('test',sql,role=role,database=database)
        self.assertIn(fragment,str(error.exception))

    def profile(self,province='00000000-0000-4000-8000-000000000011'):
        return app(f"INSERT INTO agentapp.profiles(id,user_id,display_name,gender,country_id,province_id,zone_id,description,phone_e164) VALUES('{P}','{A}','Persona sintética','No especificado','00000000-0000-4000-8000-000000000001','{province}','00000000-0000-4000-8000-000000000021','Fixture sin publicación','+5491100000000');")

    def test_01_connections_and_version(self):
        self.assertEqual(app('SELECT current_user'),'age34_app')
        self.assertEqual(db.sql('dev','SELECT current_database()',role='age34_app'),'agentapp_age34_dev')
        self.assertTrue(app('SHOW server_version').startswith('18.6'))

    def test_02_role_boundaries(self):
        self.assertEqual(db.sql('test',"SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication FROM pg_roles WHERE rolname='age34_app'"),'f')
        self.reject('CREATE TABLE agentapp.forbidden(id integer);','permission denied for schema')
        self.reject('CREATE SCHEMA forbidden;','permission denied for database')
        self.reject('SELECT * FROM age34_control.identity;','permission denied for schema')
        self.reject('SELECT * FROM agentapp.schema_migrations;','permission denied for table')
        self.reject("UPDATE agentapp.plans SET amount_minor=1;",'permission denied for table')
        self.reject('SELECT 1','permission denied for database',database='postgres')

    def test_03_eleven_contract_tables(self):
        tables=set(app("SELECT table_name FROM information_schema.tables WHERE table_schema='agentapp'").splitlines())
        self.assertEqual(tables,{'users','countries','provinces','zones','profiles','photos','plans','subscriptions','idempotency','sessions','password_resets'})

    def test_04_plan_prices_and_synthetic_seed_repeat(self):
        db.seed('test')
        self.assertEqual(app("SELECT id||':'||amount_minor FROM agentapp.plans ORDER BY id"),'basic:1500000\npromoted:3000000')
        self.assertEqual(app('SELECT count(*) FROM agentapp.users'),'2')

    def test_05_geographical_hierarchy(self):
        with self.assertRaisesRegex(RuntimeError,'foreign key'): self.profile('00000000-0000-4000-8000-000000000012')
        self.profile()
        self.assertEqual(app('SELECT count(*) FROM agentapp.profiles'),'1')

    def test_06_one_main_photo(self):
        self.profile()
        app(f"INSERT INTO agentapp.photos(profile_id,object_key,is_main) VALUES('{P}','synthetic/main',true),('{P}','synthetic/extra',false);")
        self.reject(f"INSERT INTO agentapp.photos(profile_id,object_key,is_main) VALUES('{P}','synthetic/second',true);",'photos_one_main')

    def test_07_interval_and_hash_constraints(self):
        self.reject(f"INSERT INTO agentapp.subscriptions(user_id,plan_id,origin,amount_minor,currency,starts_at,ends_at) VALUES('{A}','basic','simulated',1500000,'ARS',now(),now());",'check constraint')
        self.reject(f"INSERT INTO agentapp.idempotency(user_id,key,payload_hash,response_status,response_body,expires_at) VALUES('{A}','bad','raw',201,'{{}}',now()+interval '1 day');",'check constraint')

    def test_08_same_key_and_payload_are_scoped_by_user(self):
        a,b=activation(A,'shared'),activation(B,'shared')
        self.assertEqual((a['status'],b['status']),(201,201))
        self.assertNotEqual(a['body']['id'],b['body']['id'])
        self.assertEqual(app('SELECT count(DISTINCT payload_hash)||\':\'||count(*) FROM agentapp.idempotency'),'1:2')

    def test_09_retry_exact_body_without_extension(self):
        first=activation(A,'retry');again=activation(A,'retry')
        self.assertEqual(first,again)
        self.assertEqual(app('SELECT count(*) FROM agentapp.subscriptions'),'1')

    def test_10_different_payload_and_second_key(self):
        activation(A,'first')
        self.assertEqual(activation(A,'first','promoted'),{'status':409,'error':'idempotency_conflict'})
        self.assertEqual(activation(A,'second'),{'status':409,'error':'active_subscription'})
        self.assertEqual(app('SELECT count(*) FROM agentapp.idempotency'),'1')

    def test_11_concurrent_first_activation(self):
        # Both connections start before any subscription exists. The users row
        # exists and is the shared lock, unlike SELECT FOR UPDATE on an empty set.
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda key:activation(A,key,pause=.4),['concurrent-a','concurrent-b']))
        self.assertEqual(sorted(x['status'] for x in results),[201,409])
        self.assertEqual(app('SELECT count(*) FROM agentapp.subscriptions'),'1')
        self.assertEqual(app('SELECT count(*) FROM agentapp.idempotency'),'1')

    def test_12_expired_key_and_expired_period(self):
        activation(A,'reuse')
        app("UPDATE agentapp.idempotency SET created_at=now()-interval '2 days',expires_at=now()-interval '1 day'; UPDATE agentapp.subscriptions SET starts_at=now()-interval '31 days',ends_at=now()-interval '1 day';")
        self.assertEqual(activation(A,'reuse','promoted')['status'],201)
        self.assertEqual(app('SELECT count(*) FROM agentapp.subscriptions'),'2')
        self.assertEqual(app('SELECT count(*) FROM agentapp.idempotency'),'1')

    def test_13_failed_transaction_leaves_no_partial_write(self):
        self.reject(f"BEGIN; INSERT INTO agentapp.subscriptions(user_id,plan_id,origin,amount_minor,currency,starts_at,ends_at) VALUES('{A}','basic','simulated',1500000,'ARS',now(),now()+interval '30 days'); SELECT 1/0; COMMIT;",'division by zero')
        self.assertEqual(app('SELECT count(*) FROM agentapp.subscriptions'),'0')

    def test_14_migration_repeat_and_checksum_drift(self):
        self.assertEqual(db.migrate('test'),'already_applied')
        source=db.PACKAGE/'001_initial.sql';original=source.read_bytes()
        try:
            source.write_bytes(original+b'\n-- intentional drift fixture\n')
            with self.assertRaisesRegex(ValueError,'checksum drift'):db.migrate('test')
        finally:source.write_bytes(original)

    def test_15_foreign_reset_rejected(self):
        for target,database in [('test','production'),('test',None),('dev','agentapp_age34_dev'),('production','agentapp_age34_test')]:
            with self.assertRaises(ValueError):db.reset(target,database)
        cfg=db.config();marker=cfg['instance_id']
        try:
            db.sql('test',"UPDATE age34_control.identity SET instance_id='90000000-0000-4000-8000-000000000001';")
            with self.assertRaisesRegex(ValueError,'marker differs'):db.reset('test',db.TARGETS['test'][1])
        finally:db.sql('test',f"UPDATE age34_control.identity SET instance_id='{marker}';")
        self.assertEqual(app('SELECT count(*) FROM agentapp.users'),'2')

    def test_16_persisted_restart(self):
        before=activation(A,'restart')
        db.docker('restart',db.TARGETS['test'][0])
        deadline=time.monotonic()+30
        while True:
            try:app('SELECT 1');break
            except RuntimeError:
                if time.monotonic()>deadline:raise
                time.sleep(.5)
        self.assertEqual(activation(A,'restart'),before)

    def test_17_repeatable_disposable_reset(self):
        self.assertEqual(db.reset('test',db.TARGETS['test'][1]),'applied')
        self.assertEqual(app('SELECT count(*) FROM agentapp.users'),'0')
        db.seed('test')
        self.assertEqual(app('SELECT count(*) FROM agentapp.users'),'2')

if __name__=='__main__':unittest.main(verbosity=2)
