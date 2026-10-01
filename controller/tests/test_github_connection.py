import base64
import json
import pytest
import github_connection as gh

def test_repo_name():
    assert gh.repo_name('https://github.com/owner/repo.git')=='owner/repo'
    for value in ['https://evil.example/owner/repo','owner/repo/issues','owner/..','--help','owner/repo?x=y']:
        with pytest.raises(ValueError):gh.repo_name(value)

def test_endpoint_escape_never_executes(monkeypatch):
    def forbidden(*a,**kw):pytest.fail('No debe ejecutarse un comando')
    monkeypatch.setattr(gh.subprocess,'run',forbidden)
    for endpoint in ['user','https://evil.example','repos/owner/repo/actions/secrets','repos/owner/repo;whoami']:
        with pytest.raises(ValueError):gh.api(endpoint)

def test_read_path_validation(monkeypatch):
    for path in ['../secret','/etc/passwd','a\\b','a//b']:
        with pytest.raises(ValueError):gh.read_file(path)

def test_read_file_pinned_to_configured_repo(tmp_path,monkeypatch):
    config=tmp_path/'github.json';config.write_text(json.dumps({'repository':'owner/repo','mode':'read_only'}))
    monkeypatch.setattr(gh,'CONFIG',config)
    endpoints=[]
    def api(endpoint):
        endpoints.append(endpoint)
        return {'type':'file','encoding':'base64','size':5,'content':base64.b64encode(b'hello').decode(),'sha':'abc'}
    monkeypatch.setattr(gh,'api',api)
    assert gh.read_file('docs/intro.md')['content']=='hello'
    assert endpoints==['repos/owner/repo/contents/docs/intro.md']
