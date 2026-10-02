// Codex local infrastructure check, no auth route/product acceptance.
import https from 'node:https';
import {readFile,writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import assert from 'node:assert/strict';
const manifestPath=new URL('./auth-tls-preparation.json',import.meta.url);
const receipt=JSON.parse(await readFile(manifestPath,'utf8'));
if(process.argv.includes('--verify-socket')){
  assert.equal(receipt.state,'tls_probe_verified');
  await writeFile(new URL('./auth-tls-preparation-original-1.json',import.meta.url),JSON.stringify(receipt,null,2)+'\n',{flag:'wx'});
  receipt.prior_probe='auth-tls-preparation-original-1.json';
}else assert.equal(receipt.state,'prepared_pending_probe');
const files={};
for(const [name,sha] of Object.entries(receipt.sha256)){
  const data=await readFile(receipt.private_directory+'/'+name);assert.equal(createHash('sha256').update(data).digest('hex'),sha);files[name]=data;
}
const server=https.createServer({key:files['server-key.pem'],cert:files['server.pem'],minVersion:'TLSv1.2'},(req,res)=>{
  if(req.url!=='/health'){res.writeHead(404);res.end();return;}
  res.setHeader('Set-Cookie','refresh=synthetic-tls-probe; HttpOnly; Secure; SameSite=Lax; Path=/api/auth; Max-Age=604800');
  res.setHeader('Content-Type','application/json');res.end('{"scope":"tls_infrastructure_only"}');
});
await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});
const port=server.address().port;
function get(options){return new Promise((resolve,reject)=>{
  const req=https.get({host:'127.0.0.1',port,path:'/health',timeout:3000,...options},res=>{
    const authorized=res.socket.authorized;
    let body='';res.on('data',c=>body+=c);res.on('end',()=>resolve({status:res.statusCode,body,cookie:res.headers['set-cookie'],authorized}));
  });req.once('error',reject);req.once('timeout',()=>req.destroy(new Error('TLS probe timeout')));
});}
try{
  const valid=await get({ca:files['ca.pem'],servername:'localhost',rejectUnauthorized:true});
  assert.equal(valid.status,200);assert.equal(valid.authorized,true);
  for(const flag of ['HttpOnly','Secure','SameSite=Lax','Path=/api/auth','Max-Age=604800'])assert.ok(valid.cookie[0].includes(flag));
  await assert.rejects(get({ca:files['ca.pem'],servername:'wrong.invalid',rejectUnauthorized:true}),{code:'ERR_TLS_CERT_ALTNAME_INVALID'});
  await assert.rejects(get({servername:'localhost',rejectUnauthorized:true}));
  receipt.state='tls_probe_verified';receipt.checks=['Trusted fixture CA+localhost validation+HTTPS200','Secure-cookie headers over actual TLS','Wrong hostname rejected','Untrusted certificate rejected'];
  receipt.probe_finished_at=new Date().toISOString();receipt.listener_closed=true;
  await writeFile(manifestPath,JSON.stringify(receipt,null,2)+'\n');
  console.log(JSON.stringify({state:receipt.state,checks:receipt.checks,host_or_browser_trust_changed:false,secure_cookie_browser_check:false}));
}finally{await new Promise(resolve=>server.close(resolve));}
