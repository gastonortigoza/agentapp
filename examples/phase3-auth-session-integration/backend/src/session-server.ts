// Trusted bootstrap. The generated module receives only explicit auth config/Pool.
import {Pool} from 'pg';
import {registerAuth} from './auth.js';
import {createSessionApp} from './session-app.js';
const {DATABASE_URL,AUTH_KEY_B64,AUTH_ISSUER,AUTH_AUDIENCE,AUTH_ORIGIN}=process.env;
if(!DATABASE_URL||!AUTH_KEY_B64||!AUTH_ISSUER||!AUTH_AUDIENCE||!AUTH_ORIGIN)throw new Error('Auth configuration required');
const key=Buffer.from(AUTH_KEY_B64,'base64');
if(key.toString('base64')!==AUTH_KEY_B64||key.length<32)throw new Error('Invalid auth key');
const pool=new Pool({connectionString:DATABASE_URL,max:8,connectionTimeoutMillis:3000,query_timeout:5000});
const app=await createSessionApp(registerAuth,pool,{key,issuer:AUTH_ISSUER,audience:AUTH_AUDIENCE,origin:AUTH_ORIGIN})
 .catch(async(error)=>{await pool.end();throw error;});
try{await app.listen({host:'127.0.0.1',port:3000});}catch(error){await app.close();await pool.end();throw error;}
for(const signal of ['SIGTERM','SIGINT'])process.on(signal,async()=>{await app.close();await pool.end();process.exit(0);});
