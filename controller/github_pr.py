"""PR borrador revisado con intención durable y reconciliación sin reenvío.

API del controlador de confianza. No expone comandos ni aprobaciones al modelo.
Un resultado ausente tras un timeout no prueba que el POST no pueda llegar tarde.
"""
import getpass
import json
import os
import re
import subprocess
from urllib.parse import quote, urlencode

import controller_gate
import github_app_auth
import github_connection as connection
import github_publish as branches
from publication_journal import Journal
from worker_lock import worker_lock


class PRJournal(Journal):
    def resolve(self, decision, evidence, actor, version=None):
        with self.transaction() as db:
            row=self._row(db,version)
            if row['state'] not in {'in_flight','uncertain'}:
                raise ValueError('PR no reconciliable')
            intent=json.loads(row['intent'])
            if decision=='confirm-not-applied':
                raise ValueError('GitHub no demuestra que el POST no pueda completarse tarde; conservar uncertain')
            if decision=='confirm-applied':
                for name in ('repository','branch','commit','base','base_sha','marker'):
                    if evidence.get(name)!=intent[name]:raise ValueError('Evidencia de otro PR o revisión')
                number=evidence.get('number')
                if type(number) is not int or number<1 or evidence.get('url')!=f"https://github.com/{intent['repository']}/pull/{number}":
                    raise ValueError('Identidad remota inválida')
                target='confirmed'
            elif decision=='defer':
                evidence={}
                target='uncertain'
            else:raise ValueError('Resolución inválida')
            db.execute('UPDATE publication SET state=? WHERE id=1',(target,))
            self._event(db,row,decision,actor,evidence)


class GitHubPR:
    def __init__(self,repository):
        if repository!=connection.configured_repo():raise ValueError('Repositorio fuera del alcance')
        self.repository=connection.repo_name(repository)

    def _request(self,method,suffix,payload=None):
        # suffix is constructed by the methods below, never from remote content.
        if self.repository!=connection.configured_repo():raise ValueError('Cambió el repositorio autorizado')
        if method=='POST' and (suffix!='pulls' or branches.enabled()!=self.repository):
            raise ValueError('Escritura fuera del alcance')
        command=[str(connection.GH),'api','--hostname','github.com','--method',method,
                 '-H','Accept: application/vnd.github+json',
                 f'repos/{self.repository}/{suffix}']
        if payload is not None:command+=['--input','-']
        proc=subprocess.run(command,input=json.dumps(payload) if payload is not None else None,
                            capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45,
                            env=github_app_auth.environment(self.repository))
        if proc.returncode:raise RuntimeError('GitHub no confirmó la solicitud; consultar y reconciliar')
        if len(proc.stdout)>2_000_000:raise ValueError('Respuesta demasiado grande')
        return json.loads(proc.stdout)

    def branch_sha(self,branch):
        # Encoding prevents a branch from adding a query or another endpoint.
        if not isinstance(branch,str) or not 1<=len(branch)<=200:raise ValueError('Rama inválida')
        row=self._request('GET','git/ref/heads/'+quote(branch,safe=''))
        sha=row.get('object',{}).get('sha')
        if row.get('ref')!='refs/heads/'+branch or row.get('object',{}).get('type')!='commit' or not isinstance(sha,str) or not re.fullmatch('[0-9a-f]{40}',sha):
            raise ValueError('Referencia remota inválida')
        return sha

    def candidates(self,intent):
        rows=[]
        for page in range(1,11):
            query=urlencode({'state':'all','head':self.repository.split('/')[0]+':'+intent['branch'],
                             'base':intent['base'],'per_page':100,'page':page})
            batch=self._request('GET','pulls?'+query)
            if not isinstance(batch,list):raise ValueError('Lista de PR inválida')
            rows.extend(batch)
            if len(batch)<100:return rows
        raise ValueError('Consulta de PR incompleta; no se infiere ausencia')

    def create(self,intent):
        return self._request('POST','pulls',{'title':intent['title'],'body':intent['body'],
                            'head':intent['branch'],'base':intent['base'],'draft':True,
                            'maintainer_can_modify':False})


def read_manifest(job_id):
    job=branches.job_path(job_id)
    manifest=json.loads((job/'manifest.json').read_text(encoding='utf-8'))
    if manifest['job_id']!=job_id or manifest['repository']!=connection.configured_repo():
        raise ValueError('Trabajo de otro repositorio o identidad')
    branches.branch_name(manifest['branch'])
    if manifest.get('new_repository') or not manifest.get('base_sha') or manifest['base']==manifest['branch']:
        raise ValueError('Un PR necesita una rama base inicial distinta ya publicada')
    receipt=branches.publication_status(job_id)
    if receipt['state']!='confirmed' or receipt['intent']['review_digest']!=branches.digest(manifest):
        raise ValueError('Falta confirmar la publicación exacta de la rama')
    return job,manifest


