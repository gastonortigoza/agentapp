// Repository qualification of Codex wiring with explicit fixture Pages/API.
// Never runs an agent AuthPages.tsx, auth.ts, or a personal browser session.
import assert from 'node:assert/strict';import fs from 'node:fs';
import https from 'node:https';import http from 'node:http';import {execFileSync} from 'node:child_process';
import {chromium} from 'playwright';
assert.equal(process.env.AUTH_WIRING_FIXTURE,'1');
const checks=[],calls=[];let failed=false,delay=false,pendingResponse;
const base='https://localhost:9443';
function handler(req,res){
 if(req.url==='/api/auth/logout'){
  let body='';req.on('data',c=>body+=c);req.on('end',()=>{
   calls.push({authorization:req.headers.authorization,origin:req.headers.origin,cookie:req.headers.cookie,body});
   if(delay){pendingResponse=res;return;}
   res.statusCode=failed?204:503;failed=true;res.end();
  });return;
 }
 const path=new URL(req.url,'http://localhost').pathname;
 if(path.startsWith('/assets/')){
  if(!/^\/assets\/[A-Za-z0-9_.-]+$/.test(path)){res.statusCode=404;res.end();return;}
  res.setHeader('Content-Type',path.endsWith('.css')?'text/css':'text/javascript');
  res.end(fs.readFileSync('/work/dist'+path));return;
 }
 res.setHeader('Content-Type','text/html');
 if(req.socket.encrypted)res.setHeader('Set-Cookie','refresh=synthetic-wiring-cookie; Path=/api/auth; Max-Age=604800; HttpOnly; Secure; SameSite=Lax');
 res.end(fs.readFileSync('/work/dist/wiring.html'));
}
const tls=https.createServer({key:fs.readFileSync('/tmp/server-key.pem'),cert:fs.readFileSync('/work/server.pem'),minVersion:'TLSv1.2'},handler);
const plain=http.createServer(handler);
await new Promise(r=>tls.listen(9443,'127.0.0.1',r));await new Promise(r=>plain.listen(9442,'127.0.0.1',r));
const home='/tmp/wiring-browser',db=home+'/.pki/nssdb';fs.mkdirSync(db,{recursive:true});
execFileSync('/usr/bin/certutil',['-N','--empty-password','-d','sql:'+db]);
execFileSync('/usr/bin/certutil',['-A','-d','sql:'+db,'-t','C,,','-n','age50-wiring-fixture-CA','-i','/work/ca.pem']);
let browser;
async function visible(page,text){await page.getByText(text,{exact:true}).waitFor({state:'visible',timeout:5000});}
async function waitFor(fn){const until=Date.now()+5000;while(!fn()){assert.ok(Date.now()<until,'Fixture observation timed out');await new Promise(r=>setTimeout(r,25));}}
try{
 browser=await chromium.launch({headless:true,chromiumSandbox:false,env:{...process.env,HOME:home},args:['--no-proxy-server']});
 const context=await browser.newContext({ignoreHTTPSErrors:false});const page=await context.newPage();
 await page.goto(base+'/');await visible(page,'Directorio fixture público');
 assert.equal(await page.getByText('Sesión iniciada',{exact:true}).count(),0);checks.push('public_root_without_session');
 await page.getByRole('link',{name:'Registrarse',exact:true}).click();await visible(page,'Fixture registro');
 await page.getByRole('button',{name:'Emitir sesión fixture',exact:true}).click();await visible(page,'Sesión iniciada');
 assert.equal(new URL(page.url()).pathname,'/mi-perfil');assert.ok(!(await page.content()).includes('fixture.token.value'));
 assert.deepEqual(await page.evaluate(()=>({local:localStorage.length,session:sessionStorage.length})),{local:0,session:0});
 checks.push('session_memory_only_and_owner_navigation');
 await page.reload();await visible(page,'Ingresá para gestionar tu perfil.');
 assert.equal(await page.getByText('Sesión iniciada',{exact:true}).count(),0);checks.push('reload_does_not_restore_access_token');
 await page.getByRole('link',{name:'Registrarse',exact:true}).click();await visible(page,'Fixture registro');
 await page.getByRole('button',{name:'Retener callback viejo'}).click();await page.getByRole('link',{name:'Ingresar',exact:true}).click();
 await visible(page,'Fixture login');await page.getByRole('button',{name:'Liberar callback viejo'}).click();
 assert.equal(new URL(page.url()).pathname,'/ingresar');assert.equal(await page.getByText('Sesión iniciada',{exact:true}).count(),0);
 checks.push('old_page_callback_cannot_resurrect_session');
 await page.getByRole('button',{name:'Emitir respuesta inválida'}).click();await visible(page,'Respuesta de sesión inválida');
 assert.equal(await page.getByText('Sesión iniciada',{exact:true}).count(),0);checks.push('malformed_session_rejected');
 await page.getByRole('button',{name:'Emitir sesión fixture',exact:true}).click();await visible(page,'Sesión iniciada');
 await page.getByRole('button',{name:'Cerrar sesión',exact:true}).click();await visible(page,'No pudimos cerrar la sesión. Podés reintentar.');
 await visible(page,'Sesión iniciada');await page.getByRole('button',{name:'Cerrar sesión',exact:true}).click();await visible(page,'Fixture login');
 assert.equal(await page.getByText('Sesión iniciada',{exact:true}).count(),0);assert.equal(calls.length,2);
 for(const c of calls){assert.equal(c.authorization,'Bearer fixture.token.value');assert.equal(c.origin,base);assert.equal(c.body,'{}');assert.ok(c.cookie.includes('refresh=synthetic-wiring-cookie'));}
 checks.push('logout_failure_retains_session_retry_204_clears_it');
 await page.getByRole('button',{name:'Emitir sesión fixture',exact:true}).click();await visible(page,'Sesión iniciada');delay=true;
 await page.getByRole('button',{name:'Cerrar sesión',exact:true}).click();await waitFor(()=>Boolean(pendingResponse));
 await page.getByRole('button',{name:'Fixture navegación registro'}).click();await visible(page,'Fixture registro');
 await page.getByRole('button',{name:'Emitir otra sesión fixture'}).click();await visible(page,'Sesión iniciada');
 pendingResponse.statusCode=204;pendingResponse.end();pendingResponse=undefined;delay=false;
 await page.getByRole('button',{name:'Cerrar sesión',exact:true}).waitFor({state:'visible'});
 // Wait for the response handler to finish via the control becoming enabled.
 await page.waitForFunction(()=>!Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='Cerrar sesión')?.disabled);
 await visible(page,'Sesión iniciada');assert.equal(new URL(page.url()).pathname,'/mi-perfil');
 checks.push('late_logout_cannot_clear_replacement_session');
 for(const width of [360,1280]){
  await page.setViewportSize({width,height:800});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth),false);
 }
 checks.push('shell_navigation_no_overflow_360_1280');
 await page.goto('http://localhost:9442/registro');await visible(page,'El acceso a tu cuenta requiere HTTPS.');
 assert.equal(await page.getByRole('button',{name:'Emitir sesión fixture',exact:true}).count(),0);
 checks.push('HTTP_auth_pages_blocked');
 console.log(JSON.stringify({state:'qualified_trusted_auth_wiring',checks,browser_version:browser.version(),
  fixture_pages:true,fixture_logout_API:true,agent_sources_executed:false,auth_acceptance_executed:false,model_calls:0,host_trust_changed:false}));
}finally{
 if(pendingResponse){pendingResponse.statusCode=503;pendingResponse.end();}
 if(browser)await browser.close();
 await Promise.all([new Promise(r=>tls.close(r)),new Promise(r=>plain.close(r))]);
}
