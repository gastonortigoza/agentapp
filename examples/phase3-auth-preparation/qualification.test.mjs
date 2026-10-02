// Codex infrastructure qualification. This file implements no SaaS auth routes.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {randomBytes} from 'node:crypto';
import {hash,verify,Algorithm,Version} from '@node-rs/argon2';
import {SignJWT,jwtVerify} from 'jose';
import parsePhoneNumber from 'libphonenumber-js/max';
import cookie from '@fastify/cookie';
import Fastify from 'fastify';

test('Argon2id exact contract parameters and randomized salt; correct/wrong password',async()=>{
  const password=randomBytes(24).toString('base64url');
  const options={algorithm:Algorithm.Argon2id,version:Version.V0x13,memoryCost:65536,timeCost:3,parallelism:1,outputLen:32};
  const first=await hash(password,options),second=await hash(password,options);
  assert.match(first,/^\$argon2id\$v=19\$m=65536,t=3,p=1\$/);
  assert.notEqual(first,second);
  assert.equal(await verify(first,password),true);
  assert.equal(await verify(first,password+'wrong'),false);
});

const issuer='synthetic-qualification',audience='synthetic-api';
const key=randomBytes(32);
async function token(exp){return new SignJWT({sid:'synthetic-session'}).setProtectedHeader({alg:'HS256'})
  .setSubject('synthetic-owner').setIssuer(issuer).setAudience(audience).setIssuedAt().setExpirationTime(exp).sign(key);}
test('JWT validates signature, issuer, audience, sid and15min expiry',async()=>{
  const {payload}=await jwtVerify(await token('15m'),key,{issuer,audience,algorithms:['HS256']});
  assert.equal(payload.sub,'synthetic-owner');assert.equal(payload.sid,'synthetic-session');
  assert.equal(payload.exp-payload.iat,900);
});
test('JWT wrong signing key rejected',async()=>{
  await assert.rejects(jwtVerify(await token('15m'),randomBytes(32),{issuer,audience,algorithms:['HS256']}),{code:'ERR_JWS_SIGNATURE_VERIFICATION_FAILED'});
});
test('JWT wrong issuer rejected',async()=>{
  await assert.rejects(jwtVerify(await token('15m'),key,{issuer:'wrong',audience,algorithms:['HS256']}),{code:'ERR_JWT_CLAIM_VALIDATION_FAILED'});
});
test('JWT wrong audience rejected',async()=>{
  await assert.rejects(jwtVerify(await token('15m'),key,{issuer,audience:'wrong',algorithms:['HS256']}),{code:'ERR_JWT_CLAIM_VALIDATION_FAILED'});
});
test('JWT expired token rejected',async()=>{
  await assert.rejects(jwtVerify(await token(Math.floor(Date.now()/1000)-1),key,{issuer,audience,algorithms:['HS256']}),{code:'ERR_JWT_EXPIRED'});
});
test('Full phone metadata classifies synthetic AR mobile as MOBILE with E.164',()=>{
  const phone=parsePhoneNumber('+5491123456789',{extract:false});
  assert.equal(phone.country,'AR');assert.equal(phone.number,'+5491123456789');
  assert.equal(phone.isValid(),true);assert.equal(phone.getType(),'MOBILE');
});
test('Full phone metadata distinguishes fixed lines and rejects malformed input',()=>{
  const fixed=parsePhoneNumber('+541143211234',{extract:false});
  assert.equal(fixed.isValid(),true);assert.equal(fixed.getType(),'FIXED_LINE');
  assert.equal(parsePhoneNumber('invalid-phone',{defaultCountry:'AR',extract:false}),undefined);
});
test('Fastify5 cookie plugin emits contract flags and parses cookie',async()=>{
  const app=Fastify({logger:false});
  try {
    await app.register(cookie);
    app.get('/qualification-cookie',async(req,reply)=>{
      reply.setCookie('refresh','synthetic-value',{httpOnly:true,secure:true,sameSite:'lax',path:'/api/auth',maxAge:604800});
      return {parsed:req.cookies.refresh??null};
    });
    const response=await app.inject({method:'GET',url:'/qualification-cookie',headers:{cookie:'refresh=synthetic-input'}});
    assert.equal(response.statusCode,200);assert.equal(response.json().parsed,'synthetic-input');
    const set=response.headers['set-cookie'];
    for(const flag of ['HttpOnly','Secure','SameSite=Lax','Path=/api/auth','Max-Age=604800'])assert.ok(set.includes(flag));
  } finally {await app.close();}
});
