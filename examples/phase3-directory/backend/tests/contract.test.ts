import test from 'node:test';import assert from 'node:assert/strict';import {Pool} from 'pg';
import {createApp,readQuery,filtersHash,cursor,decodeCursor} from '../src/app.js';
const c='00000000-0000-4000-8000-000000000001',a='00000000-0000-4000-8000-000000000011',b='00000000-0000-4000-8000-000000000012';
test('A23 invalid syntax, extra keys and bounded size',()=>{for(const q of [{country_id:'x'},{limit:'0'},{limit:'51'},{limit:'2; DROP TABLE users'},{private:'x'}])assert.throws(()=>readQuery(q));});
test('A23 signed keyset cursor binds hierarchy and rejects tampering',()=>{
  const q={country_id:c,province_id:a},key='x'.repeat(64),boundary={hash:filtersHash(q),rank:1,at:'2026-01-01T00:00:00.000000Z',id:c};
  const token=cursor(boundary,key);assert.deepEqual(decodeCursor(token,q,key),boundary);
  assert.throws(()=>decodeCursor(token,{country_id:c,province_id:b},key));assert.throws(()=>decodeCursor(token+'x',q,key));
});
test('real PostgreSQL listing, filters, eligibility, keyset and least privilege',async()=>{
  assert.ok(process.env.DATABASE_URL,'A real isolated database is mandatory');
  const pool=new Pool({connectionString:process.env.DATABASE_URL,max:5}),app=createApp(pool,'k'.repeat(64));
  try{
    assert.equal((await app.inject('/api/health')).statusCode,200);
    const all=(await app.inject('/api/profiles')).json();assert.equal(all.items.length,4);
    assert.deepEqual(all.items.map((x:any)=>x.plan),['promoted','promoted','basic','basic']);
    for(const x of all.items){assert.ok(x.age>=18);assert.ok(x.main_photo.is_main);for(const field of ['user_id','email','password_hash','birth_date','phone_e164','sort_at','rank'])assert.equal(field in x,false);}
    for(const province of [a,b]){
      const res=await app.inject('/api/profiles?country_id='+c+'&province_id='+province);assert.equal(res.statusCode,200);
      const items=res.json().items;assert.equal(items.length,2);assert.ok(items.every((x:any)=>x.province_id===province));assert.deepEqual(items.map((x:any)=>x.plan),['promoted','basic']);
    }
    let next:string|null=null;const seen:string[]=[];do{const res:{statusCode:number;json():{items:{id:string}[];next_cursor:string|null}}=await app.inject('/api/profiles?limit=1'+(next?'&cursor='+encodeURIComponent(next):''));assert.equal(res.statusCode,200);const page=res.json();seen.push(...page.items.map(x=>x.id));next=page.next_cursor;}while(next&&seen.length<10);
    assert.deepEqual(seen,all.items.map((x:any)=>x.id));assert.equal(new Set(seen).size,4);
    assert.equal((await app.inject('/api/profiles?province_id='+a+'&zone_id=00000000-0000-4000-8000-000000000022')).statusCode,422);
    assert.equal((await app.inject('/api/profiles?cursor=bad')).statusCode,400);
    const geo=(await app.inject('/api/catalog/geography')).json();assert.ok(geo.countries[0].name.includes('sintético'));assert.equal(geo.provinces.length,2);
    await assert.rejects(pool.query('CREATE TABLE agentapp.unauthorized (id int)'));
    await assert.rejects(pool.query('UPDATE agentapp.plans SET amount_minor=1'));
    await assert.rejects(pool.query('SELECT * FROM pg_authid'));
  }finally{await app.close();await pool.end();}
});