def prepare(job_id,title,summary):
    controller_gate.require_green()
    if not isinstance(title,str) or not title.strip() or len(title)>180:raise ValueError('Título inválido')
    if not isinstance(summary,str) or not summary.strip() or len(summary)>12000 or '<!-- agent-operation:' in summary:
        raise ValueError('Descripción inválida')
    job,manifest=read_manifest(job_id)
    # Deterministic identity: calling prepare twice cannot generate another PR ID.
    marker='<!-- agent-operation:pr-'+job_id+' -->'
    intent={key:manifest[key] for key in ('repository','branch','commit','base','base_sha')}
    intent.update(operation_id='pr-'+job_id,job_id=job_id,marker=marker,title=title.strip(),
                  body=summary.rstrip()+'\n\n'+marker,
                  branch_review_digest=branches.digest(manifest),scope='manual_reviewed_draft')
    journal=PRJournal(job/'pull-request.sqlite')
    journal.prepare(intent)
    return {'job_id':job_id,'review_digest':branches.digest(intent),'intent':intent,
            'state':journal.status()['state']}


def match(row,intent):
    if not isinstance(row,dict):raise ValueError('Respuesta de PR inválida')
    head=row.get('head') or {};base=row.get('base') or {}
    if any((part.get('repo') or {}).get('full_name')!=intent['repository'] for part in (head,base)):
        return None
    if (head.get('ref')!=intent['branch'] or head.get('sha')!=intent['commit'] or
        base.get('ref')!=intent['base'] or base.get('sha')!=intent['base_sha']):return None
    body=row.get('body')
    if not isinstance(body,str) or body.splitlines().count(intent['marker'])!=1:return None
    number=row.get('number')
    if type(number) is not int or number<1 or row.get('html_url')!=f"https://github.com/{intent['repository']}/pull/{number}":return None
    return {**{key:intent[key] for key in ('repository','branch','commit','base','base_sha','marker')},
            'number':number,'url':row['html_url']}


def observed(adapter,intent):
    candidates=adapter.candidates(intent)
    if not isinstance(candidates,list):raise ValueError('Consulta incompleta')
    matches=[evidence for row in candidates if (evidence:=match(row,intent)) is not None]
    if len(matches)>1:raise ValueError('Más de un PR coincide; resolver ambigüedad fuera de la repetición')
    return candidates,matches[0] if matches else None


def status(job_id):
    job=branches.job_path(job_id)
    if not (job/'pull-request.sqlite').exists():return {'job_id':job_id,'state':'not_prepared'}
    record=PRJournal(job/'pull-request.sqlite').status()
    record.update(job_id=job_id,service='github',operation='create_draft_pr',
                  next_action='Consultar y reconciliar; nunca repetir el POST incierto'
                  if record['state'] in {'uncertain','in_flight'} else None)
    return record


def open_pr(job_id,approved_digest):
    job=branches.job_path(job_id)
    if not (job/'pull-request.sqlite').exists():raise ValueError('Preparar el PR antes de abrirlo')
    with worker_lock(job/'pull-request.sqlite','pr-'+job_id) as acquired:
        if not acquired:raise ValueError('Otro proceso opera este PR')
        journal=PRJournal(job/'pull-request.sqlite');record=journal.status();intent=record['intent']
        if not approved_digest or approved_digest!=branches.digest(intent):raise ValueError('Falta aprobar el PR exacto')
        if branches.enabled()!=intent['repository']:raise ValueError('Repositorio fuera del alcance')
        controller_gate.require_green()
        if record['state']=='confirmed':
            return next(event['evidence'] for event in reversed(record['events']) if event['kind']=='confirm-applied')
        journal.uncertain()
        adapter=GitHubPR(intent['repository'])
        candidates,evidence=observed(adapter,intent)
        if record['attempts']:
            if evidence:
                journal.resolve('confirm-applied',evidence,'github-reconciliation')
                return evidence
            raise ValueError('PR incierto: la ausencia no autoriza otro POST')
        if candidates:raise ValueError('Ya existe un PR candidato; no se crea otro')
        _,manifest=read_manifest(job_id)
        if branches.digest(manifest)!=intent['branch_review_digest']:raise ValueError('Cambió el artefacto aprobado')
        for name,sha in ((intent['branch'],intent['commit']),(intent['base'],intent['base_sha'])):
            if adapter.branch_sha(name)!=sha:raise ValueError('Cambió una rama; preparar otra revisión')
        journal.claim(max_attempts=1)
        try:
            response=adapter.create(intent)
            evidence=match(response,intent)
            if evidence is None:raise ValueError('Respuesta no corresponde al PR solicitado')
            journal.resolve('confirm-applied',evidence,'github-adapter')
            return evidence
        except Exception:
            journal.uncertain()
            raise


def control(job_id,action,version):
    job=branches.job_path(job_id)
    if not (job/'pull-request.sqlite').exists():raise ValueError('PR no preparado')
    journal=PRJournal(job/'pull-request.sqlite')
    actor='local:'+getpass.getuser()
    if action in {'pause','resume'}:
        journal.pause(action=='pause',version,actor)
    else:
        with worker_lock(job/'pull-request.sqlite','pr-'+job_id) as acquired:
            if not acquired:raise ValueError('Otro proceso opera este PR')
            record=journal.status();intent=record['intent']
            if record['version']!=version:raise ValueError('Versión obsoleta')
            evidence={}
            if action=='confirm-applied':
                _,evidence=observed(GitHubPR(intent['repository']),intent)
                if evidence is None:raise ValueError('No se encontró evidencia exacta del PR')
            journal.resolve(action,evidence,actor,version)
    return status(job_id)
