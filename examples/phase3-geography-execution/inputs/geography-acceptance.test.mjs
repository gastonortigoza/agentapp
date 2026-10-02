// Codex frozen acceptance harness, never an agent implementation or official data.
import test, {before, beforeEach, after} from 'node:test';
import assert from 'node:assert/strict';
import Fastify from 'fastify';
import pg from 'pg';
import {registerGeography} from './backend/src/geography.ts';

const DB='age50_geography_test';
function checkedURL(name, role) {
  const value=process.env[name];
  if (process.env.GEO_DISPOSABLE_FIXTURE!=='1' || !value) throw Error('Disposable geography fixture required');
  const url=new URL(value);
  if (url.protocol!=='postgresql:' || url.hostname!=='127.0.0.1' || url.port!=='5432'
      || url.pathname!=='/'+DB || url.username!==role || url.search || !url.password)
    throw Error('Geography fixture boundary invalid');
  return value;
}
const appPool=new pg.Pool({connectionString:checkedURL('GEO_APP_DATABASE_URL','age50_geo_app'),max:2});
const fixturePool=new pg.Pool({connectionString:checkedURL('GEO_FIXTURE_DATABASE_URL','age50_geo_fixture'),max:2});
const C='a1000000-0000-4000-8000-000000000001';
const P1='b1000000-0000-4000-8000-000000000001', P2='b1000000-0000-4000-8000-000000000002';
const Z1='c1000000-0000-4000-8000-000000000001', Z2='c1000000-0000-4000-8000-000000000002';
const UNKNOWN='d1000000-0000-4000-8000-000000000099';
const country={id:C,code:'AR',name:'Synthetic AR country'};
const provinces=[{id:P1,country_id:C,name:'Synthetic province one'},{id:P2,country_id:C,name:'Synthetic province two'}];
const zones=[{id:Z1,country_id:C,province_id:P1,name:'Synthetic zone one'},{id:Z2,country_id:C,province_id:P2,name:'Synthetic zone two'}];
const applications=[];
const sorted=(rows)=>[...rows].sort((a,b)=>a.id.localeCompare(b.id));

before(async()=>{
  const r=await fixturePool.query('SELECT current_database() AS db,current_user AS role,version() AS version');
  assert.equal(r.rows[0].db,DB);assert.equal(r.rows[0].role,'age50_geo_fixture');
  assert.match(r.rows[0].version,/^PostgreSQL 18\.6\b/);
  assert.equal((await appPool.query('SELECT current_user AS role')).rows[0].role,'age50_geo_app');
});
beforeEach(async()=>{
  const client=await fixturePool.connect();
  try {
    await client.query('BEGIN');
    await client.query('DELETE FROM agentapp.zones');await client.query('DELETE FROM agentapp.provinces');await client.query('DELETE FROM agentapp.countries');
    await client.query('INSERT INTO agentapp.countries(id,code,name) VALUES($1,$2,$3)',[C,'AR',country.name]);
    for(const p of provinces) await client.query('INSERT INTO agentapp.provinces(id,country_id,name) VALUES($1,$2,$3)',[p.id,p.country_id,p.name]);
    for(const z of zones) await client.query('INSERT INTO agentapp.zones(id,country_id,province_id,name) VALUES($1,$2,$3,$4)',[z.id,z.country_id,z.province_id,z.name]);
    await client.query('COMMIT');
  } catch(e) {await client.query('ROLLBACK');throw e;} finally {client.release();}
});
after(async()=>{for(const app of applications) await app.close();await Promise.all([appPool.end(),fixturePool.end()]);});

async function application(ready=true, fail=false) {
  const calls=[];const app=Fastify({logger:false});applications.push(app);
  const pool={query:async(sql,values=[])=>{
    calls.push({sql,values});
    assert.equal(typeof sql,'string');assert.ok(Array.isArray(values));
    assert.match(sql,/^\s*SELECT\b/i);
    assert.doesNotMatch(sql,/;|\b(?:INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|GRANT|COPY|pg_read_file|pg_read_binary_file)\b/i);
    const tables=[...sql.matchAll(/\bFROM\s+agentapp\.(\w+)/gi)].map(m=>m[1]);
    assert.ok(tables.length>0 && tables.every(t=>['countries','provinces','zones'].includes(t)));
    if(fail) throw Error('Synthetic SQL failure credential-fixture-secret');
    return appPool.query(sql,values);
  }};
  await registerGeography(app,pool,ready);await app.ready();return {app,calls};
}
async function get(context,query='') {return context.app.inject({method:'GET',url:'/api/catalog/geography'+query});}
function error(response,status,code) {
  assert.equal(response.statusCode,status);const body=response.json();
  assert.deepEqual(Object.keys(body).sort(),['error','request_id']);
  assert.deepEqual(Object.keys(body.error).sort(),['code','message']);
  assert.equal(body.error.code,code);assert.equal(typeof body.error.message,'string');
  assert.equal(typeof body.request_id,'string');assert.ok(body.request_id);
  assert.doesNotMatch(response.body,/credential-fixture-secret|SELECT|postgresql:|stack|password|user_id/i);
}
function hierarchy(response,expected) {
  assert.equal(response.statusCode,200);const body=response.json();
  assert.deepEqual(Object.keys(body).sort(),['countries','provinces','zones']);
  for(const key of Object.keys(expected)) assert.deepEqual(sorted(body[key]),sorted(expected[key]));
  for(const p of body.provinces) assert.ok(body.countries.some(c=>c.id===p.country_id));
  for(const z of body.zones) assert.ok(body.provinces.some(p=>p.id===z.province_id && p.country_id===z.country_id));
}

