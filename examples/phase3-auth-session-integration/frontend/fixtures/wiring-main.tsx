// Explicit test-only Pages fixture. Never placed at AuthPages.tsx or a product entry.
import {createRoot} from 'react-dom/client';import AuthShell,{type PageProps,type Session} from '../src/AuthShell';
const synthetic:Session={access_token:'fixture.token.value',expires_in:900,user_id:'00000000-0000-4000-8000-000000000001'};
let saved:()=>void=()=>{};
function FixturePages({mode,onSession}:PageProps){
 return <main><h1>{mode==='register'?'Fixture registro':'Fixture login'}</h1>
  <button onClick={()=>onSession(synthetic)}>Emitir sesión fixture</button>
  <button onClick={()=>onSession({...synthetic,access_token:'fixture.token.second'})}>Emitir otra sesión fixture</button>
  <button onClick={()=>onSession({...synthetic,user_id:'invalid'})}>Emitir respuesta inválida</button>
  <button onClick={()=>{saved=()=>onSession(synthetic);}}>Retener callback viejo</button>
 </main>;
}
createRoot(document.getElementById('root')!).render(<><AuthShell Pages={FixturePages} directory={<main><h1>Directorio fixture público</h1></main>}/>
 <button onClick={()=>saved()}>Liberar callback viejo</button>
 <button onClick={()=>{window.history.pushState(null,'','/registro');window.dispatchEvent(new PopStateEvent('popstate'));}}>Fixture navegación registro</button></>);
