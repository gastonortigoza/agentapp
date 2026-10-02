// Operator adapter tests with an explicit fixture registrar. No auth implementation.
import {test} from 'node:test';import assert from 'node:assert/strict';
import {type Pool} from 'pg';import {createSessionApp} from '../src/session-app.js';
const pool={fixtureOnly:true} as unknown as Pool;
const config={key:new Uint8Array(32),issuer:'fixture',audience:'fixture',origin:'https://localhost:9443'};
test('registrar receives original Pool/config; no application privileges are fabricated',async()=>{
 let calls=0;const app=await createSessionApp(async(a,p,c)=>{assert.equal(p,pool);assert.equal(c,config);calls++;a.post('/__adapter/fixture',async()=>({fixture:true}));},pool,config);
 try{assert.equal(calls,1);assert.equal((await app.inject({method:'POST',url:'/__adapter/fixture'})).statusCode,200);}finally{await app.close();}
});
test('invalid config rejects before registrar can execute',async()=>{
 for(const bad of [{...config,key:new Uint8Array(31)},{...config,origin:'http://localhost:9443'},{...config,origin:'https://localhost:9443/path'},{...config,issuer:''}]){
  let called=false;await assert.rejects(createSessionApp(async()=>{called=true;},pool,bad));assert.equal(called,false);
 }
});
test('unexpected errors produce503 and an opaque ID without private content',async()=>{
 const app=await createSessionApp(async(a)=>{a.post('/__adapter/fixture',async()=>{throw new Error('private-SQL-password-fixture');});},pool,config);
 try{
  const response=await app.inject({method:'POST',url:'/__adapter/fixture'});assert.equal(response.statusCode,503);
  assert.deepEqual(Object.keys(response.json()).sort(),['error','request_id']);assert.equal(response.json().error.code,'service_unavailable');
  assert.match(response.json().request_id,/^[0-9a-f-]{36}$/);assert.ok(!response.body.includes('private-SQL'));
 }finally{await app.close();}
});
test('malformed or oversized JSON becomes a stable400, never a stack',async()=>{
 const app=await createSessionApp(async(a)=>{a.post('/__adapter/fixture',async()=>({fixture:true}));},pool,config);
 try{
  for(const payload of ['{broken',JSON.stringify({text:'x'.repeat(9000)})]){
   const r=await app.inject({method:'POST',url:'/__adapter/fixture',headers:{'content-type':'application/json'},payload});
   assert.equal(r.statusCode,400);assert.equal(r.json().error.code,'invalid_request');assert.ok(!r.body.includes('stack'));
  }
 }finally{await app.close();}
});
