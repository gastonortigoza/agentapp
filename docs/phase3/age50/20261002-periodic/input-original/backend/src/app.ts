import Fastify from 'fastify';
import {Pool} from 'pg';
import {createHash,createHmac,timingSafeEqual,randomUUID} from 'node:crypto';

const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
type Query={country_id?:string;province_id?:string;zone_id?:string;cursor?:string;limit?:string};
type Boundary={hash:string;rank:number;at:string;id:string};
class InputError extends Error{constructor(public status:number,public code:string){super(code);}}
export function filtersHash(q:Query){return createHash('sha256').update(JSON.stringify([q.country_id??null,q.province_id??null,q.zone_id??null])).digest('hex');}
export function readQuery(value:unknown):Query{
  const q=value as Record<string,unknown>;
  if(!q||Object.keys(q).some(k=>!['country_id','province_id','zone_id','cursor','limit'].includes(k)))throw new InputError(400,'invalid_request');
  for(const key of ['country_id','province_id','zone_id'])if(q[key]!==undefined&&(typeof q[key]!=='string'||!uuid.test(q[key] as string)))throw new InputError(400,'invalid_request');
  if(q.limit!==undefined&&(typeof q.limit!=='string'||!/^[1-9][0-9]?$/.test(q.limit)||Number(q.limit)>50))throw new InputError(400,'invalid_request');
  if(q.cursor!==undefined&&(typeof q.cursor!=='string'||q.cursor.length>1024))throw new InputError(400,'invalid_request');
  return q as Query;
}
export function cursor(b:Boundary,key:string){const text=Buffer.from(JSON.stringify(b)).toString('base64url');return text+'.'+createHmac('sha256',key).update(text).digest('base64url');}
export function decodeCursor(token:string,q:Query,key:string):Boundary{
  try{
    const [body,sig,...rest]=token.split('.');if(rest.length||!body||!sig||!/^[A-Za-z0-9_-]+$/.test(body+sig))throw new Error();
    const expected=createHmac('sha256',key).update(body).digest(),received=Buffer.from(sig,'base64url');
    if(received.length!==expected.length||!timingSafeEqual(received,expected))throw new Error();
    const b=JSON.parse(Buffer.from(body,'base64url').toString()) as Boundary;
    if(Object.keys(b).sort().join(',')!=='at,hash,id,rank'||b.hash!==filtersHash(q)||![0,1].includes(b.rank)||typeof b.id!=='string'||!uuid.test(b.id)||typeof b.at!=='string'||!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$/.test(b.at)||!Number.isFinite(Date.parse(b.at)))throw new Error();
    return b;
  }catch{throw new InputError(400,'invalid_request');}
}

const eligible=`FROM agentapp.profiles p JOIN agentapp.users u ON u.id=p.user_id
 JOIN agentapp.zones z ON z.id=p.zone_id JOIN agentapp.countries c ON c.id=p.country_id
 JOIN LATERAL (SELECT s.plan_id FROM agentapp.subscriptions s JOIN agentapp.plans pl ON pl.id=s.plan_id AND pl.enabled
   WHERE s.user_id=p.user_id AND s.starts_at<=statement_timestamp() AND s.ends_at>statement_timestamp()
   ORDER BY (s.plan_id='promoted') DESC,s.starts_at DESC,s.id DESC LIMIT 1) sub ON true
 JOIN agentapp.photos ph ON ph.profile_id=p.id AND ph.is_main
 WHERE p.published AND c.code='AR' AND btrim(p.display_name)<>'' AND btrim(p.gender)<>''
 AND btrim(p.description)<>'' AND p.phone_e164 ~ '^\\+549[0-9]{10}$'
 AND u.birth_date <= (statement_timestamp() AT TIME ZONE 'America/Argentina/Buenos_Aires')::date - INTERVAL '18 years'`;
const selected=`SELECT p.id,p.display_name,p.gender,p.country_id,p.province_id,p.zone_id,z.name AS zone_label,
 p.description,EXTRACT(YEAR FROM age((statement_timestamp() AT TIME ZONE 'America/Argentina/Buenos_Aires')::date,u.birth_date))::integer AS age,
 sub.plan_id AS plan,CASE WHEN sub.plan_id='promoted' THEN 1 ELSE 0 END AS rank,
 to_char(p.created_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS sort_at,
 json_build_object('id',ph.id,'url','/synthetic-placeholder.png','is_main',ph.is_main,'created_at',ph.created_at) AS main_photo,
 'https://wa.me/'||substring(p.phone_e164 from 2) AS whatsapp_url `;

