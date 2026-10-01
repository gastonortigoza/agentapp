"""Smoke tests acumulativos por etapa; ninguna etapa pendiente se declara aprobada."""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime,timezone
from pathlib import Path
from contextlib import closing
from urllib.request import urlopen
from rag import api,client,COLLECTION

ROOT=Path(__file__).resolve().parent
LOG=Path('E:/IA/logs')

def read_url(url):
    with urlopen(url,timeout=15) as r:return json.load(r)

def command(args):
    r=subprocess.run(args,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=900,env={**os.environ,'PYTHONIOENCODING':'utf-8'})
    if r.returncode: raise RuntimeError('Comando falló: '+str(args[1:])+' / '+r.stderr[-500:])
    return r.stdout[-500:]

def check(stage):
    rows=[]
    def step(name,fn):
        try:detail=fn(); rows.append({'stage':name,'status':'pass','detail':detail})
        except Exception as exc:rows.append({'stage':name,'status':'fail','detail':str(exc)[:700]})
    def a():
        r=api('/api/generate',{'model':'qwen3.8:27b-q4_K_M','prompt':'Respondé exactamente OK','think':False,'stream':False,'options':{'num_ctx':32768,'num_predict':16}})
        assert r['response'].strip()=='OK'
        loaded=read_url('http://127.0.0.1:11434/api/ps')['models']
        assert any(m['name']=='qwen3.8:27b-q4_K_M' and m['size_vram']==m['size'] for m in loaded)
        return 'Respuesta real y modelo 100% GPU'
    def b():
        assert read_url('http://127.0.0.1:8080/health')['status'] is True
        with closing(sqlite3.connect('file:E:/IA/open-webui/data/webui.db?mode=ro',uri=True)) as db:
            assert db.execute('pragma integrity_check').fetchone()[0]=='ok'
            assert db.execute('select count(*) from user').fetchone()[0]>=1
        return 'UI responde; base persistente íntegra y cuenta conservada'
    def c():
        return command([sys.executable,'lab.py'])
    def d():
        command([sys.executable,'-m','pytest','-q'])
        command([sys.executable,'flow.py'])
        return 'Flow real y controles automatizados aprobados'
    def e():
        from executor import WORKSPACE,run_tests,git_read
        root=WORKSPACE/('smoke-'+uuid.uuid4().hex); root.mkdir()
        (root/'solution.py').write_text('value=7\n')
        (root/'test_solution.py').write_text('import unittest\nfrom solution import value\nclass T(unittest.TestCase):\n def test_value(self):self.assertEqual(value,7)\n')
        assert run_tests(root)['exit_code']==0
        (root/'solution.py').write_text('while True: pass\n')
        assert run_tests(root,timeout=3)['timed_out']
        repo=WORKSPACE/('git-smoke-'+uuid.uuid4().hex); repo.mkdir()
        command(['git','init',str(repo)])
        assert git_read(repo,'status')['exit_code']==0
        return 'Prueba real, timeout, workspace y Git read correctos'
    def f():
        assert client().count(COLLECTION).count==20
        from rag import evaluate,search
        metrics=evaluate();assert metrics['holdout']['pass']
        assert search('¿Qué es smishing?')[0]['doc_id']=='canales'
        return metrics
    def g():return command([sys.executable,'check_mcp.py'])
    def h():
        report=json.loads((LOG/'osint-latency.json').read_text(encoding='utf-8'))
        assert report['completed_runs']>=20 and report['pass_count']==report['completed_runs']
        return {'runs':report['completed_runs'],'scope':report['scope'],'note':'Evidencia de ejecuciones completas; no repite la carga de veinte ejecuciones.'}
    for name,fn in zip('ABCDEFGH',[a,b,c,d,e,f,g,h]):
        if name>stage:break
        step(name,fn)
        if rows[-1]['status']=='fail':break
    result={'timestamp':datetime.now(timezone.utc).isoformat(),'requested_stage':stage,'status':'pass' if len(rows)==ord(stage)-64 and all(r['status']=='pass' for r in rows) else 'fail','checks':rows}
    (LOG/('smoke-'+stage+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--stage',choices=list('ABCDEFGH'),default='A');a=p.parse_args()
    result=check(a.stage);print(json.dumps(result,ensure_ascii=False,indent=2));raise SystemExit(0 if result['status']=='pass' else 1)
