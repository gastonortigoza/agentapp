// Trusted network adapter tests; fixture backend, never an auth implementation.
import {test,before,after} from 'node:test';import assert from 'node:assert/strict';
import fs from 'node:fs';import https from 'node:https';import http from 'node:http';import {createGateway} from './auth-gateway.mjs';
let fixture,gateway,port;const captured=[];
function request(path,method='GET',headers={},body=''){
 return new Promise((resolve,reject)=>{
  const req=https.request({host:'localhost',port,path,method,headers,ca:fs.readFileSync('/work/ca.pem'),rejectUnauthorized:true},res=>{
   let data='';res.on('data',c=>data+=c);res.on('end',()=>resolve({status:res.statusCode,headers:res.headers,body:data}));
  });req.on('error',reject);req.end(body);
 });
}
before(async()=>{
 fs.mkdirSync('/tmp/gateway-static',{recursive:true});fs.writeFileSync('/tmp/gateway-static/index.html','<h1>Static fixture</h1>');
 fs.writeFileSync('/tmp/gateway-secret','private-fixture-file');fs.symlinkSync('/tmp/gateway-secret','/tmp/gateway-static/escape');
 fixture=http.createServer((req,res)=>{let body='';req.on('data',c=>body+=c);req.on('end',()=>{
  captured.push({headers:req.headers,method:req.method,path:req.url,body});res.setHeader('Set-Cookie','refresh=fixture; HttpOnly; Secure; SameSite=Lax; Path=/api/auth');
  res.statusCode=201;res.end('{"fixture":true}');
 });});await new Promise(r=>fixture.listen(0,'127.0.0.1',r));
 gateway=createGateway({staticRoot:'/tmp/gateway-static',key:fs.readFileSync('/tmp/server-key.pem'),cert:fs.readFileSync('/work/server.pem'),backendPort:fixture.address().port});
 await new Promise(r=>gateway.listen(0,'127.0.0.1',r));port=gateway.address().port;
});
after(async()=>{await Promise.all([new Promise(r=>gateway.close(r)),new Promise(r=>fixture.close(r))]);});
test('HTTPS public/auth SPA routes remain available without authentication',async()=>{
 for(const route of ['/','/registro','/ingresar','/mi-perfil']){const r=await request(route);assert.equal(r.status,200);assert.equal(r.body,'<h1>Static fixture</h1>');}
});
test('API proxy preserves method/body/origin/cookie/authorization and response cookie flags',async()=>{
 const r=await request('/api/auth/register','POST',{'content-type':'application/json',origin:'https://localhost:9443',authorization:'Bearer fixture',cookie:'refresh=fixture'},'{"fixture":true}');
 assert.equal(r.status,201);assert.equal(r.body,'{"fixture":true}');assert.ok(r.headers['set-cookie'][0].includes('HttpOnly; Secure; SameSite=Lax; Path=/api/auth'));
 const c=captured.at(-1);assert.equal(c.method,'POST');assert.equal(c.body,'{"fixture":true}');assert.equal(c.headers.origin,'https://localhost:9443');
 assert.equal(c.headers.authorization,'Bearer fixture');assert.equal(c.headers.cookie,'refresh=fixture');
});
test('caller forwarding headers are stripped before the backend',async()=>{
 await request('/api/auth/login','POST',{'x-forwarded-for':'attacker','x-forwarded-host':'attacker','x-forwarded-proto':'https',forwarded:'for=attacker'},'{}');
 for(const name of ['x-forwarded-for','x-forwarded-host','x-forwarded-proto','forwarded'])assert.equal(captured.at(-1).headers[name],undefined);
});
test('static symlink/traversal cannot expose files outside root',async()=>{
 for(const route of ['/escape','/%2e%2e/gateway-secret','/%00']){const r=await request(route);assert.ok([400,404].includes(r.status));assert.ok(!r.body.includes('private-fixture-file'));}
});
