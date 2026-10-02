// Frozen acceptance harness, excluded from the production entry point.
import {useState} from 'react';
import {createRoot} from 'react-dom/client';
import PublicProfile from './PublicProfile';
function Fixture(){
  const [id,setId]=useState('20000000-0000-4000-8000-000000000001');
  return <><button onClick={()=>setId('20000000-0000-4000-8000-000000000002')}>Mostrar otro perfil</button><PublicProfile profileId={id}/></>;
}
createRoot(document.getElementById('root')!).render(<Fixture/>);
