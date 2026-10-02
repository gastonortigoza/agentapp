// Codex infrastructure check. No auth proposal, endpoint, UI, or acceptance claim.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
const root=new URL('./',import.meta.url);
const manifests=['package.json','backend/package.json','frontend/package.json'];
const lock=JSON.parse(fs.readFileSync(new URL('package-lock.json',root),'utf8'));
assert.equal(lock.lockfileVersion,3);
let pins=0;
for(const name of manifests){
 const pkg=JSON.parse(fs.readFileSync(new URL(name,root),'utf8'));
 const req=createRequire(new URL(name,root));
 const key=name==='package.json'?'':name.replace('/package.json','');
 for(const field of ['dependencies','devDependencies']){
  assert.deepEqual(lock.packages[key][field]??{},pkg[field]??{});
  for(const [dependency,version] of Object.entries(pkg[field]??{})){
   // Walk from the resolved entry to its own manifest, including exports-only packages.
   let path;
   try{path=req.resolve(dependency+'/package.json');}
   catch{path=req.resolve(dependency);}
   for(let i=0;i<12;i++){
    const parent=path.slice(0,path.lastIndexOf('/'));
    assert.ok(parent&&parent!==path);path=parent;
    if(!fs.existsSync(path+'/package.json'))continue;
    const actual=JSON.parse(fs.readFileSync(path+'/package.json','utf8'));
    if(actual.name!==dependency)continue;
    assert.equal(actual.version,version,dependency);pins++;break;
   }
  }
 }
}
assert.equal(pins,19);
const argon=await import('@node-rs/argon2');assert.equal(typeof argon.hash,'function');
const pg=await import('pg');assert.equal(typeof pg.default.Pool,'function');
const jwt=await import('jose');assert.equal(typeof jwt.SignJWT,'function');
const cookie=await import('@fastify/cookie');assert.equal(typeof cookie.default,'function');
const react=await import('react');const render=await import('react-dom/server');
assert.equal(render.renderToStaticMarkup(react.createElement('span',null,'runtime')),'<span>runtime</span>');
const ts=await import('./runtime-loader-probe.ts');assert.equal(ts.value,'typed-loader');
for(const name of ['backend/src/auth.ts','frontend/src/AuthPages.tsx'])assert.equal(fs.existsSync(fileURLToPath(new URL(name,root))),false);
assert.equal(fs.readFileSync('/proc/mounts','utf8').split('\n').find(l=>l.split(' ')[1]==='/').split(' ')[3].split(',').includes('ro'),true);
console.log(JSON.stringify({state:'runtime_inputs_verified',direct_pins:pins,native_loader:true,typed_loader:true,product_sources:false,product_tests:false}));
