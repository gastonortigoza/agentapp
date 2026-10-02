// Trusted Codex infrastructure fixture. No auth module, login, DB, or product claim.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import https from 'node:https';
import {execFileSync} from 'node:child_process';
import {chromium} from 'playwright';
const ca='/work/ca.pem', checks=[];
assert.equal(process.env.AUTH_TLS_FIXTURE,'1');
const server=https.createServer({key:fs.readFileSync('/tmp/server-key.pem'),cert:fs.readFileSync('/work/server.pem'),minVersion:'TLSv1.2'},(req,res)=>{
 if(req.url?.endsWith('/probe')){
  res.setHeader('Content-Type','application/json');res.end(JSON.stringify({received:req.headers.cookie?.includes('refresh=synthetic-tls-fixture')??false}));return;
 }
 res.setHeader('Content-Type','text/html');
 res.setHeader('Set-Cookie','refresh=synthetic-tls-fixture; Path=/api/auth; Max-Age=604800; HttpOnly; Secure; SameSite=Lax');
 res.end('<!doctype html><html lang="es"><title>Fixture TLS</title><h1>Fixture de infraestructura</h1></html>');
});
await new Promise(resolve=>server.listen(9443,'127.0.0.1',resolve));
const base='https://localhost:9443';let browser;
async function launch(home){
 fs.mkdirSync(home,{recursive:true});
 return chromium.launch({headless:true,chromiumSandbox:false,env:{...process.env,HOME:home},
  args:['--no-proxy-server','--host-resolver-rules=MAP wrong.fixture.invalid 127.0.0.1']});
}
async function reject(page,url,expected){
 let message='';try{await page.goto(url,{timeout:10000});}catch(error){message=error.message;}
 assert.ok(message.includes(expected),`Expected ${expected}, got ${message}`);
}
try{
 browser=await launch('/tmp/untrusted-browser');
 let context=await browser.newContext({ignoreHTTPSErrors:false});let page=await context.newPage();
 await reject(page,base+'/api/auth/fixture','ERR_CERT_AUTHORITY_INVALID');checks.push('untrusted_CA_rejected');
 await browser.close();browser=undefined;
 const home='/tmp/trusted-browser',db=home+'/.pki/nssdb';fs.mkdirSync(db,{recursive:true});
 execFileSync('/usr/bin/certutil',['-N','--empty-password','-d','sql:'+db]);
 execFileSync('/usr/bin/certutil',['-A','-d','sql:'+db,'-t','C,,','-n','age50-disposable-fixture-CA','-i',ca]);
 const listing=execFileSync('/usr/bin/certutil',['-L','-d','sql:'+db],{encoding:'utf8'});
 assert.ok(listing.includes('age50-disposable-fixture-CA'));assert.ok(listing.includes('C,,'));
 browser=await launch(home);context=await browser.newContext({ignoreHTTPSErrors:false});page=await context.newPage();
 const response=await page.goto(base+'/api/auth/fixture',{timeout:10000});assert.equal(response.status(),200);
 assert.equal(await page.evaluate(()=>window.isSecureContext),true);checks.push('trusted_CA_HTTPS_secure_context');
 // Synthetic cookies in a fresh disposable context only; never a personal session.
 const cookies=await context.cookies(base+'/api/auth/fixture');assert.equal(cookies.length,1);
 const cookie=cookies[0];assert.equal(cookie.name,'refresh');assert.equal(cookie.secure,true);assert.equal(cookie.httpOnly,true);
 assert.equal(cookie.sameSite,'Lax');assert.equal(cookie.path,'/api/auth');
 assert.ok(cookie.expires-Date.now()/1000<=604801&&cookie.expires-Date.now()/1000>=604790);
 checks.push('Secure_HttpOnly_Lax_path_and_TTL');
 assert.equal(await page.evaluate(()=>document.cookie),'');checks.push('HttpOnly_hidden_at_matching_path');
 assert.equal(await page.evaluate(async()=>{const r=await fetch('/api/auth/probe',{credentials:'include'});return (await r.json()).received;}),true);
 checks.push('browser_sends_cookie_over_validated_TLS');
 assert.equal(await page.evaluate(async()=>{const r=await fetch('/outside/probe',{credentials:'include'});return (await r.json()).received;}),false);
 checks.push('cookie_not_sent_outside_auth_path');
 await reject(page,'https://wrong.fixture.invalid:9443/api/auth/fixture','ERR_CERT_COMMON_NAME_INVALID');checks.push('wrong_hostname_rejected');
 const unsafe=[];
 for(const name of fs.readdirSync('/proc').filter(x=>/^\d+$/.test(x))){
  try{
   const argv=fs.readFileSync('/proc/'+name+'/cmdline','utf8').split('\0');
   if(argv.some(a=>a.includes('/chrome'))){for(const arg of argv)if(/^--(?:ignore-certificate-errors|allow-insecure-localhost)/.test(arg))unsafe.push(arg);}
  }catch{}
 }
 assert.deepEqual(unsafe,[]);checks.push('certificate_validation_bypasses_absent');
 console.log(JSON.stringify({state:'qualified_disposable_chromium_TLS',browser_version:browser.version(),checks,
  product_tests_executed:false,host_trust_changed:false,certificate_validation_disabled:false,private_key_published:false}));
}finally{if(browser)await browser.close();await new Promise(resolve=>server.close(resolve));}
