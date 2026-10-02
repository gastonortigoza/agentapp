import test from 'node:test';
import assert from 'node:assert/strict';
import {Pool} from 'pg';
import {createApp} from '../src/app.js';
const id=(n:number)=>'20000000-0000-4000-8000-'+String(n).padStart(12,'0');
test('UI04/A13 real detail shares listing eligibility and exposes only PublicProfile/Photo',async()=>{
  assert.ok(process.env.DATABASE_URL,'Real PostgreSQL mandatory');
  const pool=new Pool({connectionString:process.env.DATABASE_URL,max:5}),app=createApp(pool,'k'.repeat(64));
  try {
    for(const n of [1,2,3,4]) {
      const res=await app.inject('/api/profiles/'+id(n));assert.equal(res.statusCode,200);
      assert.deepEqual(Object.keys(res.json()),['profile']);const profile=res.json().profile;
      assert.deepEqual(Object.keys(profile).sort(),['age','country_id','description','display_name','gender','id','photos','plan','province_id','whatsapp_url','zone_id','zone_label']);
      assert.equal(profile.id,id(n));assert.ok(profile.age>=18);assert.equal(profile.whatsapp_url,'https://wa.me/5491100000000');
      for(const photo of profile.photos){assert.deepEqual(Object.keys(photo).sort(),['created_at','id','is_main','url']);assert.equal(photo.url,'/synthetic-placeholder.png');assert.ok(Number.isFinite(Date.parse(photo.created_at)));}
      assert.equal(profile.photos[0].is_main,true);
    }
    const p=(await app.inject('/api/profiles/'+id(1))).json().profile;
    assert.deepEqual(p.photos.map((x:any)=>x.id),['30000000-0000-4000-8000-000000000001','40000000-0000-4000-8000-000000000001','40000000-0000-4000-8000-000000000002']);
    assert.equal(p.description,'<script>window.__injected=true</script>');
    for(const n of [5,6,7,8,999]) {
      const res=await app.inject('/api/profiles/'+id(n));assert.equal(res.statusCode,404);
      assert.equal(res.json().error.code,'profile_not_found');assert.equal(typeof res.json().request_id,'string');assert.deepEqual(Object.keys(res.json()).sort(),['error','request_id']);
    }
    for(const url of ['/api/profiles/not-a-uuid','/api/profiles/'+id(1)+'?private=true','/api/profiles/'+id(1)+'?cursor=x']){
      const res=await app.inject(url);assert.equal(res.statusCode,400);assert.equal(res.json().error.code,'invalid_request');assert.equal(typeof res.json().request_id,'string');
    }
  } finally {await app.close();await pool.end();}
});
test('detail DB failure is a503 common error without SQL or private data',async()=>{
  const pool={query:async()=>{throw new Error('password=secret SELECT private FROM users')}} as unknown as Pool;
  const app=createApp(pool,'k'.repeat(64));
  try {const res=await app.inject('/api/profiles/'+id(1));assert.equal(res.statusCode,503);assert.equal(res.json().error.code,'service_unavailable');assert.equal(typeof res.json().request_id,'string');assert.doesNotMatch(res.body,/secret|SELECT|password/);}
  finally {await app.close();}
});
