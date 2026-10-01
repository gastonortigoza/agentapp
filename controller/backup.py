"""Backup local cifrado con Windows DPAPI y SQLite backup consistente."""
import ctypes
import hashlib
import io
import json
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from ctypes import wintypes
from datetime import datetime,timezone
from pathlib import Path

BASE=Path('E:/IA')
class Blob(ctypes.Structure):
    _fields_=[('cbData',wintypes.DWORD),('pbData',ctypes.POINTER(ctypes.c_ubyte))]

def crypt(data,decrypt=False):
    buffer=ctypes.create_string_buffer(data)
    source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte))); target=Blob()
    dll=ctypes.WinDLL('crypt32',use_last_error=True)
    func=dll.CryptUnprotectData if decrypt else dll.CryptProtectData
    func.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    func.restype=wintypes.BOOL
    if not func(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)): raise ctypes.WinError(ctypes.get_last_error())
    try: return ctypes.string_at(target.pbData,target.cbData)
    finally:
        free=ctypes.WinDLL('kernel32').LocalFree
        free.argtypes=[ctypes.c_void_p]; free.restype=ctypes.c_void_p
        free(target.pbData)

def create():
    memory=io.BytesIO(); manifest=[]
    excludes={'.venv','.git','.state','__pycache__','.pytest_cache','cache','node_modules'}
    # El archivo completo se cifra: la base de WebUI contiene información privada.
    with zipfile.ZipFile(memory,'w',zipfile.ZIP_DEFLATED) as z:
        for folder in ['projects','knowledge','open-webui/data','config','logs']:
            root=BASE/folder
            if not root.exists(): continue
            for p in root.rglob('*'):
                if not p.is_file() or p.is_symlink() or excludes.intersection(p.relative_to(root).parts): continue
                if p.name.endswith(('-wal','-shm')) or p.stat().st_size>30_000_000: continue
                if p.suffix=='.db':
                    with tempfile.TemporaryDirectory(prefix='local-ai-backup-') as temp:
                        snap=Path(temp)/'consistent.db'
                        with closing(sqlite3.connect('file:'+p.as_posix()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(snap)) as dst:
                            src.backup(dst)
                            if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise RuntimeError('SQLite inválida')
                        data=snap.read_bytes()
                else: data=p.read_bytes()
                name=p.relative_to(BASE).as_posix()
                z.writestr(name,data); manifest.append({'path':name,'sha256':hashlib.sha256(data).hexdigest()})
        z.writestr('backup-manifest.json',json.dumps(manifest,indent=2))
    encrypted=crypt(memory.getvalue())
    target=BASE/'backups'/('local-ai-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.zip.dpapi')
    target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(encrypted)
    # Prueba de restauración en memoria: descifrado + checksum de cada archivo.
    with zipfile.ZipFile(io.BytesIO(crypt(target.read_bytes(),True))) as restored:
        if restored.testzip() is not None: raise RuntimeError('ZIP corrupto')
        for entry in manifest:
            if hashlib.sha256(restored.read(entry['path'])).hexdigest()!=entry['sha256']: raise RuntimeError('Checksum inválido')
    report={'timestamp':datetime.now(timezone.utc).isoformat(),'path':str(target),'files':len(manifest),'restore_check':'pass','encryption':'Windows DPAPI CurrentUser','limitation':'Requiere la misma cuenta Windows y sus claves DPAPI. Copia local; no protege contra pérdida del disco. Excluye entornos, cachés y archivos mayores a 30 MB.'}
    (BASE/'logs'/'backup-latest.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report

if __name__=='__main__': print(json.dumps(create(),indent=2))
