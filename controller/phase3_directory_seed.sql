-- Only synthetic, disposable fixtures. Never an official geography catalogue.
INSERT INTO agentapp.countries VALUES ('00000000-0000-4000-8000-000000000001','AR','Argentina — fixture sintético');
INSERT INTO agentapp.provinces VALUES
 ('00000000-0000-4000-8000-000000000011','00000000-0000-4000-8000-000000000001','Provincia sintética A'),
 ('00000000-0000-4000-8000-000000000012','00000000-0000-4000-8000-000000000001','Provincia sintética B');
INSERT INTO agentapp.zones VALUES
 ('00000000-0000-4000-8000-000000000021','00000000-0000-4000-8000-000000000001','00000000-0000-4000-8000-000000000011','Zona sintética A'),
 ('00000000-0000-4000-8000-000000000022','00000000-0000-4000-8000-000000000001','00000000-0000-4000-8000-000000000012','Zona sintética B');
INSERT INTO agentapp.plans VALUES ('basic',1500000,'ARS','monthly',true),('promoted',3000000,'ARS','monthly',true);
INSERT INTO agentapp.users(id,email,password_hash,birth_date)
 SELECT ('10000000-0000-4000-8000-'||lpad(n::text,12,'0'))::uuid,
 'synthetic-'||n||'@example.invalid','NOT_A_LOGIN_HASH_SYNTHETIC_ONLY',CASE WHEN n=8 THEN DATE '2015-01-01' ELSE DATE '1990-01-01' END
 FROM generate_series(1,8) n;
INSERT INTO agentapp.profiles(id,user_id,display_name,gender,country_id,province_id,zone_id,description,phone_e164,published,created_at)
 SELECT ('20000000-0000-4000-8000-'||lpad(n::text,12,'0'))::uuid,('10000000-0000-4000-8000-'||lpad(n::text,12,'0'))::uuid,
 CASE n WHEN 1 THEN 'Promovido A' WHEN 2 THEN 'Básico A' WHEN 3 THEN 'Promovido B' WHEN 4 THEN 'Básico B' ELSE 'Oculto '||n END,
 'género sintético','00000000-0000-4000-8000-000000000001',
 CASE WHEN n<=2 THEN '00000000-0000-4000-8000-000000000011'::uuid ELSE '00000000-0000-4000-8000-000000000012'::uuid END,
 CASE WHEN n<=2 THEN '00000000-0000-4000-8000-000000000021'::uuid ELSE '00000000-0000-4000-8000-000000000022'::uuid END,
 CASE WHEN n=1 THEN '<script>window.__injected=true</script>' ELSE 'Descripción exclusivamente sintética' END,
 '+5491100000000',n<>6,TIMESTAMPTZ '2026-01-01 00:00:00.000001+00'
 FROM generate_series(1,8) n;
INSERT INTO agentapp.photos(id,profile_id,object_key,is_main)
 SELECT ('30000000-0000-4000-8000-'||lpad(n::text,12,'0'))::uuid,('20000000-0000-4000-8000-'||lpad(n::text,12,'0'))::uuid,
 'synthetic-image-'||n,true FROM generate_series(1,8) n WHERE n<>7;
INSERT INTO agentapp.subscriptions(user_id,plan_id,origin,amount_minor,currency,starts_at,ends_at)
 SELECT ('10000000-0000-4000-8000-'||lpad(n::text,12,'0'))::uuid,CASE WHEN n IN (1,3) THEN 'promoted' ELSE 'basic' END,
 'simulated',CASE WHEN n IN (1,3) THEN 3000000 ELSE 1500000 END,'ARS',statement_timestamp()-INTERVAL '2 days',
 CASE WHEN n=5 THEN statement_timestamp()-INTERVAL '1 day' ELSE statement_timestamp()+INTERVAL '30 days' END
 FROM generate_series(1,8) n;
