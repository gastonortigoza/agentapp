"""Publicación revisada: snapshot local, hash de aprobación y rama nueva."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import uuid
import getpass
from pathlib import Path,PurePosixPath
import github_connection as connection
from git_control import safe_name,secret_gate
from publication_journal import Journal
from worker_lock import worker_lock
import controller_gate
import publication_policy
import github_app_auth

ROOT=Path(__file__).resolve().parent
JOBS=Path('E:/IA/workspace/github-publish')

def branch_name(branch):
    if not re.fullmatch(r'crewai/[a-z0-9][a-z0-9-]{0,70}',branch):
        raise ValueError('La rama debe ser nueva y tener formato crewai/nombre')
    return branch

def run_git(path,*args):
    auth_env=(github_app_auth.environment(connection.configured_repo())
              if github_app_auth.CONFIG.exists() and args and args[0] in {'push','fetch','ls-remote'}
              else dict(os.environ))
    env={**auth_env,'GIT_TERMINAL_PROMPT':'0','GIT_CONFIG_GLOBAL':os.devnull,'GIT_CONFIG_NOSYSTEM':'1'}
    helper='!'+connection.GH.as_posix()+' auth git-credential'
    command=['git','-c','credential.helper=','-c','credential.https://github.com.helper='+helper,
             '-c','core.hooksPath='+str(ROOT/'.state/empty-hooks'),'-c','commit.gpgsign=false','-C',str(path),*args]
    r=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120,env=env)
    if r.returncode:raise RuntimeError('Git no confirmó la operación; revisar estado antes de reintentar')
    return r.stdout.strip()

def enabled():
    cfg=json.loads(connection.CONFIG.read_text(encoding='utf-8'))
    if cfg.get('mode')!='reviewed_publish' or cfg.get('remote_writes') is not True:
        raise ValueError('Publicación no habilitada')
    return connection.configured_repo()

def digest(manifest):
    return hashlib.sha256(json.dumps(manifest,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def validated_files(source,names):
    publication_policy.check_paths(names)
    source=Path(source).resolve(strict=True)
    if source!=ROOT and not source.is_relative_to(Path('E:/IA/workspace').resolve()):
        raise ValueError('Origen fuera del proyecto y workspace autorizados')
    if not names or len(names)>150:raise ValueError('Seleccioná entre 1 y 150 archivos')
    result=[]
    for name in names:
        p=PurePosixPath(name)
        if not safe_name(name) or '\\' in name or any(x.startswith('.') and x not in {'.gitignore','.env.example'} for x in p.parts):
            raise ValueError('Archivo no publicable: '+name)
        target=(source/name).resolve(strict=True)
        if not target.is_relative_to(source) or not target.is_file() or (source/name).is_symlink():raise ValueError('Ruta inválida')
        data=target.read_bytes()
        if len(data)>2_000_000 or b'\0' in data:raise ValueError('Sólo texto de hasta 2 MB por archivo')
        data.decode('utf-8')
        publication_policy.check_trusted_tests(name,data)
        result.append((name,data))
    if len({n for n,d in result})!=len(result):raise ValueError('Archivos duplicados')
    return result

def prepare(source,names,branch):
    repo=enabled();branch=branch_name(branch)
    selected=validated_files(source,names)
    job_id=uuid.uuid4().hex; job=JOBS/job_id; checkout=job/'checkout'; checkout.mkdir(parents=True)
    url='https://github.com/'+repo+'.git'
    run_git(checkout,'init','-b',branch)
    refs=run_git(checkout,'ls-remote','--heads',url)
    if any(line.endswith('\trefs/heads/'+branch) for line in refs.splitlines()):raise ValueError('La rama ya existe; elegí una nueva')
    metadata=connection.api('repos/'+repo)
    base=metadata['default_branch'];base_sha=None
    if refs:
        run_git(checkout,'fetch','--depth=1',url,'refs/heads/'+base)
        base_sha=run_git(checkout,'rev-parse','FETCH_HEAD')
        run_git(checkout,'checkout','-B',branch,'FETCH_HEAD')
    for name,data in selected:
        target=checkout/name
        # No se escribe a través de symlinks provenientes del repositorio remoto.
        if not target.resolve().is_relative_to(checkout.resolve()) or any(p.is_symlink() for p in [target,*target.parents]):raise ValueError('Symlink en destino')
        target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
    run_git(checkout,'add','--',*[name for name,data in selected])
    secret_gate(checkout)
    diff=run_git(checkout,'diff','--cached','--no-ext-diff','--no-textconv')
    if not diff:raise ValueError('No hay cambios para publicar')
    (job/'changes.diff').write_text(diff,encoding='utf-8')
    run_git(checkout,'-c','user.name=Local CrewAI','-c','user.email=crewai@localhost','commit','-m','Propuesta preparada por CrewAI')
    commit=run_git(checkout,'rev-parse','HEAD')
    manifest={'job_id':job_id,'repository':repo,'branch':branch,'base':base,'base_sha':base_sha,'commit':commit,
              'files':[{'path':n,'sha256':hashlib.sha256(d).hexdigest()} for n,d in selected],
              'new_repository':not bool(refs),'note':'Si el repositorio está vacío, esta primera rama publicada será la rama inicial. No se crea ni modifica main.' if not refs else 'Publicación en rama nueva sin modificar la rama base.'}
    (job/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'job_id':job_id,'review_digest':digest(manifest),'manifest':manifest,'diff':str(job/'changes.diff')}

def job_path(job_id):
    if not re.fullmatch('[0-9a-f]{32}',job_id):raise ValueError('ID inválido')
    job=JOBS/job_id
    if not job.is_dir() or not job.resolve().is_relative_to(JOBS.resolve()):
        raise ValueError('Trabajo inexistente o fuera del workspace')
    return job


def observation(checkout,intent):
    url='https://github.com/'+intent['repository']+'.git'
    lines=run_git(checkout,'ls-remote','--heads',url,'refs/heads/'+intent['branch']).splitlines()
    sha=None
    if lines:
        expected='refs/heads/'+intent['branch']
        if len(lines)!=1 or len(lines[0].split())!=2 or lines[0].split()[1]!=expected:
            raise ValueError('Respuesta remota ambigua')
        sha=lines[0].split()[0]
        if not re.fullmatch('[0-9a-f]{40}',sha):raise ValueError('SHA remoto inválido')
    return {'repository':intent['repository'],'branch':intent['branch'],'sha':sha,
            'retry_safety':'git_create_only_lease'}


def publication_status(job_id):
    job=job_path(job_id)
    if not (job/'publication.sqlite').exists():
        return {'job_id':job_id,'state':'not_started','attempts':0}
    result=Journal(job/'publication.sqlite').status()
    result.update(job_id=job_id,service='github',environment='repository',
                  operation='create_branch',operation_id=job_id,
                  next_action='Consultar remoto y resolver; no repetir a ciegas'
                  if result['state'] in {'uncertain','in_flight'} else
                  'Reanudar explícitamente' if result['paused'] else None)
    return result


def publication_control(job_id,action,version):
    job=job_path(job_id)
    if not (job/'publication.sqlite').exists():raise ValueError('Publicación no iniciada')
    journal=Journal(job/'publication.sqlite')
    actor='windows:'+getpass.getuser() if os.name=='nt' else 'local:'+getpass.getuser()
    if action in {'pause','resume'}:
        journal.pause(action=='pause',version,actor)
    else:
        with worker_lock(job/'publication.sqlite',job_id) as acquired:
            if not acquired:raise ValueError('Publicador activo; esperar antes de reconciliar')
            record=journal.status()
            if record['version']!=version:raise ValueError('Versión obsoleta')
            intent=record['intent']
            if intent['repository']!=connection.configured_repo():raise ValueError('Repositorio configurado diferente')
            evidence={'repository':intent['repository'],'branch':intent['branch']}
            if action!='defer':evidence=observation(job/'checkout',intent)
            journal.resolve(action,evidence,actor,version)
    return publication_status(job_id)


def publish(job_id,approved_digest):
    job=job_path(job_id)
    with worker_lock(job/'publication.sqlite',job_id) as acquired:
        if not acquired:raise ValueError('Otro proceso está publicando este trabajo')
        return publish_owned(job_id,approved_digest)


def publish_owned(job_id,approved_digest):
    repo=enabled()
    job=job_path(job_id);checkout=job/'checkout'
    manifest=json.loads((job/'manifest.json').read_text(encoding='utf-8'))
    if not approved_digest or digest(manifest)!=approved_digest:raise ValueError('Falta aprobar el manifiesto exacto')
    if manifest['repository']!=repo:raise ValueError('El repositorio configurado cambió')
    publication_policy.check_paths([item['path'] for item in manifest['files']])
    branch=branch_name(manifest['branch'])
    if run_git(checkout,'rev-parse','HEAD')!=manifest['commit'] or run_git(checkout,'status','--porcelain'):
        raise ValueError('El snapshot cambió después de la revisión')
    controller_gate.require_green()
    journal=Journal(job/'publication.sqlite')
    journal.prepare({'job_id':job_id,'repository':repo,'branch':branch,
                     'commit':manifest['commit'],'review_digest':approved_digest})
    record=journal.status()
    url='https://github.com/'+repo+'.git'
    if record['state']=='confirmed':
        return publication_result(repo,branch,manifest['commit'])
    # Al reiniciar, una llamada despachada jamás vuelve sola a prepared.
    journal.uncertain()
    remote=observation(checkout,record['intent'])
    if remote['sha']:
        if record['attempts'] and remote['sha']==manifest['commit']:
            journal.resolve('confirm-applied',remote,'git-reconciliation')
            return publication_result(repo,branch,remote['sha'])
        raise ValueError('La rama ya existe con identidad ajena; no se sobrescribe')
    if record['state'] in {'in_flight','uncertain'}:
        raise ValueError('Resultado incierto; resolver antes de repetir')
    # Lease vacío: sólo crea una referencia inexistente, incluso ante una carrera.
    journal.claim()
    try:
        run_git(checkout,'push','--force-with-lease=refs/heads/'+branch+':',url,manifest['commit']+':refs/heads/'+branch)
        remote=observation(checkout,record['intent'])
        journal.resolve('confirm-applied',remote,'git-adapter')
    except Exception:
        journal.uncertain()
        raise
    return publication_result(repo,branch,manifest['commit'])


def publication_result(repo,branch,commit):
    # El recibo durable es SQLite, no un JSON escrito después del efecto.
    result={'repository':repo,'branch':branch,'commit':commit,'url':'https://github.com/'+repo+'/tree/'+branch}
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True)
    prep=sub.add_parser('prepare');prep.add_argument('--source',default=str(ROOT));prep.add_argument('--branch',required=True);prep.add_argument('files',nargs='+')
    pub=sub.add_parser('publish');pub.add_argument('job_id');pub.add_argument('--approved-digest',required=True)
    a=p.parse_args();result=prepare(a.source,a.files,a.branch) if a.action=='prepare' else publish(a.job_id,a.approved_digest)
    print(json.dumps(result,ensure_ascii=False,indent=2))
