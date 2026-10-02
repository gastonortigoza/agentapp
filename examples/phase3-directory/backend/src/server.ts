import {Pool} from 'pg';import {createApp} from './app.js';
if(!process.env.DATABASE_URL||!process.env.CURSOR_KEY)throw new Error('Validated local configuration required');
const pool=new Pool({connectionString:process.env.DATABASE_URL,max:5,connectionTimeoutMillis:3000,query_timeout:5000});
const app=createApp(pool,process.env.CURSOR_KEY);
await app.listen({host:'127.0.0.1',port:3000});
for(const signal of ['SIGTERM','SIGINT'])process.on(signal,async()=>{await app.close();await pool.end();process.exit(0);});