test('GPG01 unavailable qualification returns503 without DB calls',async()=>{
  const c=await application(false);error(await get(c),503,'catalog_unavailable');
  error(await get(c,'?country_id=malformed'),503,'catalog_unavailable');assert.equal(c.calls.length,0);
});
test('GPG02 synthetic loaded hierarchy exact DTO and AR parameter',async()=>{
  const c=await application();hierarchy(await get(c),{countries:[country],provinces,zones});
  const queries=c.calls.filter(q=>/\bFROM\s+agentapp\.countries\b/i.test(q.sql));assert.ok(queries.length);
  for(const q of queries) {
    const match=q.sql.match(/\b(?:\w+\.)?code\s*=\s*\$(\d+)/i);
    assert.ok(match,'AR must be a bound country predicate, not a fixture ID');assert.equal(q.values[Number(match[1])-1],'AR');
  }
});
test('GPG03 country filter preserves hierarchy',async()=>{
  hierarchy(await get(await application(),'?country_id='+C),{countries:[country],provinces,zones});
});
test('GPG04 province alone derives country and filters zones',async()=>{
  hierarchy(await get(await application(),'?province_id='+P1),{countries:[country],provinces:[provinces[0]],zones:[zones[0]]});
});
test('GPG05 combined filters honor selected province',async()=>{
  hierarchy(await get(await application(),'?country_id='+C+'&province_id='+P2),{countries:[country],provinces:[provinces[1]],zones:[zones[1]]});
});
test('GPG06 empty malformed repeated and extra query keys return400',async()=>{
  const c=await application();
  for(const query of ['?country_id=','?province_id=','?country_id=invalid','?province_id=invalid','?country_id='+C+'&country_id='+C,'?extra=1'])
    error(await get(c,query),400,'invalid_request');
});
test('GPG07 unknown and inconsistent selectors return422',async()=>{
  const c=await application();
  for(const query of ['?country_id='+UNKNOWN,'?province_id='+UNKNOWN,'?country_id='+UNKNOWN+'&province_id='+P1,'?country_id='+C+'&province_id='+UNKNOWN])
    error(await get(c,query),422,'geography_invalid');
});
test('GPG08 uppercase UUID selectors preserve canonical parents',async()=>{
  hierarchy(await get(await application(),'?country_id='+C.toUpperCase()+'&province_id='+P1.toUpperCase()),{countries:[country],provinces:[provinces[0]],zones:[zones[0]]});
});
test('GPG09 empty country catalogue returns503',async()=>{
  await fixturePool.query('DELETE FROM agentapp.zones');await fixturePool.query('DELETE FROM agentapp.provinces');await fixturePool.query('DELETE FROM agentapp.countries');
  error(await get(await application()),503,'catalog_unavailable');
});
test('GPG10 missing provinces returns503 with and without country filter',async()=>{
  await fixturePool.query('DELETE FROM agentapp.zones');await fixturePool.query('DELETE FROM agentapp.provinces');
  const c=await application();error(await get(c),503,'catalog_unavailable');error(await get(c,'?country_id='+C),503,'catalog_unavailable');
});
test('GPG11 missing zones returns503 including selected province',async()=>{
  await fixturePool.query('DELETE FROM agentapp.zones');const c=await application();
  for(const query of ['', '?country_id='+C,'?province_id='+P1,'?country_id='+C+'&province_id='+P1])
    error(await get(c,query),503,'catalog_unavailable');
});
test('GPG12 database failure returns safe503 with request id',async()=>{
  error(await get(await application(true,true)),503,'service_unavailable');
});
test('GPG13 application role cannot modify geography and FK protects parents',async()=>{
  for(const sql of ['DELETE FROM agentapp.zones','UPDATE agentapp.countries SET name=name',"INSERT INTO agentapp.countries(id,code,name) VALUES('d1000000-0000-4000-8000-000000000098','AR','forbidden')"])
    await assert.rejects(()=>appPool.query(sql),e=>e.code==='42501');
  await assert.rejects(()=>fixturePool.query('INSERT INTO agentapp.zones(id,country_id,province_id,name) VALUES($1,$2,$3,$4)',[UNKNOWN,UNKNOWN,P1,'Synthetic invalid parent']),e=>e.code==='23503');
  assert.equal((await fixturePool.query('SELECT count(*) AS count FROM agentapp.zones')).rows[0].count,'2');
});
test('GPG14 standalone registration exposes no auth or profile routes',async()=>{
  const {app}=await application();
  for(const [method,url] of [['POST','/api/catalog/geography'],['POST','/api/auth/login'],['GET','/api/profiles']])
    assert.equal((await app.inject({method,url})).statusCode,404);
});
