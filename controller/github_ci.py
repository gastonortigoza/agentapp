"""Inspección de CI de push para un commit publicado; nunca autoriza merge.

La política vive en el controlador, fuera del checkout del candidato. Verifica
archivos confiables, run, intento, jobs y emisor de cada check. No ejecuta CI.
"""
import json
from pathlib import Path
import re
from urllib.parse import quote, urlencode

import controller_gate
import github_publish as publish
from github_pr import GitHubPR

POLICY=Path(__file__).resolve().parent/'config/ci-policy.json'


def policy(path=POLICY):
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    if set(data)!={'schema_version','repository','workflow_path','app_id','required_jobs','trusted_files'}:
        raise ValueError('Política CI inválida')
    if data['schema_version']!=1 or data['repository']!=publish.connection.configured_repo():
        raise ValueError('Política de otro repositorio o versión')
    if type(data['app_id']) is not int or data['app_id']<=0:
        raise ValueError('Emisor de checks inválido')
    jobs=data['required_jobs'];files=data['trusted_files']
    if not isinstance(jobs,list) or not jobs or any(not isinstance(n,str) or not n for n in jobs) or len(set(jobs))!=len(jobs):
        raise ValueError('Jobs requeridos inválidos')
    if not isinstance(files,dict) or data['workflow_path'] not in files or 'tests/test_status_summary.py' not in files:
        raise ValueError('Faltan workflow o tests confiables')
    for name,sha in files.items():
        if not isinstance(name,str) or name.startswith('/') or '\\' in name or any(p in {'','..','.'} for p in name.split('/')):
            raise ValueError('Ruta CI inválida')
        if not isinstance(sha,str) or not re.fullmatch('[0-9a-f]{40}',sha):raise ValueError('Identidad de archivo inválida')
    return data


def assess(config,commit,branch,packet):
    """Sólo admite paquetes obtenidos por inspect; no texto o salidas del modelo."""
    if not isinstance(commit,str) or not re.fullmatch('[0-9a-f]{40}',commit):raise ValueError('Commit inválido')
    if not config.get('required_jobs') or not config.get('trusted_files'):raise ValueError('Política vacía')
    run=packet['run'];jobs=packet['jobs'];checks=packet['checks']
    repo=config['repository']
    if packet['files']!=config['trusted_files']:raise ValueError('Workflow o tests cambiaron')
    if packet['current_branch_sha']!=commit:raise ValueError('La rama avanzó después de la publicación')
    if ((run.get('repository') or {}).get('full_name')!=repo or
        (run.get('head_repository') or {}).get('full_name')!=repo or
        run.get('head_sha')!=commit or run.get('head_branch')!=branch or
        run.get('path')!=config['workflow_path'] or run.get('event')!='push'):
        raise ValueError('CI de otra fuente, evento o revisión')
    if run.get('status')!='completed' or run.get('conclusion')!='success':
        raise ValueError('Workflow no completado con éxito')
    for key in ('id','run_attempt','check_suite_id'):
        if type(run.get(key)) is not int or run[key]<=0:raise ValueError('Identidad de ejecución inválida')
    matched=[]
    for name in config['required_jobs']:
        candidates=[job for job in jobs if job.get('name')==name]
        if len(candidates)!=1:raise ValueError('Job requerido ausente o ambiguo: '+name)
        job=candidates[0]
        if (job.get('run_id')!=run['id'] or job.get('run_attempt')!=run['run_attempt'] or
            job.get('head_sha')!=commit or job.get('status')!='completed' or job.get('conclusion')!='success'):
            raise ValueError('Job de otro intento o no exitoso')
        url=job.get('check_run_url')
        check=checks.get(url)
        if not isinstance(check,dict):raise ValueError('Falta check del job')
        check_id=check.get('id')
        if (type(check_id) is not int or check_id<=0 or url!=f'https://api.github.com/repos/{repo}/check-runs/{check_id}' or
            check.get('name')!=name or check.get('head_sha')!=commit or
            (check.get('app') or {}).get('id')!=config['app_id'] or
            (check.get('check_suite') or {}).get('id')!=run['check_suite_id'] or
            check.get('status')!='completed' or check.get('conclusion')!='success'):
            raise ValueError('Check de fuente incorrecta, revisión distinta o no exitoso')
        matched.append({'name':name,'check_id':check_id})
    return {'ci_verified':True,'commit':commit,'workflow_run_id':run['id'],
            'run_attempt':run['run_attempt'],'checks':matched,'merge_authorized':False,
            'scope':'push_ci_exact_commit','limitation':'No comprueba aprobación humana ni reglas de rama'}


def pages(adapter,suffix,key):
    result=[]
    separator='&' if '?' in suffix else '?'
    for page in range(1,11):
        data=adapter._request('GET',suffix+separator+f'per_page=100&page={page}')
        batch=data.get(key)
        if not isinstance(batch,list):raise ValueError('Respuesta CI inválida')
        result.extend(batch)
        if len(batch)<100:return result
    raise ValueError('Consulta CI incompleta')


def inspect(job_id,policy_path=POLICY):
    controller_gate.require_green()
    config=policy(policy_path)
    record=publish.publication_status(job_id)
    if record['state']!='confirmed':raise ValueError('La rama aún no está confirmada')
    intent=record['intent'];commit=intent['commit'];branch=intent['branch']
    if intent['repository']!=config['repository']:raise ValueError('Destino CI fuera de alcance')
    adapter=GitHubPR(config['repository'])
    query=urlencode({'head_sha':commit,'branch':branch,'event':'push'})
    runs=pages(adapter,'actions/runs?'+query,'workflow_runs')
    runs=[run for run in runs if run.get('path')==config['workflow_path']]
    if not runs:raise ValueError('No hay CI del workflow esperado para este commit')
    # Nunca escoger un verde anterior si hay una ejecución posterior fallida.
    latest=max(runs,key=lambda run:run['id'])
    run_id=latest['id']
    if type(run_id) is not int or run_id<=0:raise ValueError('Run inválido')
    run=adapter._request('GET',f'actions/runs/{run_id}')
    attempt=run.get('run_attempt')
    if type(attempt) is not int or attempt<=0:raise ValueError('Intento inválido')
    jobs=pages(adapter,f'actions/runs/{run_id}/attempts/{attempt}/jobs','jobs')
    checks={}
    for job in jobs:
        if job.get('name') not in config['required_jobs']:continue
        url=job.get('check_run_url','')
        prefix=f"https://api.github.com/repos/{config['repository']}/check-runs/"
        if not isinstance(url,str) or not url.startswith(prefix) or not re.fullmatch('[1-9][0-9]*',url[len(prefix):]):
            raise ValueError('URL de check fuera de alcance')
        checks[url]=adapter._request('GET','check-runs/'+url[len(prefix):])
    files={}
    for name in config['trusted_files']:
        data=adapter._request('GET','contents/'+quote(name,safe='/')+'?ref='+commit)
        if data.get('type')!='file' or data.get('path')!=name:raise ValueError('Archivo CI inválido')
        files[name]=data.get('sha')
    # Detectar cambios durante la lectura, incluyendo re-runs.
    fresh=adapter._request('GET',f'actions/runs/{run_id}')
    if any(fresh.get(key)!=run.get(key) for key in ('run_attempt','status','conclusion','head_sha')):
        raise ValueError('CI cambió durante la consulta')
    packet={'run':run,'jobs':jobs,'checks':checks,'files':files,
            'current_branch_sha':adapter.branch_sha(branch)}
    return assess(config,commit,branch,packet)
