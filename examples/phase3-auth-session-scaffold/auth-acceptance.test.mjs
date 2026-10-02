// Codex frozen integration harness. No SaaS routes are implemented here.
// Run only inside a separately qualified disposable PostgreSQL auth sandbox.
import {test,before,after,beforeEach} from 'node:test';
import assert from 'node:assert/strict';
import {randomUUID,createHash,randomBytes} from 'node:crypto';
import {Pool} from 'pg';
import Fastify from 'fastify';
import {jwtVerify,SignJWT} from 'jose';
import {registerAuth,requireOwner} from './backend/src/auth.ts';

assert.equal(process.env.AUTH_DISPOSABLE_FIXTURE,'1','Dedicated disposable auth DB only');
assert.ok(process.env.DATABASE_URL&&process.env.AUTH_FIXTURE_DATABASE_URL,'Separate auth app/fixture roles required');
const pool=new Pool({connectionString:process.env.DATABASE_URL,max:8});
const fixture=new Pool({connectionString:process.env.AUTH_FIXTURE_DATABASE_URL,max:2});
const now=new Date('2026-10-02T02:30:00Z'); // Buenos Aires civil date is still October1.
const config={key:randomBytes(32),issuer:'auth-fixture',audience:'auth-fixture-api',origin:'https://localhost:9443',now:()=>new Date(now)};
let app;
const password='Synthetic-Only-Password-1234';
const adult={email:'Adult@EXAMPLE.invalid ',password,birth_date:'2008-10-01'};
function request(method,url,body,headers={}){
  return app.inject({method,url,headers:{origin:config.origin,...headers},...(body===undefined?{}:{payload:body})});
}
function cookie(response){
  const header=response.headers['set-cookie'];assert.ok(header,'refresh cookie missing');
  const raw=(Array.isArray(header)?header[0]:header);return raw.split(';')[0];
}
function session(response,status=200){
  assert.equal(response.statusCode,status,response.body);
  const value=response.json();assert.deepEqual(Object.keys(value),['session']);
  assert.deepEqual(Object.keys(value.session).sort(),['access_token','expires_in','user_id']);
  assert.equal(value.session.expires_in,900);return value.session;
}
async function counts(){return (await fixture.query('SELECT (SELECT count(*)::integer FROM agentapp.users) users,(SELECT count(*)::integer FROM agentapp.sessions) sessions')).rows[0];}
async function owner(token){return request('GET','/__acceptance/owner',undefined,{authorization:'Bearer '+token});}

before(async()=>{
  // Fail before any cleanup when caller accidentally selects a non-disposable database.
  const identity=(await fixture.query('SELECT current_database() name')).rows[0].name;
  assert.equal(identity,'age50_auth_test');
  const privileges=(await pool.query("SELECT has_table_privilege(current_user,'agentapp.users','INSERT') insert_users,has_table_privilege(current_user,'agentapp.sessions','UPDATE') update_sessions")).rows[0];
  assert.equal(privileges.insert_users,true);assert.equal(privileges.update_sessions,true);
  const role=(await pool.query('SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user')).rows[0];
  assert.deepEqual(role,{rolsuper:false,rolcreatedb:false,rolcreaterole:false});
  app=Fastify({logger:false});await registerAuth(app,pool,config);
  // Trusted harness-only private endpoint, never part of generated module or preview.
  app.get('/__acceptance/owner',async(req,reply)=>{
    try{return {owner:await requireOwner(pool,config,req.headers.authorization)};}
    catch{return reply.code(401).send({error:{code:'invalid_session',message:'invalid_session'},request_id:randomUUID()});}
  });
  await app.ready();
});
beforeEach(async()=>{await fixture.query('TRUNCATE agentapp.sessions,agentapp.users');});
after(async()=>{if(app)await app.close();await pool.end();await fixture.end();});

