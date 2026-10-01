import base64
import json
import os
import subprocess
from datetime import datetime,timedelta,timezone

import pytest
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding,rsa
import github_app_auth as auth


@pytest.fixture(scope='module')
def key():
    private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    pem=private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())
    return private,pem


@pytest.fixture
def service(monkeypatch):
    calls=[]
    responses=[{'id':42,'slug':'pilot-publisher','owner':{'login':'owner'}},
               {'id':7,'app_id':42,'account':{'login':'owner'},'repository_selection':'selected',
                'suspended_at':None,'permissions':dict(auth.PERMISSIONS)},
               {'token':'ghs_'+'x'*40,'permissions':dict(auth.PERMISSIONS),
                'expires_at':(datetime.now(timezone.utc)+timedelta(minutes=59)).isoformat()},
               {'total_count':1,'repositories':[{'full_name':'owner/repo'}]}]
    def request(*args):
        calls.append(args)
        return responses[len(calls)-1]
    monkeypatch.setattr(auth,'request',request)
    return responses,calls


def test_jwt_signature_and_short_lifetime(key,monkeypatch):
    monkeypatch.setattr(auth.time,'time',lambda:2000000000)
    encoded=auth.jwt(42,key[1]);header,payload,signature=encoded.split('.')
    decode=lambda part:base64.urlsafe_b64decode(part+'='*(-len(part)%4))
    assert json.loads(decode(header))['alg']=='RS256'
    claims=json.loads(decode(payload))
    assert claims=={'iat':1999999940,'exp':2000000540,'iss':'42'}
    key[0].public_key().verify(decode(signature),(header+'.'+payload).encode(),padding.PKCS1v15(),hashes.SHA256())


def test_token_requested_for_one_repo_and_verified(key,service):
    responses,calls=service
    token,evidence=auth.issue(42,key[1],'owner/repo')
    assert token==responses[2]['token'] and 'token' not in evidence
    assert calls[2][3]=={'repositories':['repo'],'permissions':auth.PERMISSIONS}
    assert calls[3][1]=='installation/repositories?per_page=100'


def test_long_opaque_token_is_accepted(key,service):
    service[0][2]['token']='synthetic.'+'x'*380+'-end'
    token,_=auth.issue(42,key[1],'owner/repo')
    assert token==service[0][2]['token']


@pytest.mark.parametrize('token',['short','x'*30+'\n','x'*30+'\r','x'*30+' ','x'*8193])
def test_invalid_token_transport_is_rejected(key,service,token):
    service[0][2]['token']=token
    with pytest.raises(ValueError,match='Token'):auth.issue(42,key[1],'owner/repo')


@pytest.mark.parametrize('case',['wrong_app','wrong_owner','all_repos','suspended','extra_permissions','wrong_token_repo','extra_token_repo','token_permissions'])
def test_wrong_identity_or_scope_blocks(key,service,case):
    responses,calls=service
    if case=='wrong_app':responses[0]['id']=43
    elif case=='wrong_owner':responses[1]['account']['login']='other'
    elif case=='all_repos':responses[1]['repository_selection']='all'
    elif case=='suspended':responses[1]['suspended_at']='date'
    elif case=='extra_permissions':responses[1]['permissions']['administration']='write'
    elif case=='wrong_token_repo':responses[3]['repositories'][0]['full_name']='owner/other'
    elif case=='extra_token_repo':responses[3]['total_count']=2
    else:responses[2]['permissions']['checks']='write'
    with pytest.raises(ValueError):auth.issue(42,key[1],'owner/repo')
    if case in {'wrong_app','wrong_owner','all_repos','suspended','extra_permissions'}:
        assert all(call[0]=='GET' for call in calls)


def test_present_broken_config_never_falls_back(tmp_path,monkeypatch):
    cfg=tmp_path/'github-app.json';cfg.write_text('{}')
    monkeypatch.setattr(auth,'CONFIG',cfg)
    monkeypatch.setenv('GH_TOKEN','human-token')
    with pytest.raises(ValueError):auth.environment('owner/repo')


def test_token_only_in_child_environment(tmp_path,monkeypatch):
    cfg=tmp_path/'github-app.json';cfg.write_text('{}')
    monkeypatch.setattr(auth,'CONFIG',cfg)
    monkeypatch.setattr(auth,'token_for',lambda repo:('app-secret',{}))
    monkeypatch.setenv('GH_TOKEN','human-token')
    monkeypatch.setenv('GITHUB_TOKEN','alternate-human')
    child=auth.environment('owner/repo')
    assert child['GH_TOKEN']=='app-secret' and 'GITHUB_TOKEN' not in child
    assert os.environ['GH_TOKEN']=='human-token'


