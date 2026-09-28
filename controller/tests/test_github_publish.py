import json
import pytest
import github_publish as p

def test_branch_restrictions():
    assert p.branch_name('crewai/propuesta-1')=='crewai/propuesta-1'
    for branch in ['main','master','crewai/../main','--all','crewai/a:main']:
        with pytest.raises(ValueError):p.branch_name(branch)

def test_sensitive_files_denied(tmp_path,monkeypatch):
    monkeypatch.setattr(p,'ROOT',tmp_path)
    for name in ['.env','.git/config','secrets/key.txt','../outside.py']:
        with pytest.raises(ValueError):p.validated_files(tmp_path,[name])

def test_approval_bound_to_destination_and_commit():
    m={'repository':'a/b','branch':'crewai/test','commit':'abc'}
    original=p.digest(m)
    for key,value in [('repository','a/other'),('branch','crewai/other'),('commit','def')]:
        assert p.digest({**m,key:value})!=original

def test_no_push_without_exact_approval(tmp_path,monkeypatch):
    monkeypatch.setattr(p,'enabled',lambda:'a/b');monkeypatch.setattr(p,'JOBS',tmp_path)
    job='a'*32;(tmp_path/job).mkdir();(tmp_path/job/'manifest.json').write_text(json.dumps({'repository':'a/b'}))
    monkeypatch.setattr(p,'run_git',lambda *a:pytest.fail('No debe llamar a Git'))
    with pytest.raises(ValueError,match='aprobar'):p.publish(job,'wrong')

def test_publish_to_local_bare_repository(tmp_path,monkeypatch):
    import subprocess
    import git_control
    remote=tmp_path/'remote.git'
    subprocess.run(['git','init','--bare',str(remote)],check=True,capture_output=True)
    source=tmp_path/'source';source.mkdir();(source/'hello.py').write_text('print("hello")\n')
    monkeypatch.setattr(p,'ROOT',source);monkeypatch.setattr(p,'JOBS',tmp_path/'jobs')
    monkeypatch.setattr(git_control,'WORKSPACE',tmp_path)
    monkeypatch.setattr(p,'enabled',lambda:'owner/repo')
    monkeypatch.setattr(p.connection,'api',lambda endpoint:{'default_branch':'main'})
    monkeypatch.setattr(p.controller_gate,'require_green',lambda:None)
    actual=p.run_git
    def local(path,*args):
        return actual(path,*[str(remote) if x=='https://github.com/owner/repo.git' else x for x in args])
    monkeypatch.setattr(p,'run_git',local)
    proposal=p.prepare(source,['hello.py'],'crewai/test')
    assert proposal['manifest']['new_repository']
    result=p.publish(proposal['job_id'],proposal['review_digest'])
    assert result['commit']==proposal['manifest']['commit']
    assert p.publish(proposal['job_id'],proposal['review_digest'])==result
    assert p.publication_status(proposal['job_id'])['attempts']==1
