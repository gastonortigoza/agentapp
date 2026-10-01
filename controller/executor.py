"""Executor de pruebas aisladas; nunca ejecuta código generado en Windows."""
import json
import subprocess
import time
import uuid
import threading
from pathlib import Path

WORKSPACE=Path('E:/IA/workspace').resolve()
IMAGE='python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea'
MAX_INPUT_BYTES=1024*1024
TEST_ARGV=['python','-I','-c',"import sys,unittest; sys.path.insert(0,'/work'); s=unittest.defaultTestLoader.discover('/work',pattern='test_solution.py'); r=unittest.TextTestRunner(verbosity=2).run(s); sys.exit(0 if r.wasSuccessful() and r.testsRun>0 else 1)"]

def within_workspace(path):
    path=Path(path).resolve(strict=True)
    if not path.is_relative_to(WORKSPACE) or path==WORKSPACE:
        raise ValueError('Ruta fuera del workspace o raíz completa')
    return path

def run_tests(path,timeout=120):
    path=within_workspace(path)
    if not 1<=timeout<=120: raise ValueError('Timeout debe estar entre 1 y 120 segundos')
    files={p.name for p in path.iterdir()}
    if files!={'solution.py','test_solution.py'} or any(p.is_symlink() or not p.is_file() for p in path.iterdir()):
        raise ValueError('La entrada del sandbox debe contener sólo solution.py y test_solution.py')
    if sum(p.stat().st_size for p in path.iterdir())>MAX_INPUT_BYTES:
        raise ValueError('Entrada del sandbox supera 1 MiB')
    name='local-ai-test-'+uuid.uuid4().hex
    try:
        health=subprocess.run(['docker','info','--format','{{.ServerVersion}}'],capture_output=True,text=True,timeout=10)
        if health.returncode!=0: raise RuntimeError('Docker no está disponible')
    except (OSError,subprocess.TimeoutExpired,RuntimeError) as exc:
        return dict(command=['docker','info'],exit_code=125,stdout='',stderr=str(exc),timed_out=False,duration_ms=0,status='infrastructure_error')
    command=['docker','run','--pull','never','--rm','--name',name,'--network','none','--read-only',
             '--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','64',
             '--memory','512m','--memory-swap','512m','--cpus','1','--user','65534:65534',
             '--shm-size','16m','--log-driver','none','--ulimit','fsize=16777216:16777216',
             '--mount',f'type=bind,source={path},target=/work,readonly',
             '--workdir','/work','--env','PYTHONDONTWRITEBYTECODE=1',IMAGE,
             *TEST_ARGV]
    started=time.monotonic()
    try:
        proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        captured={'stdout':bytearray(),'stderr':bytearray()}
        def drain(pipe,key):
            while True:
                chunk=pipe.read(4096)
                if not chunk: break
                captured[key].extend(chunk)
                del captured[key][:-24000]
        threads=[threading.Thread(target=drain,args=(proc.stdout,'stdout'),daemon=True),threading.Thread(target=drain,args=(proc.stderr,'stderr'),daemon=True)]
        for t in threads: t.start()
        code=proc.wait(timeout=timeout)
        for t in threads: t.join(timeout=2)
        result=dict(command=command,exit_code=code,stdout=captured['stdout'].decode('utf-8','replace'),stderr=captured['stderr'].decode('utf-8','replace'),timed_out=False)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
        # Sólo se detiene el contenedor creado para esta llamada, por nombre único.
        try: subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=10)
        except (OSError,subprocess.TimeoutExpired): pass
        result=dict(command=command,exit_code=124,stdout='',stderr='Tiempo máximo excedido',timed_out=True)
    result['duration_ms']=round((time.monotonic()-started)*1000)
    # Un error de Docker no se presenta como un defecto del código.
    result['status']='pass' if result['exit_code']==0 else ('infrastructure_error' if result['exit_code'] in (125,126,127) else 'fail')
    return result

def git_read(path,action):
    path=within_workspace(path)
    allowed={'status':['status','--short'],'diff':['diff','--no-ext-diff','--no-textconv'],
             'log':['log','-5','--oneline'],'branch':['branch','--list']}
    if action not in allowed: raise ValueError('Comando Git no permitido')
    # No se aceptan argumentos libres ni un shell.
    r=subprocess.run(['git','-C',str(path),*allowed[action]],capture_output=True,text=True,timeout=15)
    return {'exit_code':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