test('A01 adult boundary uses Argentina civil date; minor/future DOB leave zero rows',async()=>{
  for(const birth_date of ['2008-10-02','2027-01-01']){
    const response=await request('POST','/api/auth/register',{...adult,birth_date});
    assert.equal(response.statusCode,422,response.body);assert.equal(response.json().error.code,'age_invalid');
    assert.deepEqual(await counts(),{users:0,sessions:0});
  }
  const response=await request('POST','/api/auth/register',adult);session(response,201);
  assert.deepEqual(await counts(),{users:1,sessions:1});
  const rows=(await fixture.query('SELECT email,password_hash FROM agentapp.users')).rows;
  assert.equal(rows[0].email,'adult@example.invalid');assert.match(rows[0].password_hash,/^\$argon2id\$v=19\$m=65536,t=3,p=1\$/);
  for(const privateField of ['birth_date','password_hash','password','refresh_hash'])assert.ok(!response.body.includes(privateField));
});

test('Normalization duplicate409 and malformed/extra body400 do not partially insert',async()=>{
  session(await request('POST','/api/auth/register',adult),201);
  assert.equal((await request('POST','/api/auth/register',{...adult,email:' adult@example.INVALID'})).statusCode,409);
  for(const extra of [{user_id:randomUUID()},{role:'admin'},{plan:'promoted'}])assert.equal((await request('POST','/api/auth/register',{...adult,email:'other@example.invalid',...extra})).statusCode,400);
  assert.deepEqual(await counts(),{users:1,sessions:1});
});

test('Session JWT signature/issuer/audience/sid/15min and Secure cookie match contract',async()=>{
  const response=await request('POST','/api/auth/register',adult);const s=session(response,201);
  const {payload}=await jwtVerify(s.access_token,config.key,{issuer:config.issuer,audience:config.audience,algorithms:['HS256'],currentDate:now});
  assert.equal(payload.exp-payload.iat,900);assert.equal(payload.sub,s.user_id);assert.match(payload.sid,/^[0-9a-f-]{36}$/);
  const raw=Array.isArray(response.headers['set-cookie'])?response.headers['set-cookie'][0]:response.headers['set-cookie'];
  for(const flag of ['HttpOnly','Secure','SameSite=Lax','Path=/api/auth','Max-Age=604800'])assert.ok(raw.includes(flag),flag);
  const token=decodeURIComponent(cookie(response).split('=')[1]);assert.equal(Buffer.from(token,'base64url').length,32);
  const persisted=(await fixture.query('SELECT refresh_hash FROM agentapp.sessions WHERE id=$1',[payload.sid])).rows[0];
  assert.equal(persisted.refresh_hash,createHash('sha256').update(token).digest('hex'));assert.ok(!response.body.includes(token));
});

test('A02 unknown/wrong login401 generic; sixth failed login/IP429',async()=>{
  session(await request('POST','/api/auth/register',adult),201);
  const unknown=await request('POST','/api/auth/login',{email:'absent@example.invalid',password});
  const wrong=await request('POST','/api/auth/login',{email:'adult@example.invalid',password:password+'wrong'});
  assert.equal(unknown.statusCode,401);assert.equal(wrong.statusCode,401);
  assert.deepEqual(unknown.json().error,wrong.json().error);assert.equal(wrong.json().error.code,'invalid_credentials');
  for(let n=0;n<3;n++)assert.equal((await request('POST','/api/auth/login',{email:'absent@example.invalid',password})).statusCode,401);
  assert.equal((await request('POST','/api/auth/login',{email:'absent@example.invalid',password})).statusCode,429);
});

test('A03 rotation revokes old access; refresh reuse commits whole-family revocation',async()=>{
  const original=await request('POST','/api/auth/register',adult);const first=session(original,201);const oldCookie=cookie(original);
  const rotated=await request('POST','/api/auth/refresh',{}, {cookie:oldCookie});const second=session(rotated);
  assert.notEqual(cookie(rotated),oldCookie);assert.equal((await owner(first.access_token)).statusCode,401);
  assert.equal((await owner(second.access_token)).statusCode,200);
  assert.equal((await request('POST','/api/auth/refresh',{}, {cookie:oldCookie})).statusCode,401);
  assert.equal((await owner(second.access_token)).statusCode,401);
  assert.equal((await fixture.query('SELECT count(*)::integer n FROM agentapp.sessions WHERE revoked_at IS NULL')).rows[0].n,0);
});

