// Codex trusted integration scaffold; the local agent supplies the registrar.
import Fastify, {type FastifyInstance} from 'fastify';
import {type Pool} from 'pg';
import {randomUUID} from 'node:crypto';
export type AuthConfig={key:Uint8Array;issuer:string;audience:string;origin:string;now?:()=>Date};
export type Registrar=(app:FastifyInstance,pool:Pool,config:AuthConfig)=>Promise<void>;
export function validateAuthConfig(config:AuthConfig){
 if(!(config.key instanceof Uint8Array)||config.key.length<32||!config.issuer||!config.audience)throw new Error('Validated auth configuration required');
 const origin=new URL(config.origin);
 if(origin.protocol!=='https:'||origin.origin!==config.origin||origin.username||origin.password)throw new Error('HTTPS origin required');
}
export async function createSessionApp(register:Registrar,pool:Pool,config:AuthConfig){
 validateAuthConfig(config);
 const app=Fastify({logger:false,bodyLimit:8192});
 app.setErrorHandler((error,_request,reply)=>{
  const invalid=Boolean(error.validation)||error.statusCode===400||error.statusCode===413;
  const code=invalid?'invalid_request':'service_unavailable';
  reply.code(invalid?400:503).send({error:{code,message:invalid?'Solicitud inválida':'Servicio temporalmente no disponible'},request_id:randomUUID()});
 });
 try{await register(app,pool,config);return app;}catch(error){await app.close();throw error;}
}
