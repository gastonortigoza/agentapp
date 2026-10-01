"""Git local: rutas explícitas, revisión del índice y control de secretos."""
import hashlib
import re
import subprocess
from pathlib import Path

PROJECT=Path(__file__).resolve().parent
WORKSPACE=Path('E:/IA/workspace').resolve()
DENIED={'.env','secrets','credentials','.venv','.state','runs','__pycache__'}

def repo_path(path):
    path=Path(path).resolve(strict=True)
    if path!=PROJECT and (path==WORKSPACE or not path.is_relative_to(WORKSPACE)):
        raise ValueError('Repositorio fuera de las rutas permitidas')
    r=subprocess.run(['git','-C',str(path),'rev-parse','--show-toplevel'],capture_output=True,text=True,check=True)
    if Path(r.stdout.strip()).resolve()!=path: raise ValueError('Debe indicarse la raíz del repositorio')
    return path

def git(path,*args):
    return subprocess.run(['git','-C',str(path),*args],capture_output=True,check=True)

def safe_name(name):
    p=Path(name)
    if p.is_absolute() or '..' in p.parts or any(part in DENIED for part in p.parts): return False
    if p.name.startswith('.env') and p.name!='.env.example': return False
    return p.suffix.lower() not in {'.key','.pem','.pfx','.db','.sqlite','.sqlite3','.log','.zip','.dpapi'}

def secret_gate(path):
    path=repo_path(path)
    names=git(path,'diff','--cached','--name-only','-z').stdout.decode().split('\0')
    for name in filter(None,names):
        if not safe_name(name): raise ValueError('Archivo sensible en índice: '+name)
        # Los borrados no tienen blob; no se permiten symlinks en el índice.
        entry=git(path,'ls-files','--stage','--',name).stdout.decode()
        if not entry: continue
        if entry.startswith('120000'): raise ValueError('Symlink en índice')
        data=git(path,'show',':'+name).stdout
        if len(data)>2_000_000 or b'\0' in data: raise ValueError('Binario o archivo demasiado grande: '+name)
        patterns=[rb'-----BEGIN [A-Z ]*PRIVATE KEY-----',rb'(?i)(?:api[_-]?key|password|token|secret)\s*[=:]\s*[\x22\x27][A-Za-z0-9_+/=-]{20,}']
        if any(re.search(p,data) for p in patterns): raise ValueError('Posible secreto en: '+name)
    return hashlib.sha256(git(path,'diff','--cached','--binary','--no-ext-diff','--no-textconv').stdout).hexdigest()

def stage(path,names):
    path=repo_path(path)
    if not names or any(not safe_name(n) for n in names): raise ValueError('Lista de archivos inválida')
    for name in names:
        p=(path/name).resolve(strict=True)
        if not p.is_relative_to(path) or not p.is_file(): raise ValueError('Ruta inválida')
    git(path,'add','--',*names)
    return secret_gate(path)

def commit(path,message,reviewed_digest):
    path=repo_path(path)
    if not message.strip() or len(message)>200: raise ValueError('Mensaje inválido')
    if secret_gate(path)!=reviewed_digest: raise ValueError('El índice cambió después de revisarlo')
    hooks=PROJECT/'.state'/'empty-hooks'
    hooks.mkdir(parents=True,exist_ok=True)
    return git(path,'-c','core.hooksPath='+str(hooks),'-c','commit.gpgsign=false','commit','-m',message).stdout.decode()