test('Concurrent same-token refresh has one success; detected reuse leaves zero live sessions',async()=>{
  const registration=await request('POST','/api/auth/register',adult);session(registration,201);
  const responses=await Promise.all([0,1].map(()=>request('POST','/api/auth/refresh',{}, {cookie:cookie(registration)})));
  assert.deepEqual(responses.map(r=>r.statusCode).sort(),[200,401]);
  const success=responses.find(r=>r.statusCode===200);assert.equal((await owner(session(success).access_token)).statusCode,401);
  assert.equal((await fixture.query('SELECT count(*)::integer n FROM agentapp.sessions WHERE revoked_at IS NULL')).rows[0].n,0);
});

test('Foreign/missing Origin rejected without rotating the still-valid refresh',async()=>{
  const registration=await request('POST','/api/auth/register',adult);session(registration,201);const stored=cookie(registration);
  assert.equal((await request('POST','/api/auth/refresh',{}, {cookie:stored,origin:'https://other.invalid'})).statusCode,401);
  assert.equal((await app.inject({method:'POST',url:'/api/auth/refresh',payload:{},headers:{cookie:stored}})).statusCode,401);
  session(await request('POST','/api/auth/refresh',{}, {cookie:stored}));
});

test('A24 logout requires Bearer and revokes family; cookie cleared; old access/refresh401',async()=>{
  const registration=await request('POST','/api/auth/register',adult);const s=session(registration,201);const stored=cookie(registration);
  assert.equal((await request('POST','/api/auth/logout',{})).statusCode,401);
  const response=await request('POST','/api/auth/logout',{}, {authorization:'Bearer '+s.access_token});
  assert.equal(response.statusCode,204);assert.equal(response.body,'');
  const raw=Array.isArray(response.headers['set-cookie'])?response.headers['set-cookie'][0]:response.headers['set-cookie'];
  for(const flag of ['HttpOnly','Secure','SameSite=Lax','Path=/api/auth'])assert.ok(raw.includes(flag),flag);
  assert.ok(/Max-Age=0|Expires=Thu, 01 Jan 1970/i.test(raw));
  assert.equal((await owner(s.access_token)).statusCode,401);assert.equal((await request('POST','/api/auth/refresh',{}, {cookie:stored})).statusCode,401);
});

test('Access checks session revocation/expiry and cryptographic claims on every request',async()=>{
  const registration=await request('POST','/api/auth/register',adult);const s=session(registration,201);
  assert.equal((await owner(s.access_token)).statusCode,200);
  const {payload}=await jwtVerify(s.access_token,config.key,{issuer:config.issuer,audience:config.audience,algorithms:['HS256'],currentDate:now});
  await fixture.query('UPDATE agentapp.sessions SET expires_at=$1 WHERE id=$2',[new Date(now.getTime()-1000),payload.sid]);
  assert.equal((await owner(s.access_token)).statusCode,401);
  await fixture.query('UPDATE agentapp.sessions SET expires_at=$1 WHERE id=$2',[new Date(now.getTime()+86400000),payload.sid]);
  for(const [key,issuer,audience,expiry] of [[randomBytes(32),config.issuer,config.audience,900],[config.key,'other',config.audience,900],[config.key,config.issuer,'other',900],[config.key,config.issuer,config.audience,-1]]){
    const forged=await new SignJWT({sid:payload.sid}).setProtectedHeader({alg:'HS256'}).setSubject(s.user_id).setIssuer(issuer).setAudience(audience).setIssuedAt(Math.floor(now.getTime()/1000)).setExpirationTime(Math.floor(now.getTime()/1000)+expiry).sign(key);
    assert.equal((await owner(forged)).statusCode,401);
  }
});