export function createApp(pool:Pool,key:string){
  if(key.length<32)throw new Error('Cursor signing key required');
  const app=Fastify({logger:false,bodyLimit:8192});
  app.setErrorHandler((err,req,reply)=>{
    const requestId=randomUUID();
    if(err instanceof InputError)return reply.code(err.status).send({error:{code:err.code,message:err.code},request_id:requestId});
    return reply.code(503).send({error:{code:'service_unavailable',message:'Servicio temporalmente no disponible'},request_id:requestId});
  });
  async function hierarchy(q:Query){
    if(q.country_id&&(await pool.query('SELECT 1 FROM agentapp.countries WHERE id=$1 AND code=\'AR\'',[q.country_id])).rowCount!==1)throw new InputError(422,'geography_invalid');
    if(q.province_id&&(await pool.query('SELECT 1 FROM agentapp.provinces WHERE id=$1 AND ($2::uuid IS NULL OR country_id=$2)',[q.province_id,q.country_id??null])).rowCount!==1)throw new InputError(422,'geography_invalid');
    if(q.zone_id&&(await pool.query('SELECT 1 FROM agentapp.zones WHERE id=$1 AND ($2::uuid IS NULL OR country_id=$2) AND ($3::uuid IS NULL OR province_id=$3)',[q.zone_id,q.country_id??null,q.province_id??null])).rowCount!==1)throw new InputError(422,'geography_invalid');
  }
  app.get('/api/health',async()=>{await pool.query('SELECT 1');return {status:'ok'};});
  app.get('/api/catalog/geography',async req=>{
    const q=readQuery(req.query);if(q.zone_id||q.cursor||q.limit)throw new InputError(400,'invalid_request');await hierarchy(q);
    const countries=(await pool.query("SELECT id,code,name FROM agentapp.countries WHERE code='AR' ORDER BY id")).rows;
    if(!countries.length)throw new InputError(503,'catalog_unavailable');
    const provinces=(await pool.query('SELECT id,country_id,name FROM agentapp.provinces WHERE ($1::uuid IS NULL OR country_id=$1) ORDER BY id',[q.country_id??null])).rows;
    const zones=(await pool.query('SELECT id,country_id,province_id,name FROM agentapp.zones WHERE ($1::uuid IS NULL OR country_id=$1) AND ($2::uuid IS NULL OR province_id=$2) ORDER BY id',[q.country_id??null,q.province_id??null])).rows;
    return {countries,provinces,zones};
  });
  app.get('/api/profiles',async req=>{
    const q=readQuery(req.query),boundary=q.cursor?decodeCursor(q.cursor,q,key):null;await hierarchy(q);
    const size=Number(q.limit??20);
    const rows=(await pool.query(selected+eligible+`
      AND ($1::uuid IS NULL OR p.country_id=$1) AND ($2::uuid IS NULL OR p.province_id=$2) AND ($3::uuid IS NULL OR p.zone_id=$3)
      AND ($4::integer IS NULL OR (CASE WHEN sub.plan_id='promoted' THEN 1 ELSE 0 END,p.created_at,p.id)<($4,$5::timestamptz,$6::uuid))
      ORDER BY rank DESC,p.created_at DESC,p.id DESC LIMIT $7`,[q.country_id??null,q.province_id??null,q.zone_id??null,boundary?.rank??null,boundary?.at??null,boundary?.id??null,size+1])).rows;
    const page=rows.slice(0,size),last=page.at(-1);
    return {items:page.map(({rank,sort_at,...publicFields})=>publicFields),next_cursor:rows.length>size&&last?cursor({hash:filtersHash(q),rank:last.rank,at:last.sort_at,id:last.id},key):null};
  });
  app.get('/api/profiles/:profile_id',async req=>{
    const {profile_id}=req.params as {profile_id:string};
    if(!uuid.test(profile_id))throw new InputError(400,'invalid_request');
    const q=req.query as Record<string,unknown>;
    if(q&&Object.keys(q).length>0)throw new InputError(400,'invalid_request');
    
    const detailQuery = `SELECT p.id,p.display_name,p.gender,p.country_id,p.province_id,p.zone_id,z.name AS zone_label,
      p.description,EXTRACT(YEAR FROM age((statement_timestamp() AT TIME ZONE 'America/Argentina/Buenos_Aires')::date,u.birth_date))::integer AS age,
      sub.plan_id AS plan, 'https://wa.me/'||substring(p.phone_e164 from 2) AS whatsapp_url,
      (SELECT json_agg(json_build_object('id',ph.id,'url','/synthetic-placeholder.png','is_main',ph.is_main,'created_at',ph.created_at) ORDER BY ph.is_main DESC, ph.created_at ASC, ph.id ASC)
       FROM agentapp.photos ph WHERE ph.profile_id=p.id) AS photos
      ` + eligible + ` AND p.id = $1`;

    const result = await pool.query(detailQuery, [profile_id]);
    if(result.rows.length === 0) throw new InputError(404, 'profile_not_found');
    
    const row = result.rows[0];
    const profile = {
      id: row.id,
      display_name: row.display_name,
      gender: row.gender,
      country_id: row.country_id,
      province_id: row.province_id,
      zone_id: row.zone_id,
      zone_label: row.zone_label,
      description: row.description,
      age: row.age,
      plan: row.plan,
      whatsapp_url: row.whatsapp_url,
      photos: row.photos
    };
    return { profile };
  });
  return app;
}
