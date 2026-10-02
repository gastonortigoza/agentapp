// Codex trusted routing/session scaffold. AuthPages is supplied by a local agent.
import {useEffect,useRef,useState,type ComponentType,type ReactNode} from 'react';
export type Session={access_token:string;expires_in:number;user_id:string};
export type PageProps={mode:'register'|'login';onSession:(session:Session)=>void};
type Props={Pages:ComponentType<PageProps>;directory:ReactNode;owner?:ReactNode};
function validSession(value:Session){
 return value&&typeof value.access_token==='string'&&/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(value.access_token)
  &&value.access_token.length<=8192&&value.expires_in===900&&typeof value.user_id==='string'
  &&/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value.user_id);
}
export default function AuthShell({Pages,directory,owner}:Props){
 const [path,setPath]=useState(window.location.pathname),[session,setSession]=useState<Session|null>(null);
 const [error,setError]=useState(''),[busy,setBusy]=useState(false);
 const currentPath=useRef(path),epoch=useRef(0),mounted=useRef(true),logout=useRef<AbortController|null>(null),pending=useRef(false);
 const liveSession=useRef<Session|null>(null);
 const version=epoch.current;
 function navigate(next:string){currentPath.current=next;epoch.current++;window.history.pushState(null,'',next);setPath(next);setError('');}
 useEffect(()=>{
  mounted.current=true;
  const pop=()=>{currentPath.current=window.location.pathname;epoch.current++;setPath(currentPath.current);setError('');};
  window.addEventListener('popstate',pop);
  return()=>{mounted.current=false;logout.current?.abort();window.removeEventListener('popstate',pop);};
 },[]);
 function accept(value:Session){
  if(!mounted.current||version!==epoch.current||currentPath.current!==path||!['/registro','/ingresar'].includes(path))return;
  if(!validSession(value)){setError('Respuesta de sesión inválida');return;}
  const next={access_token:value.access_token,expires_in:value.expires_in,user_id:value.user_id};
  liveSession.current=next;setSession(next);navigate('/mi-perfil');
 }
 async function endSession(){
  if(!session||pending.current)return;
  pending.current=true;setBusy(true);setError('');const sent=session,token=sent.access_token;const controller=new AbortController();logout.current=controller;
  try{
   const response=await fetch('/api/auth/logout',{method:'POST',headers:{'content-type':'application/json',authorization:'Bearer '+token},
    credentials:'include',body:'{}',signal:controller.signal});
   if(![204,401].includes(response.status))throw new Error('logout failed');
   if(mounted.current&&!controller.signal.aborted&&liveSession.current===sent){liveSession.current=null;setSession(null);navigate('/ingresar');}
  }catch{
   if(mounted.current&&!controller.signal.aborted&&liveSession.current===sent)setError('No pudimos cerrar la sesión. Podés reintentar.');
  }finally{pending.current=false;if(mounted.current){setBusy(false);logout.current=null;}}
 }
 const auth=path==='/registro'||path==='/ingresar';
 return <><nav aria-label="Navegación principal"><a href="/" onClick={e=>{e.preventDefault();navigate('/');}}>Directorio</a>{' '}
  {session?<><a href="/mi-perfil" onClick={e=>{e.preventDefault();navigate('/mi-perfil');}}>Mi perfil</a>{' '}<button disabled={busy} onClick={endSession}>Cerrar sesión</button></>:
   <><a href="/registro" onClick={e=>{e.preventDefault();navigate('/registro');}}>Registrarse</a>{' '}<a href="/ingresar" onClick={e=>{e.preventDefault();navigate('/ingresar');}}>Ingresar</a></>}
 </nav>{error&&<p role="alert">{error}</p>}{session&&<p role="status">Sesión iniciada</p>}
 {auth?(window.location.protocol==='https:'?<Pages key={path} mode={path==='/registro'?'register':'login'} onSession={accept}/>:<p role="alert">El acceso a tu cuenta requiere HTTPS.</p>):
  path==='/mi-perfil'?(session?(owner??<p>La gestión del perfil estará disponible en el siguiente incremento.</p>):<p>Ingresá para gestionar tu perfil.</p>):directory}
 </>;
}
