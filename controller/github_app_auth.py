"""Identidad publicadora optativa. Clave DPAPI fuera del repo; tokens en memoria.

La provisión es una operación local explícita, nunca una herramienta del modelo.
Una configuración presente pero inválida bloquea: no vuelve a la cuenta humana.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import time
import uuid
from datetime import datetime, timezone

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from backup import crypt
import github_connection as connection

ROOT=Path(__file__).resolve().parent
CONFIG=ROOT/'config/github-app.json'
SECRET_DIR=Path('E:/IA/credentials/github-app')
PERMISSIONS={'contents':'write','pull_requests':'write','actions':'read',
             'checks':'read','statuses':'read','metadata':'read'}


def request(method,path,token,payload=None):
    # Fixed host, no redirects, no retry of token issuance, no logging of bodies.
    try:
        with httpx.Client(timeout=30,follow_redirects=False,trust_env=False) as client:
            result=client.request(method,'https://api.github.com/'+path,
                headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json',
                         'X-GitHub-Api-Version':'2022-11-28'},json=payload)
        if result.status_code not in {200,201} or len(result.content)>2_000_000:
            raise ValueError()
        data=result.json()
        if not isinstance(data,dict):raise ValueError()
        return data
    except Exception:
        raise RuntimeError('No se pudo verificar la identidad GitHub App') from None


def jwt(app_id,pem):
    if type(app_id) is not int or app_id<1:raise ValueError('App ID inválido')
    try:
        key=serialization.load_pem_private_key(pem,password=None)
        if not isinstance(key,rsa.RSAPrivateKey) or key.key_size<2048:raise ValueError()
    except Exception:
        raise ValueError('Se requiere una clave privada RSA válida') from None
    def b64(data):return base64.urlsafe_b64encode(data).rstrip(b'=')
    now=int(time.time())
    body=b'.'.join(b64(json.dumps(item,separators=(',',':')).encode()) for item in
                   ({'alg':'RS256','typ':'JWT'},{'iat':now-60,'exp':now+540,'iss':str(app_id)}))
    signature=key.sign(body,padding.PKCS1v15(),hashes.SHA256())
    return (body+b'.'+b64(signature)).decode()


def issue(app_id,pem,repository):
    repository=connection.repo_name(repository)
    auth=jwt(app_id,pem)
    app=request('GET','app',auth)
    slug=app.get('slug')
    if app.get('id')!=app_id or not isinstance(slug,str) or not re.fullmatch('[a-z0-9-]{1,100}',slug):
        raise ValueError('Identidad de App inesperada')
    if app.get('owner',{}).get('login')!=repository.split('/')[0]:
        raise ValueError('La App debe pertenecer al propietario del repositorio')
    install=request('GET','repos/'+repository+'/installation',auth)
    installation_id=install.get('id')
    if (type(installation_id) is not int or installation_id<1 or install.get('app_id')!=app_id
        or install.get('account',{}).get('login')!=repository.split('/')[0]
        or install.get('repository_selection')!='selected' or install.get('suspended_at') is not None
        or install.get('permissions')!=PERMISSIONS):
        raise ValueError('Instalación o permisos fuera de la política')
    result=request('POST',f'app/installations/{installation_id}/access_tokens',auth,
                   {'repositories':[repository.split('/')[1]],'permissions':PERMISSIONS})
    token=result.get('token')
    # GitHub tokens are opaque; their encoding and length may change.
    if (not isinstance(token,str) or not 20<=len(token)<=8192
        or any(not 33<=ord(char)<=126 for char in token)):
        raise ValueError('Token de instalación inválido')
    if result.get('permissions')!=PERMISSIONS:raise ValueError('Permisos de token inesperados')
    try:
        expiry=datetime.fromisoformat(result['expires_at'].replace('Z','+00:00'))
        remaining=(expiry-datetime.now(timezone.utc)).total_seconds()
        if not 60<remaining<=3660:raise ValueError()
    except (KeyError,AttributeError,ValueError,TypeError):
        raise ValueError('Vencimiento de token inválido') from None
    # Verify actual token scope, not just our requested scope.
    repos=request('GET','installation/repositories?per_page=100',token)
    if (repos.get('total_count')!=1 or len(repos.get('repositories',[]))!=1
        or repos['repositories'][0].get('full_name')!=repository):
        raise ValueError('Token fuera del repositorio autorizado')
    return token,{'app_id':app_id,'slug':slug,'installation_id':installation_id,
                  'repository':repository,'permissions':PERMISSIONS,'expires_at':result.get('expires_at')}


def provision(app_id,pem_path,repository):
    if CONFIG.exists():raise ValueError('La App ya está configurada; no se reemplaza implícitamente')
    repository=connection.repo_name(repository)
    if repository!=connection.configured_repo():raise ValueError('Repositorio fuera de alcance')
    source=Path(pem_path).resolve(strict=True)
    if source.is_relative_to(ROOT) or source.is_relative_to(Path('E:/IA/workspace')):
        raise ValueError('La clave debe estar fuera del proyecto y del workspace de agentes')
    if source.stat().st_size>16000:raise ValueError('Clave demasiado grande')
    pem=source.read_bytes()
    _,evidence=issue(app_id,pem,repository)
    encrypted=crypt(pem)
    SECRET_DIR.mkdir(parents=True,exist_ok=True)
    key_id=uuid.uuid4().hex
    with (SECRET_DIR/(key_id+'.dpapi')).open('xb') as file:
        file.write(encrypted);file.flush();os.fsync(file.fileno())
    # Config contains no secret. Publish only after all identity checks succeeded.
    cfg={'app_id':app_id,'slug':evidence['slug'],'installation_id':evidence['installation_id'],
         'repository':repository,'key_id':key_id}
    with CONFIG.open('x',encoding='utf-8') as file:
        json.dump(cfg,file,indent=2);file.flush();os.fsync(file.fileno())
    return {**evidence,'storage':'Windows DPAPI CurrentUser','self_test_required':True}


def token_for(repository):
    cfg=json.loads(CONFIG.read_text(encoding='utf-8'))
    if cfg.get('repository')!=repository or not re.fullmatch('[0-9a-f]{32}',cfg.get('key_id','')):
        raise ValueError('Configuración de App fuera de alcance')
    pem=crypt((SECRET_DIR/(cfg['key_id']+'.dpapi')).read_bytes(),decrypt=True)
    token,evidence=issue(cfg['app_id'],pem,repository)
    if any(evidence[k]!=cfg[k] for k in ('app_id','slug','installation_id','repository')):
        raise ValueError('Cambió la identidad publicadora')
    return token,evidence


def environment(repository):
    env={**os.environ,'GH_PROMPT_DISABLED':'1','GH_PAGER':'cat','GH_HOST':'github.com'}
    if CONFIG.exists():
        token,_=token_for(repository)
        for key in ('GH_TOKEN','GITHUB_TOKEN','GH_ENTERPRISE_TOKEN','GITHUB_ENTERPRISE_TOKEN'):
            env.pop(key,None)
        env['GH_TOKEN']=token
    return env


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    add=sub.add_parser('provision')
    add.add_argument('--app-id',type=int,required=True)
    add.add_argument('--pem',type=Path,required=True)
    sub.add_parser('check')
    args=parser.parse_args()
    try:
        repository=connection.configured_repo()
        result=provision(args.app_id,args.pem,repository) if args.command=='provision' else token_for(repository)[1]
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except Exception:
        parser.exit(1,'No se pudo configurar/verificar la App; revisar ID, archivo e instalación. No se habilitó fallback.\n')


if __name__=='__main__':main()
