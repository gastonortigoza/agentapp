// Product entry: intentionally unresolved until original reviewed modules arrive.
import {createRoot} from 'react-dom/client';
import './style.css';
import AuthPages from './AuthPages';
import Directory from './App';
import AuthShell from './AuthShell';
createRoot(document.getElementById('root')!).render(<AuthShell Pages={AuthPages} directory={<Directory/>}/>);