def test_failed_provision_stores_nothing(tmp_path,key,monkeypatch):
    cfg=tmp_path/'github-app.json';secrets=tmp_path/'secrets';pem=tmp_path/'key.pem';pem.write_bytes(key[1])
    monkeypatch.setattr(auth,'CONFIG',cfg);monkeypatch.setattr(auth,'SECRET_DIR',secrets)
    monkeypatch.setattr(auth.connection,'configured_repo',lambda:'owner/repo')
    def deny(*args):raise ValueError('bad installation')
    monkeypatch.setattr(auth,'issue',deny)
    with pytest.raises(ValueError):auth.provision(42,pem,'owner/repo')
    assert not cfg.exists() and not secrets.exists()


@pytest.mark.skipif(os.name!='nt',reason='Windows DPAPI')
def test_key_stored_encrypted_and_identity_rechecked(tmp_path,key,monkeypatch):
    cfg=tmp_path/'github-app.json';secrets=tmp_path/'secrets';pem=tmp_path/'key.pem';pem.write_bytes(key[1])
    monkeypatch.setattr(auth,'CONFIG',cfg);monkeypatch.setattr(auth,'SECRET_DIR',secrets)
    monkeypatch.setattr(auth.connection,'configured_repo',lambda:'owner/repo')
    evidence={'app_id':42,'slug':'pilot','installation_id':7,'repository':'owner/repo'}
    monkeypatch.setattr(auth,'issue',lambda *args:('secret-token',dict(evidence)))
    auth.provision(42,pem,'owner/repo')
    encrypted=next(secrets.iterdir()).read_bytes()
    assert b'PRIVATE KEY' not in encrypted and auth.crypt(encrypted,decrypt=True)==key[1]
    assert 'secret-token' not in cfg.read_text() and 'PRIVATE KEY' not in cfg.read_text()
    assert auth.token_for('owner/repo')[0]=='secret-token'
    evidence['installation_id']=8
    with pytest.raises(ValueError,match='identidad'):auth.token_for('owner/repo')


def test_http_failure_redacts_secret(monkeypatch):
    class Client:
        def __init__(self,**kwargs):assert kwargs['follow_redirects'] is False
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def request(self,*args,**kwargs):raise RuntimeError('secret-token leaked upstream')
    monkeypatch.setattr(auth.httpx,'Client',Client)
    with pytest.raises(RuntimeError) as error:auth.request('GET','app','secret-token')
    assert 'secret-token' not in str(error.value)


@pytest.mark.parametrize('expiry',['not-a-date','2000-01-01T00:00:00Z','2100-01-01T00:00:00Z'])
def test_bad_token_expiry_blocks(key,service,expiry):
    service[0][2]['expires_at']=expiry
    with pytest.raises(ValueError,match='Vencimiento'):auth.issue(42,key[1],'owner/repo')


def test_git_network_uses_app_token_without_command_line_secret(tmp_path,monkeypatch):
    import github_publish as publish
    cfg=tmp_path/'github-app.json';cfg.write_text('{}')
    monkeypatch.setattr(auth,'CONFIG',cfg)
    monkeypatch.setattr(auth.connection,'configured_repo',lambda:'owner/repo')
    monkeypatch.setattr(auth,'environment',lambda repo:{'GH_TOKEN':'app-token'})
    def run(cmd,**kwargs):
        assert kwargs['env']['GH_TOKEN']=='app-token' and 'app-token' not in ' '.join(cmd)
        return subprocess.CompletedProcess(cmd,1,'','contains app-token')
    monkeypatch.setattr(publish.subprocess,'run',run)
    with pytest.raises(RuntimeError) as error:publish.run_git(tmp_path,'push','origin','crewai/test')
    assert 'app-token' not in str(error.value)


def test_pr_adapter_uses_app_environment(monkeypatch):
    import github_pr as pr
    monkeypatch.setattr(pr.connection,'configured_repo',lambda:'owner/repo')
    monkeypatch.setattr(auth,'environment',lambda repo:{'GH_TOKEN':'app-token'})
    def run(cmd,**kwargs):
        assert kwargs['env']['GH_TOKEN']=='app-token' and 'app-token' not in cmd
        return subprocess.CompletedProcess(cmd,0,'{}','')
    monkeypatch.setattr(pr.subprocess,'run',run)
    pr.GitHubPR('owner/repo')._request('GET','pulls')
