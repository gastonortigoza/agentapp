import subprocess
import pytest
import git_control as g

def setup(tmp_path,monkeypatch):
    repo=tmp_path/'repo'; repo.mkdir()
    monkeypatch.setattr(g,'WORKSPACE',tmp_path)
    subprocess.run(['git','init',str(repo)],check=True,capture_output=True)
    return repo

def test_secret_in_staged_blob_is_blocked(tmp_path,monkeypatch):
    repo=setup(tmp_path,monkeypatch)
    (repo/'config.txt').write_text('token="'+'a'*32+'"')
    with pytest.raises(ValueError,match='secreto'): g.stage(repo,['config.txt'])

def test_denied_and_outside_paths(tmp_path,monkeypatch):
    repo=setup(tmp_path,monkeypatch)
    for name in ['.env','../outside.txt','credentials/file.txt']:
        with pytest.raises(ValueError): g.stage(repo,[name])

def test_changed_index_rejects_commit(tmp_path,monkeypatch):
    repo=setup(tmp_path,monkeypatch)
    p=repo/'code.py'; p.write_text('x=1')
    digest=g.stage(repo,['code.py'])
    p.write_text('x=2'); g.stage(repo,['code.py'])
    with pytest.raises(ValueError,match='cambió'): g.commit(repo,'test',digest)
