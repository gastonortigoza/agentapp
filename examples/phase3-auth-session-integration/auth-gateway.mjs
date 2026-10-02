// Trusted operator gateway. Future auth code runs in a different container.
import https from 'node:https';import http from 'node:http';import fs from 'node:fs';import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';
export function createGateway({staticRoot,key,cert,backendPort=3000}){
 const root=fs.realpathSync(staticRoot);
 if(!Number.isInteger(backendPort)||backendPort<1||backendPort>65535)throw new Error('Invalid backend port');
 return https.createServer({key,cert,minVersion:'TLSv1.2'},(req,res)=>{
  let pathname;
  try{pathname=decodeURIComponent(new URL(req.url,'https://localhost').pathname);if(pathname.includes('\0'))throw new Error();}
  catch{res.writeHead(400);res.end();return;}
  if(pathname.startsWith('/api/')){
   const headers={...req.headers,host:'127.0.0.1:'+backendPort};
   // Caller-supplied forwarding headers never become identity/privilege evidence.
   for(const name of ['forwarded','x-forwarded-for','x-forwarded-host','x-forwarded-proto'])delete headers[name];
   const upstream=http.request({hostname:'127.0.0.1',port:backendPort,path:req.url,method:req.method,headers},response=>{
    res.writeHead(response.statusCode??503,response.headers);response.pipe(res);
   });
   upstream.setTimeout(10000,()=>upstream.destroy());
   upstream.on('error',()=>{if(!res.headersSent)res.writeHead(503,{'content-type':'application/json'});res.end(JSON.stringify({error:{code:'service_unavailable',message:'Servicio temporalmente no disponible'},request_id:randomUUID()}));});
   req.on('aborted',()=>upstream.destroy());req.pipe(upstream);return;
  }
  if(!['GET','HEAD'].includes(req.method??'')){res.writeHead(405);res.end();return;}
  const routes=['/','/registro','/ingresar','/mi-perfil'];
  const file=routes.includes(pathname)||/^\/personas\/[0-9a-f-]{36}$/i.test(pathname)?'index.html':'.'+pathname;
  try{
   const resolved=fs.realpathSync(path.resolve(root,file));
   if(!resolved.startsWith(root+path.sep)||!fs.statSync(resolved).isFile())throw new Error();
   const type=resolved.endsWith('.js')?'text/javascript':resolved.endsWith('.css')?'text/css':resolved.endsWith('.html')?'text/html':resolved.endsWith('.png')?'image/png':'application/octet-stream';
   res.writeHead(200,{'content-type':type,'x-content-type-options':'nosniff'});
   if(req.method==='HEAD'){res.end();return;}fs.createReadStream(resolved).pipe(res);
  }catch{res.writeHead(404);res.end();}
 });
}
if(process.argv[1]===fileURLToPath(import.meta.url)){
 const {AUTH_STATIC_DIR,AUTH_TLS_KEY_PATH,AUTH_TLS_CERT_PATH}=process.env;
 if(!AUTH_STATIC_DIR||!AUTH_TLS_KEY_PATH||!AUTH_TLS_CERT_PATH)throw new Error('Gateway configuration required');
 const server=createGateway({staticRoot:AUTH_STATIC_DIR,key:fs.readFileSync(AUTH_TLS_KEY_PATH),cert:fs.readFileSync(AUTH_TLS_CERT_PATH)});
 server.listen(9443,'127.0.0.1');
 for(const signal of ['SIGTERM','SIGINT'])process.on(signal,()=>server.close(()=>process.exit(0)));
}
